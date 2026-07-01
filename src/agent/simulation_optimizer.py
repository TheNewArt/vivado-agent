import time
from pathlib import Path
from src.utils.logger import setup_logger
from src.core.config import Config
from src.tools.incremental_compile import IncrementalCompileManager
from src.tools.multithread_tuner import MultithreadTuner
from src.tools.waveform_trimmer import WaveformTrimmer
from src.tools.static_scanner import StaticScanner
from src.tools.runtime_monitor import SimulationMonitor
from src.tools.module_parser import ModuleParser
from src.tools.project_detector import ProjectDetector, ProjectFiles

logger = setup_logger("simulation_optimizer")


class SimulationOptimizerAgent:
    """
    Agent with adaptive optimization decisions.
    Decides what optimizations to apply based on project profile and history.
    """

    def __init__(self, config: Config):
        self.config = config
        self.inc_compile = IncrementalCompileManager(config.xsim_cache)
        self.tuner = MultithreadTuner(config.max_threads, config.get("vivado.version", "2020.2"))
        self.trim = WaveformTrimmer()
        self.scanner = StaticScanner()
        self.monitor = SimulationMonitor()
        self.parser = ModuleParser()
        self.detector = ProjectDetector()

        # Decision history for adaptive tuning
        self._decision_history: list[dict] = []

    def auto_detect(self, project_dir: str | Path | None = None) -> ProjectFiles:
        project_dir = project_dir or Path.cwd()
        pf = self.detector.detect(project_dir)
        rtl_dir = self.config.get("project.rtl_dir", "./src/hdl")
        if not pf.rtl_files:
            pf = self.detector._from_scan(Path(rtl_dir))
        return pf

    # ── Decision: should we enable incremental compile? ──────────────────────
    def _decide_incremental(self, changed: list[str], cached: list[str], total: list[str]) -> dict:
        """Decision: incremental compile is only beneficial if change set is small."""
        if not total:
            return {"enable": False, "reason": "no modules"}

        change_ratio = len(changed) / len(total)

        if change_ratio == 0:
            return {"enable": True, "reason": "all cached, no change needed"}
        elif change_ratio < 0.3:
            return {"enable": True, "reason": f"only {len(changed)}/{len(total)} modules changed, cache reuse beneficial"}
        elif change_ratio < 0.6:
            return {"enable": True, "reason": f"moderate change ({len(changed)}/{len(total)}), partial cache may help"}
        else:
            return {"enable": False, "reason": f"massive change ({len(changed)}/{len(total)}), full rebuild likely faster"}

    # ── Decision: waveform verbosity ──────────────────────────────────────────
    def _decide_waveform_verbosity(self, has_scan_issues: bool, history: list[dict]) -> dict:
        """Decision: reduce waveform in regression mode, expand in debug mode."""
        recent_errors = any(h.get("had_errors") for h in history[-3:]) if history else False

        if has_scan_issues or recent_errors:
            return {
                "log_all": False,  # still false in batch, but keep more scope
                "storage": "compact",
                "reason": "issues detected, keep compact but don't drop signals",
            }
        # Clean run history → regression mode, minimize overhead
        if len(history) >= 2 and not recent_errors:
            return {
                "log_all": False,
                "storage": "compact",
                "reason": "regression mode — minimal waveform overhead",
            }
        return {
            "log_all": False,
            "storage": "compact",
            "reason": "default conservative mode",
        }

    # ── Decision: thread count ────────────────────────────────────────────────
    def _decide_threads(self, rtl_lines: int, module_count: int) -> int:
        """Decision: don't blindly use max threads. Small designs lose to overhead."""
        if rtl_lines < 1000 and module_count < 5:
            return 2  # small design: 2 threads is enough
        elif rtl_lines < 50000:
            return min(4, self.tuner._detect_cpu_count())
        else:
            return min(self.tuner._detect_cpu_count(), 8)

    # ── Decision: abort on critical issues ────────────────────────────────────
    def _decide_abort(self, scan_issues: list, predicted_time: dict) -> dict:
        """Decision: should we abort before running simulation?"""
        critical = [i for i in scan_issues if i.severity == "error"]
        if critical:
            return {
                "abort": True,
                "reason": f"{len(critical)} critical static issues found (e.g. infinite loop, handshake deadlock)",
                "issues": critical,
            }
        return {"abort": False, "reason": "static scan clean"}

    # ── Main optimization entry point with decisions ──────────────────────────
    def optimize_simulation(
        self,
        top_module: str | None = None,
        rtl_dir: str | Path | None = None,
        tb_path: str | Path | None = None,
        project_files: ProjectFiles | None = None,
    ) -> dict:
        start_time = time.time()

        plan = {
            "decisions": [],
            "incremental": {},
            "waveform": {},
            "threads": {},
            "abort": {"abort": False},
            "override_tcl": [],
            "project": {},
            "scan_issues": [],
            "time_prediction": {},
            "stats": {},
        }

        # 1) Detect project
        if not project_files:
            project_files = self.auto_detect()
        if not rtl_dir:
            rtl_dir = Path(self.config.get("project.rtl_dir", "./src/hdl"))
        if not tb_path and project_files.tb_files:
            tb_path = project_files.tb_files[0]
        rtl_dir = Path(rtl_dir)

        plan["project"] = {
            "name": project_files.project_name,
            "rtl_count": len(project_files.rtl_files),
            "tb_count": len(project_files.tb_files),
            "top_module": top_module or project_files.top_module or "top",
            "source": project_files.source,
        }

        # 2) Parse modules
        module_to_files = {}
        for f in (project_files.rtl_files or list(rtl_dir.rglob("*.v")) + list(rtl_dir.rglob("*.sv"))):
            mods = self.parser.parse_file(f)
            for mod_name, info in mods.items():
                module_to_files.setdefault(mod_name, []).append(f)

        # 3) Incremental check
        changed_mods, cached_mods, is_first = self.inc_compile.get_changed_modules(module_to_files)
        incr_decision = self._decide_incremental(changed_mods, cached_mods, list(module_to_files.keys()))
        plan["incremental"] = {
            "changed": changed_mods,
            "cached": cached_mods,
            "all": list(module_to_files.keys()),
            "decision": incr_decision,
        }
        plan["decisions"].append(f"incremental: {incr_decision['reason']}")

        if incr_decision["enable"] and changed_mods:
            plan["override_tcl"].append(self.inc_compile.get_compile_deps_tcl(["sim_1"], enable_incr=True))

        # 4) Time prediction
        prediction = self.inc_compile.predict_compile_time(changed_mods)
        plan["time_prediction"] = prediction

        # 5) Thread decision
        total_lines = sum(
            len(f.read_text(encoding="utf-8", errors="replace").splitlines())
            for f in project_files.rtl_files if f.is_file()
        ) if project_files.rtl_files else 0
        thread_count = self._decide_threads(total_lines, len(module_to_files))
        plan["threads"] = {"count": thread_count, "reason": f"{total_lines} lines, {len(module_to_files)} modules"}
        plan["decisions"].append(f"threads: {thread_count} ({plan['threads']['reason']})")
        plan["override_tcl"].append(f"# Performance tuning: {thread_count} threads")
        plan["override_tcl"].append(f"set_param general.maxThreads {thread_count}")

        # 6) Waveform decision
        has_scan = False
        if tb_path and Path(tb_path).exists():
            scan_issues = self.scanner.scan_testbench(tb_path)
            plan["scan_issues"] = scan_issues
            has_scan = len(scan_issues) > 0

        wave_decision = self._decide_waveform_verbosity(has_scan, self._decision_history)
        plan["waveform"] = wave_decision
        plan["decisions"].append(f"waveform: {wave_decision['reason']}")
        waveform_tcl = self.trim.generate_log_wave_tcl(
            top_module=top_module or project_files.top_module or "top",
            rtl_dir=rtl_dir,
            tb_path=tb_path,
            has_error=has_scan,
        )
        plan["override_tcl"].append(waveform_tcl)

        # 7) Abort decision
        if has_scan:
            abort_decision = self._decide_abort(scan_issues, prediction)
            plan["abort"] = abort_decision
            if abort_decision["abort"]:
                plan["decisions"].append(f"ABORT: {abort_decision['reason']}")

        # 8) Stats
        elapsed = time.time() - start_time
        plan["stats"] = {
            "analysis_time_s": round(elapsed, 2),
            "total_rtl_lines": total_lines,
            "module_count": len(module_to_files),
            "threads_selected": thread_count,
        }

        return plan

    # ── Feedback loop: record results for future decisions ────────────────────
    def record_result(self, plan: dict, sim_result: dict):
        """Record simulation result so decisions adapt over time."""
        had_errors = sim_result.get("returncode", 0) != 0 or len(sim_result.get("errors", [])) > 0
        self._decision_history.append({
            "had_errors": had_errors,
            "threads": plan.get("threads", {}).get("count"),
            "elapsed": sim_result.get("elapsed", 0),
            "timestamp": time.time(),
        })
        # Keep only last 10
        self._decision_history = self._decision_history[-10:]

        # Record compile time for prediction model
        changed = plan.get("incremental", {}).get("changed", [])
        total_lines = plan.get("stats", {}).get("total_rtl_lines", 0)
        self.inc_compile.record_compile(
            changed, sim_result.get("elapsed", 0),
            len(changed), total_lines
        )

    def generate_simulation_script(self, plan: dict) -> str:
        tcl_parts = [
            "# ===================================================================",
            f"# Vivado Agent — Optimized Simulation Script",
            f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"# Project: {plan.get('project', {}).get('name', 'unknown')}",
            f"# Decisions: {'; '.join(plan.get('decisions', [])[:3])}",
            f"# ===================================================================",
            "",
        ]

        if plan.get("abort", {}).get("abort"):
            tcl_parts.append("# ABORTED: static scan found critical issues")
            return "\n".join(tcl_parts)

        if plan.get("override_tcl"):
            tcl_parts.append("# === Optimization Overrides ===")
            tcl_parts.extend(plan["override_tcl"])
            tcl_parts.append("")

        tcl_parts.append("# === Launch Simulation ===")
        tcl_parts.append("launch_simulation")
        tcl_parts.append('puts "Simulation completed."')

        return "\n".join(tcl_parts)

    def run_monitor(self, log_path: str | Path) -> SimulationMonitor:
        self.monitor = SimulationMonitor(log_path)
        return self.monitor