import re
import time
from pathlib import Path
from src.utils.logger import setup_logger
from src.core.config import Config
from src.agent.log_parser_agent import LogParserAgent
from src.agent.waveform_agent import WaveformAnalysisAgent
from src.agent.auto_fix_agent import AutoFixAgent
from src.core.tcl_engine import TCLEngine

logger = setup_logger("debug_orchestrator")


class DebugOrchestrator:
    """
    Phase 3: End-to-end multi-agent debug loop.
    1. Parse log for errors
    2. Extract waveform snapshots around error timestamps
    3. LLM generates minimal RTL patch
    4. Apply patch and re-run simulation
    5. Verify fix or iterate
    """

    def __init__(self, config: Config):
        self.config = config
        self.log_agent = LogParserAgent(config.get("project.rtl_dir"))
        self.wave_agent = WaveformAnalysisAgent(config.vivado_path)
        self.fix_agent = AutoFixAgent(config.build_llm_config())
        self.engine = TCLEngine(config.vivado_path)

    def run_debug_cycle(
        self,
        log_path: str | Path,
        wdb_path: str | Path,
        top_module: str,
        clock_period_ns: float = 10.0,
        max_iterations: int = 5,
        apply_fixes: bool = True,
    ) -> dict:
        log_path = Path(log_path)
        wdb_path = Path(wdb_path)

        result = {
            "iterations": [],
            "fixed": False,
            "total_errors_before": 0,
            "total_errors_after": 0,
        }

        rtl_dir = Path(self.config.get("project.rtl_dir", "./src/hdl"))
        tb_dir = Path(self.config.get("project.tb_dir", "./src/tb"))

        for iteration in range(max_iterations):
            logger.info(f"=== Debug iteration {iteration + 1}/{max_iterations} ===")
            iter_data = {"iteration": iteration + 1}

            # Step 1: Parse log
            log_analysis = self.log_agent.analyze(log_path)
            iter_data["log_analysis"] = log_analysis
            if iteration == 0:
                result["total_errors_before"] = len(log_analysis.get("errors", []))

            if not log_analysis.get("has_errors", False):
                result["fixed"] = True
                logger.info("No errors — debug complete")
                break

            # Step 2: Extract error timestamps and analyze waveform
            error_timestamps = self._extract_timestamps(log_analysis)
            wave_results = []

            for ts in error_timestamps[:3]:  # Max 3 timestamps per iteration
                wave_res = self.wave_agent.run_extraction(
                    wdb_path, top_module, ts
                )
                wave_results.append(wave_res)

            iter_data["waveform_analyses"] = wave_results

            # Step 3: Build LLM context
            error_context = log_analysis.get("summary", "")
            snapshot_data = self._build_snapshot_text(wave_results)
            rtl_path = self._find_source_file(log_analysis)

            # Step 4: Propose fix
            if rtl_path and error_context:
                fix = self.fix_agent.propose_fix(
                    rtl_path=rtl_path,
                    error_context=error_context,
                    snapshot_data=snapshot_data,
                )
                iter_data["proposed_fix"] = fix
                iter_data["rtl_path"] = str(rtl_path)

                # Step 5: Apply fix and re-run
                if apply_fixes and "```diff" in fix:
                    applied = self._apply_patch(rtl_path, fix)
                    iter_data["patch_applied"] = applied
                    if applied:
                        logger.info(f"Patch applied to {rtl_path}, re-running simulation")
                        sim_result = self._rerun_simulation()
                        iter_data["rerun_result"] = sim_result
                        if sim_result.get("returncode") == 0:
                            errors_after = len(self.engine.extract_errors(
                                sim_result.get("stdout", "") + sim_result.get("stderr", "")
                            ))
                            result["total_errors_after"] = errors_after
                            if errors_after == 0:
                                result["fixed"] = True
                                logger.info("Fix verified — no errors after re-run")
                                break
            else:
                iter_data["proposed_fix"] = "# No RTL file to fix"
                logger.warning("No source file found for the error")

            result["iterations"].append(iter_data)

        return result

    @staticmethod
    def _extract_timestamps(log_analysis: dict) -> list[float]:
        timestamps = []
        for err in log_analysis.get("errors", []):
            ts = getattr(err, "timestamp_ns", None)
            if isinstance(err, dict):
                ts = err.get("timestamp_ns")
            if ts and ts > 0:
                timestamps.append(ts)
        return timestamps or [0.0]

    @staticmethod
    def _build_snapshot_text(wave_results: list) -> str:
        parts = []
        for wr in wave_results:
            chain = wr.get("fault_chain", [])
            snapshots = wr.get("snapshots", [])
            if chain:
                parts.append("Fault chain:")
                for c in chain[:10]:
                    parts.append(f"  {c['signal']}: {c.get('first_val','?')} -> {c.get('last_val','?')}")
            if snapshots:
                parts.append(f"Signal snapshots ({len(snapshots)} total):")
                for s in snapshots[:20]:
                    parts.append(f"  @{s.time_ns:.0f}ns {s.name} = {s.value}")
        return "\n".join(parts)

    @staticmethod
    def _find_source_file(log_analysis: dict) -> Path | None:
        """Extract the most likely RTL file path from error context."""
        for err in log_analysis.get("errors", []):
            if isinstance(err, dict):
                src = err.get("source_file") or err.get("source_block", "")
                if src:
                    return Path(src)
            line_no = getattr(err, "line_no", None) or (err.get("line_no") if isinstance(err, dict) else None)
            if line_no:
                # Try to find any .v/.sv file mentioning the line
                pass
        return None

    def _apply_patch(self, rtl_path: Path, diff_text: str) -> bool:
        """Apply a unified diff patch to a file."""
        try:
            # Extract the diff content between ```diff ... ```
            m = re.search(r'```diff\n(.*?)```', diff_text, re.DOTALL)
            if not m:
                # Try simple ---/+++ format
                m = re.search(r'---.*?\n\+\+\+.*?\n(@@.*@@\n)?(.+?)(?=\n```|$)', diff_text, re.DOTALL)
                if not m:
                    logger.warning("No valid diff found in LLM response")
                    return False

            diff_content = m.group(0) if not m else m.group(0)

            # Simple application: extract lines with +/- prefix
            original = rtl_path.read_text(encoding="utf-8", errors="replace").splitlines()
            new_lines = list(original)

            # Parse hunk headers
            hunks = re.finditer(
                r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*?)(?=@@|\Z)',
                diff_text, re.DOTALL
            )
            applied = False
            for h in hunks:
                old_start = int(h.group(1))
                hunk_body = h.group(5).strip().splitlines()
                # Apply removals
                removal_lines = [i for i, l in enumerate(hunk_body) if l.startswith('-')]
                if removal_lines:
                    # Remove lines in reverse order
                    for i in reversed(removal_lines):
                        idx = old_start - 1 + i
                        if idx < len(new_lines):
                            new_lines.pop(idx)
                            applied = True

                # Apply additions
                addition_lines = [(i, l[1:]) for i, l in enumerate(hunk_body) if l.startswith('+')]
                offset = 0
                for i, (hunk_idx, new_line) in enumerate(addition_lines):
                    insert_pos = old_start - 1 + hunk_idx + offset
                    new_lines.insert(insert_pos, new_line)
                    offset += 1
                    applied = True

            if applied:
                rtl_path.write_text("\n".join(new_lines) + "\n")
                logger.info(f"Patch applied to {rtl_path}")
                return True
            else:
                logger.warning("Could not parse hunk offsets; trying full-replace")
                return False

        except Exception as e:
            logger.error(f"Patch application failed: {e}")
            return False

    def _rerun_simulation(self) -> dict:
        """Re-run Vivado simulation after fix."""
        sim_tcl = [
            "open_project [glob *.xpr][0]",
            "launch_simulation -step simulate",
            "wait_on_run sim_1",
            "puts \"SIMULATION DONE\"",
        ]
        return self.engine.run_script("\n".join(sim_tcl))