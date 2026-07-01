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
        self.vivado_path = config.vivado_path
        self.fix_agent = AutoFixAgent(config.build_llm_config())
        self.engine = TCLEngine(config.vivado_path)

        # Internal state for decision-making
        self._error_history: list[dict] = []
        self._consecutive_no_improvement = 0
        self._last_error_count = 0
        self._stall_threshold = 3  # max iterations without any change
        self._last_fix_text = ""  # detect identical LLM responses
        self._baseline_error_count = 0  # static scanner baseline

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

            # 1) Parse log + waveform for errors
            log_analysis = self.log_agent.analyze(log_path)
            log_errors = log_analysis.get("errors", [])

            # 1b) Also check WDB for X/Z propagation (functional bugs)
            wave_errors = []
            if wdb_path.exists():
                try:
                    wave_res = self.wave_agent.run_extraction(
                        wdb_path, top_module, error_time_ns=0
                    )
                    xz_signals = wave_res.get("xz_signals", [])
                    if xz_signals:
                        wave_errors.append({
                            "category": "x_propagation",
                            "severity": "error",
                            "message": f"X/Z propagation on {len(xz_signals)} signals: {', '.join(list(xz_signals)[:5])}",
                            "line_no": 0,
                            "source_file": "",
                            "timestamp_ns": 0,
                        })
                    iter_data["waveform_xz"] = xz_signals
                except Exception as e:
                    logger.warning(f"Waveform analysis failed: {e}")

            # 1c) If xsim failed (no X/Z detected), use static scanner on RTL
            if not wave_errors and rtl_dir.exists():
                try:
                    from src.tools.static_scanner import StaticScanner
                    scanner = StaticScanner()
                    rtl_issues = scanner.scan_rtl(rtl_dir)
                    if iteration == 0:
                        self._baseline_error_count = len([i for i in rtl_issues if i.severity == "error"])
                    for issue in rtl_issues:
                        if issue.severity == "error":
                            wave_errors.append({
                                "category": issue.category,
                                "severity": "error",
                                "message": issue.description,
                                "line_no": issue.line,
                                "source_file": "",
                                "timestamp_ns": 0,
                            })
                    if rtl_issues:
                        logger.info(f"Static scan: {len(rtl_issues)} RTL issues found")
                except Exception as e:
                    logger.warning(f"RTL scan failed: {e}")

            errors = log_errors + wave_errors
            iter_data["log_analysis"] = log_analysis
            iter_data["wave_errors"] = wave_errors
            iter_data["total_errors"] = len(errors)

            # ── Decision: are we done? ──
            if not errors:
                result["fixed"] = True
                logger.info("No errors (log+waveform) — debug complete")
                break

            logger.info(f"Found {len(log_errors)} log errors + {len(wave_errors)} waveform errors")

            # ── Decision: stalled? ──
            if len(errors) == self._last_error_count > 0 and iteration >= self._stall_threshold:
                logger.warning(f"Error count unchanged for {iteration} iterations — aborting")
                result["aborted"] = True
                result["abort_reason"] = f"Stalled: {len(errors)} errors unchanged for {self._stall_threshold}+ iterations"
                iter_data["decisions"].append("ABORT: no progress")
                break
            self._last_error_count = len(errors)

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
            if error_context:
                if not rtl_path:
                    rtl_path = self._find_source_file(target_error, rtl_dir)
                if not rtl_path and rtl_dir.exists():
                    files = list(rtl_dir.rglob("*.v")) + list(rtl_dir.rglob("*.sv"))
                    # Prefer "buggy" file if errors are from static scanner
                    buggy_files = [f for f in files if "buggy" in f.name.lower()]
                    files = buggy_files or files
                    if files:
                        rtl_path = files[0]

                if rtl_path and rtl_path.exists():
                    # Build comprehensive context with ALL errors and line numbers
                    all_errors_text = error_context
                    if "wave_errors" in iter_data:
                        for we in iter_data.get("wave_errors", []):
                            if isinstance(we, dict):
                                cat = we.get("category", "?")
                                msg = we.get("message", "")[:120]
                                ln = we.get("line_no", "?")
                                all_errors_text += f"\n  [{cat}] L{ln}: {msg}"

                    # Read source code and annotate error lines
                    rtl_code = rtl_path.read_text(encoding="utf-8", errors="replace")
                    code_lines = rtl_code.splitlines()
                    annotated = []
                    for i, line in enumerate(code_lines, 1):
                        marker = "  ← ERROR" if any(
                            e.get("line_no") == i for e in errors if isinstance(e, dict)
                        ) else ""
                        annotated.append(f"L{i:4d}: {line}{marker}")
                    annotated_code = "\n".join(annotated)

                    fix = self.fix_agent.propose_fix(
                        rtl_path=rtl_path,
                        error_context=all_errors_text,
                        snapshot_data=snapshot_data,
                        spec=f"## Code with error markers\n```verilog\n{annotated_code}\n```",
                    )
                    iter_data["proposed_fix"] = fix
                    result["fixes_applied"] += 1

                    # ── Decision: duplicate fix? ──
                    fix_sig = fix.strip().replace(" ", "")[:200]
                    if fix_sig == self._last_fix_text and iteration >= 2:
                        iter_data["decisions"].append("DUPLICATE FIX — LLM produced same output, aborting")
                        result["aborted"] = True
                        result["abort_reason"] = "LLM returning identical fix each iteration"
                        break
                    self._last_fix_text = fix_sig

                    # Detect API failure and abort
                    if fix.startswith("# LLM"):
                        iter_data["decisions"].append("LLM API failed — aborting debug cycle")
                        result["aborted"] = True
                        result["abort_reason"] = fix[:100]
                        break

                    # 7) Apply and re-run
                    if apply_fixes and self.fix_agent.has_valid_diff(fix):
                        applied = self.fix_agent.apply_patch(rtl_path, fix)
                        iter_data["patch_applied"] = applied
                        iter_data["decisions"].append(
                            f"patch applied: {applied}" +
                            (" (syntax check passed)" if applied else " (syntax check failed)")
                        )

                        if applied:
                            # Re-run static scanner to check if fix worked
                            from src.tools.static_scanner import StaticScanner
                            re_scan = StaticScanner().scan_rtl(rtl_dir)
                            new_error_count = len([i for i in re_scan if i.severity == "error"])
                            old_error_count = self._baseline_error_count or len(errors)

                            # ── Decision: did the fix help? ──
                            if new_error_count < old_error_count:
                                iter_data["decisions"].append(f"FIX HELD: errors {old_error_count} -> {new_error_count}")
                            elif new_error_count == old_error_count:
                                iter_data["decisions"].append(f"NEUTRAL: still {new_error_count} errors")
                            else:
                                iter_data["decisions"].append(f"REGRESSED: errors {old_error_count} -> {new_error_count}")
                else:
                    iter_data["decisions"].append("no RTL file found for error — cannot fix")
                    # If we can't fix, terminate to avoid infinite loop
                    if iteration >= 1:
                        result["aborted"] = True
                        result["abort_reason"] = "Cannot find RTL source file to fix"
                        break
            else:
                iter_data["decisions"].append("no error context — skipping")

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
        if isinstance(target_error, dict):
            ts = target_error.get("timestamp_ns", 0)
            if ts > 0:
                return [ts]
        return [0.0]

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
        # Fallback: search for the RTL file matching the top module name
        cat = target_error.get("category", "") if isinstance(target_error, dict) else ""
        if rtl_dir and rtl_dir.exists():
            # Try to find file containing the buggy module
            for ext in ("*.v", "*.sv"):
                for f in rtl_dir.rglob(ext):
                    if "buggy" in f.name.lower() or "bug" in f.name.lower():
                        return f
            # If no buggy file, try matching error category
            for ext in ("*.v", "*.sv"):
                for f in rtl_dir.rglob(ext):
                    if "glbl" in f.name or "xsim" in str(f):
                        continue
                    return f
        return None

    @staticmethod
    def _count_sim_errors(sim_result: dict) -> int:
        from src.core.tcl_engine import TCLEngine
        stdout = sim_result.get("stdout", "") + sim_result.get("stderr", "")
        return len(TCLEngine.extract_errors(stdout))

    def _rerun_simulation(self, rtl_dir: Path | None = None) -> dict:
        from pathlib import Path
        search_dir = rtl_dir or Path.cwd()
        xprs = list(search_dir.rglob("*.xpr"))
        # Prefer buggy_prj over counter_prj
        xprs.sort(key=lambda p: 0 if "buggy" in str(p).lower() else 1)
        if not xprs:
            return {"stdout": "", "stderr": "No project found", "returncode": -1, "elapsed": 0}
        xpr_path = str(xprs[0].resolve()).replace("\\", "/")
        sim_tcl = f"""
open_project {{{xpr_path}}}
launch_simulation
puts "SIMULATION DONE"
"""
        return self.engine.run_script(sim_tcl)