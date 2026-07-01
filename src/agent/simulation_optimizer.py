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
    Phase 1 agent: optimize simulation speed via module-level incremental
    compilation, multi-thread tuning, waveform trimming, and deadlock detection.
    Now with auto file detection and time prediction.
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

    def auto_detect(self, project_dir: str | Path | None = None) -> ProjectFiles:
        """Auto-detect project structure."""
        project_dir = project_dir or Path.cwd()
        pf = self.detector.detect(project_dir)
        rtl_dir = self.config.get("project.rtl_dir", "./src/hdl")
        # If directory scan found nothing, fall back to configured dir
        if not pf.rtl_files:
            pf = self.detector._from_scan(Path(rtl_dir))
        return pf

    def optimize_simulation(
        self,
        top_module: str | None = None,
        rtl_dir: str | Path | None = None,
        tb_path: str | Path | None = None,
        project_files: ProjectFiles | None = None,
    ) -> dict:
        """Generate full optimization plan."""
        start_time = time.time()

        plan = {
            "incremental": {"status": "", "details": ""},
            "thread_tcl": None,
            "waveform_tcl": None,
            "modules": {"changed": [], "cached": [], "all": []},
            "scan_issues": [],
            "override_tcl": [],
            "time_prediction": {"predicted_s": None},
            "project": {},
            "errors": [],
            "stats": {},
        }

        # 1) Auto-detect project if not provided
        if not project_files:
            project_files = self.auto_detect()

        if not rtl_dir:
            rtl_dir = Path(self.config.get("project.rtl_dir", "./src/hdl"))

        # If tb_path not given, use first TB from project detection
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

        # 2) Parse modules from RTL files
        module_to_files = {}
        for f in (project_files.rtl_files or list(rtl_dir.rglob("*.v")) + list(rtl_dir.rglob("*.sv"))):
            mods = self.parser.parse_file(f)
            for mod_name, info in mods.items():
                module_to_files.setdefault(mod_name, []).append(f)

        # 3) Module-level incremental check
        changed_mods, cached_mods, is_first = self.inc_compile.get_changed_modules(module_to_files)

        if is_first:
            plan["incremental"]["status"] = "first_run (no cache)"
            plan["incremental"]["details"] = f"All {len(changed_mods)} modules need compilation"
        elif not changed_mods:
            plan["incremental"]["status"] = "all_cached"
            plan["incremental"]["details"] = "All modules unchanged — full cache reuse"
        else:
            plan["incremental"]["status"] = f"partial: {len(changed_mods)} modules changed"
            plan["incremental"]["details"] = (
                f"Changed: {', '.join(changed_mods[:10])}" +
                (f" ... (+{len(changed_mods)-10} more)" if len(changed_mods) > 10 else "")
            )

        plan["modules"]["changed"] = changed_mods
        plan["modules"]["cached"] = cached_mods
        plan["modules"]["all"] = list(module_to_files.keys())

        if changed_mods:
            plan["override_tcl"].append(self.inc_compile.get_compile_deps_tcl(["sim_1"]))

        # Time prediction
        prediction = self.inc_compile.predict_compile_time(changed_mods)
        plan["time_prediction"] = prediction

        # 4) Multi-thread tuning
        thread_tcl = self.tuner.generate_optimization_tcl(rtl_dir)
        plan["thread_tcl"] = thread_tcl
        plan["override_tcl"].append(thread_tcl)

        # 5) Waveform trimming
        waveform_tcl = self.trim.generate_log_wave_tcl(
            top_module=top_module or project_files.top_module or "top",
            rtl_dir=rtl_dir,
            tb_path=tb_path,
            has_error=False,
        )
        plan["waveform_tcl"] = waveform_tcl
        plan["override_tcl"].append(waveform_tcl)

        # 6) Static scan
        if tb_path and Path(tb_path).exists():
            scan_issues = self.scanner.scan_testbench(tb_path)
            plan["scan_issues"] = scan_issues
            plan["has_critical_issues"] = self.scanner.has_critical_issues()

        # Stats
        total_lines = sum(
            len(f.read_text(encoding="utf-8", errors="replace").splitlines()) for f in project_files.rtl_files if f.is_file()
        ) if project_files.rtl_files else 0
        elapsed = time.time() - start_time
        plan["stats"] = {
            "analysis_time_s": round(elapsed, 2),
            "total_rtl_lines": total_lines,
            "module_count": len(module_to_files),
            "threads_recommended": self.tuner.recommend_threads(
                self.tuner.estimate_design_scale(rtl_dir)
            ),
        }

        return plan

    def generate_simulation_script(self, plan: dict) -> str:
        """Generate complete, executable TCL script with headers."""
        tcl_parts = [
            "# ===================================================================",
            f"# Vivado Agent — Optimized Simulation Script",
            f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"# Project: {plan.get('project', {}).get('name', 'unknown')}",
            f"# Top module: {plan.get('project', {}).get('top_module', 'top')}",
            f"# Modules: {plan.get('stats', {}).get('module_count', '?')} total, "
            f"{len(plan.get('modules', {}).get('changed', []))} changed, "
            f"{len(plan.get('modules', {}).get('cached', []))} cached",
            f"# Threads: {plan.get('stats', {}).get('threads_recommended', 'auto')}",
            f"# ===================================================================",
            "",
            "# === Setup ===",
            f"set_param general.maxThreads {plan.get('stats', {}).get('threads_recommended', 4)}",
            "",
        ]

        if plan.get("override_tcl"):
            tcl_parts.append("# === Optimization Overrides ===")
            tcl_parts.extend(plan["override_tcl"])
            tcl_parts.append("")

        tcl_parts.append("# === Launch Simulation (2020.2 compatible — launch_simulation blocks) ===")
        tcl_parts.append("launch_simulation")
        tcl_parts.append("puts \"Simulation completed.\"")

        tcl_parts.append("# === Done ===")
        tcl_parts.append("puts \"Simulation completed successfully\"")

        return "\n".join(tcl_parts)

    def run_monitor(self, log_path: str | Path) -> SimulationMonitor:
        self.monitor = SimulationMonitor(log_path)
        logger.info(f"Monitoring: {log_path}")
        return self.monitor