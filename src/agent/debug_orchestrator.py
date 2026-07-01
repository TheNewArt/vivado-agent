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
    Phase 3: Multi-agent debug loop with intelligent triage decisions.
    - Classifies errors by severity/type
    - Prioritizes fix order (blocking first)
    - Decides when to stop (fixed, stuck, or regressed)
    """

    ERROR_PRIORITY = {
        "elaboration_failure": 0,
        "unresolved_reference": 0,
        "combinational_loop": 0,
        "multiple_driver": 1,
        "port_mismatch": 1,
        "simulation_failure": 1,
        "latch_inference": 2,
        "inferred_latch": 2,
        "CDC_violation": 2,
        "timing_setup": 3,
        "timing_hold": 3,
        "x_propagation": 1,
    }

    def __init__(self, config: Config):
        self.config = config
        self.log_agent = LogParserAgent(config.get("project.rtl_dir"))
        self.wave_agent = WaveformAnalysisAgent(config.vivado_path)
        self.fix_agent = AutoFixAgent(config.build_llm_config())
        self.engine = TCLEngine(config.vivado_path)

        # Internal state for decision-making
        self._error_history: list[dict] = []
        self._consecutive_no_improvement = 0

    def run_debug_cycle(
        self,
        log_path: str | Path,
        wdb_path: str | Path,
        top_module: str,
        clock_period_ns: float = 10.0,
        max_iterations: int = 10,
        apply_fixes: bool = True,
    ) -> dict:
        log_path = Path(log_path)
        wdb_path = Path(wdb_path)

        result = {
            "iterations": [],
            "fixed": False,
            "aborted": False,
            "abort_reason": "",
            "error_count_by_type": {},
            "fixes_applied": 0,
        }

        rtl_dir = Path(self.config.get("project.rtl_dir", "./src/hdl"))

        for iteration in range(max_iterations):
            logger.info(f"=== Debug iteration {iteration + 1}/{max_iterations} ===")
            iter_data = {"iteration": iteration + 1, "decisions": []}

            # 1) Parse log
            log_analysis = self.log_agent.analyze(log_path)
            errors = log_analysis.get("errors", [])
            iter_data["log_analysis"] = log_analysis

            # ── Decision: are we done? ──
            if not log_analysis.get("has_errors", False):
                result["fixed"] = True
                logger.info("No errors — debug complete")
                break

            # 2) Classify and prioritize errors
            classified = self._classify_errors(errors)
            iter_data["classified_errors"] = classified
            result["error_count_by_type"] = self._count_by_type(errors)
            iter_data["decisions"].append(f"{len(errors)} errors, top priority: {classified['top_priority']}")

            # ── Decision: regressed? ──
            if self._detect_regression(errors):
                self._consecutive_no_improvement += 1
                iter_data["decisions"].append("WARNING: error count increased — rolling back")
                if self._consecutive_no_improvement >= 2:
                    result["aborted"] = True
                    result["abort_reason"] = "Error count increased 2x in a row — giving up"
                    break
            else:
                self._consecutive_no_improvement = 0

            # 3) Pick the most critical error
            target_error = classified["sorted"][0] if classified["sorted"] else None
            if not target_error:
                break

            iter_data["target_error"] = target_error
            iter_data["decisions"].append(
                f"targeting: {target_error.get('category', '?')} "
                f"(L{target_error.get('line_no', '?')}, priority={classified.get('top_priority', '?')})"
            )

            # 4) Extract waveform around error
            error_timestamps = self._extract_timestamps(log_analysis, target_error)
            wave_results = []
            for ts in error_timestamps[:1]:  # Only the most relevant timestamp
                wave_res = self.wave_agent.run_extraction(wdb_path, top_module, ts)
                wave_results.append(wave_res)

            iter_data["waveform_analyses"] = wave_results

            # 5) Build context for LLM
            error_context = self._build_error_context(log_analysis, target_error)
            snapshot_data = self._build_snapshot_text(wave_results)
            rtl_path = self._find_source_file(target_error, rtl_dir)

            # 6) Ask LLM for fix
            if rtl_path and error_context:
                fix = self.fix_agent.propose_fix(
                    rtl_path=rtl_path,
                    error_context=error_context,
                    snapshot_data=snapshot_data,
                    spec="",
                )
                iter_data["proposed_fix"] = fix
                result["fixes_applied"] += 1

                # 7) Apply and re-run
                if apply_fixes and self.fix_agent.has_valid_diff(fix):
                    applied = self.fix_agent.apply_patch(rtl_path, fix)
                    iter_data["patch_applied"] = applied
                    iter_data["decisions"].append(
                        f"patch applied: {applied}" +
                        (" (syntax check passed)" if applied else " (syntax check failed)")
                    )

                    if applied:
                        sim_result = self._rerun_simulation()
                        iter_data["rerun_result"] = sim_result
                        new_errors = self._count_sim_errors(sim_result)

                        # ── Decision: did the fix help? ──
                        old_count = len(errors)
                        if new_errors < old_count:
                            iter_data["decisions"].append(f"FIX HELD: errors {old_count} -> {new_errors}")
                        elif new_errors == old_count:
                            iter_data["decisions"].append(f"NEUTRAL: still {new_errors} errors")
                        else:
                            iter_data["decisions"].append(f"REGRESSED: errors {old_count} -> {new_errors}")
            else:
                iter_data["decisions"].append("no RTL file found for error — skipping")

            self._error_history.append(classified)
            result["iterations"].append(iter_data)

        return result

    # ── Decision helpers ──────────────────────────────────────────────────────

    def _classify_errors(self, errors: list) -> dict:
        """Classify and prioritize errors by severity and type."""
        sorted_errors = sorted(
            errors,
            key=lambda e: self.ERROR_PRIORITY.get(
                e.get("category", e.category if hasattr(e, "category") else ""), 99
            ),
        )
        top = sorted_errors[0] if sorted_errors else {}
        top_cat = top.get("category", getattr(top, "category", "")) if isinstance(top, dict) else getattr(top, "category", "")
        return {
            "sorted": sorted_errors,
            "top_priority": self.ERROR_PRIORITY.get(top_cat, 99),
        }

    def _detect_regression(self, current_errors: list) -> bool:
        """Decision: did error count increase since last iteration?"""
        if not self._error_history:
            return False
        prev_count = len(self._error_history[-1].get("sorted", []))
        return len(current_errors) > prev_count

    @staticmethod
    def _count_by_type(errors: list) -> dict:
        counts = {}
        for e in errors:
            cat = e.get("category", getattr(e, "category", "unknown")) if isinstance(e, dict) else getattr(e, "category", "unknown")
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    @staticmethod
    def _extract_timestamps(log_analysis: dict, target_error: dict) -> list[float]:
        ts = target_error.get("timestamp_ns", 0) if isinstance(target_error, dict) else 0
        return [ts] if ts > 0 else [0.0]

    @staticmethod
    def _build_error_context(log_analysis: dict, target_error: dict) -> str:
        if isinstance(target_error, dict):
            return f"[{target_error.get('severity', '?')}] {target_error.get('category', '?')}: {target_error.get('message', '')[:200]}"
        return log_analysis.get("summary", "")

    @staticmethod
    def _build_snapshot_text(wave_results: list) -> str:
        parts = []
        for wr in wave_results:
            chain = wr.get("fault_chain", [])
            for c in chain[:10]:
                parts.append(f"  {c.get('signal', c.get('name', '?'))}: {c.get('first_val', '?')} -> {c.get('last_val', '?')}")
        return "\n".join(parts)

    @staticmethod
    def _find_source_file(target_error: dict, rtl_dir: Path) -> Path | None:
        sf = target_error.get("source_file") if isinstance(target_error, dict) else None
        if sf:
            return Path(sf)
        return None

    @staticmethod
    def _count_sim_errors(sim_result: dict) -> int:
        from src.core.tcl_engine import TCLEngine
        stdout = sim_result.get("stdout", "") + sim_result.get("stderr", "")
        return len(TCLEngine.extract_errors(stdout))

    def _rerun_simulation(self) -> dict:
        sim_tcl = [
            "open_project [glob *.xpr][0]",
            "launch_simulation",
            'puts "SIMULATION DONE"',
        ]
        return self.engine.run_script("\n".join(sim_tcl))