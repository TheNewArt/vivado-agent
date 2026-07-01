#!/usr/bin/env python3
"""Vivado Agent: AI-driven simulation acceleration & auto-debug for FPGA development."""

import sys
import argparse
from pathlib import Path
from src.utils.logger import setup_logger
from src.core.config import Config

logger = setup_logger("vivado-agent")


def main():
    parser = argparse.ArgumentParser(
        description="Vivado Agent — simulation acceleration & auto-debug",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Actions:
  status          Show configuration and system info
  optimize        Analyze project, make decisions, generate TCL
  run             optimize + simulate + record result + monitor
  monitor         Real-time watch simulation log for X/Z/timeout
  debug           Full debug cycle: log -> waveform -> LLM fix -> re-run
  detect          Auto-detect project structure
  clear-cache     Reset incremental compilation cache
        """,
    )
    parser.add_argument("action", nargs="?", default="status")
    parser.add_argument("--config", "-c", default="config/default.yaml")
    parser.add_argument("--top", "-t", help="Top module name")
    parser.add_argument("--rtl-dir", "-r", help="RTL source directory")
    parser.add_argument("--tb-dir", "-b", help="Testbench directory")
    parser.add_argument("--project-dir", "-p", help="Project root directory")
    parser.add_argument("--log", "-l", help="Simulation log path")
    parser.add_argument("--wdb", "-w", help="Waveform database path")
    parser.add_argument("--clock", type=float, default=10.0, help="Clock period in ns")
    parser.add_argument("--output", "-o", default="vivado_agent_sim.tcl", help="Output TCL path")

    args = parser.parse_args()
    config = Config()
    config.load(args.config)

    if args.top:
        config.data.setdefault("project", {})["top_module"] = args.top
    if args.rtl_dir:
        config.data.setdefault("project", {})["rtl_dir"] = args.rtl_dir
    if args.tb_dir:
        config.data.setdefault("project", {})["tb_dir"] = args.tb_dir

    actions = {
        "status": lambda: _status(config),
        "optimize": lambda: _optimize(config, args),
        "run": lambda: _run_pipeline(config, args),
        "monitor": lambda: _monitor(config, args.log),
        "debug": lambda: _debug(config, args),
        "detect": lambda: _detect(config, args),
        "clear-cache": lambda: _clear_cache(config),
    }
    fn = actions.get(args.action)
    if fn:
        fn()
    else:
        logger.error(f"Unknown action: {args.action}")
        sys.exit(1)


def _status(config: Config):
    print("Vivado Agent v0.1.0")
    print(f"  Vivado path : {config.vivado_path}")
    print(f"  LLM model   : {config.get('llm.model', 'N/A')}")
    llm_key = config.build_llm_config().api_key
    print(f"  LLM API key : {'****' + llm_key[-4:] if llm_key else 'not set'}")
    print(f"  Cache dir   : {config.xsim_cache}")
    print(f"  Threads     : {config.max_threads or 'auto'}")


def _optimize(config: Config, args):
    from src.agent.simulation_optimizer import SimulationOptimizerAgent
    agent = SimulationOptimizerAgent(config)

    project_dir = args.project_dir or Path.cwd()
    project_files = agent.auto_detect(project_dir)
    top = args.top or project_files.top_module or config.get("project.top_module", "top")
    rtl_dir = args.rtl_dir or config.get("project.rtl_dir", "./src/hdl")
    tb_path = _find_tb(args, project_files)

    plan = agent.optimize_simulation(top, rtl_dir, tb_path, project_files)
    script = agent.generate_simulation_script(plan)
    _print_plan(plan, script)
    Path(args.output).write_text(script)
    print(f"\n  [OK] Script -> {Path(args.output).resolve()}")


def _run_pipeline(config: Config, args):
    from src.agent.simulation_optimizer import SimulationOptimizerAgent
    from src.core.tcl_engine import TCLEngine

    agent = SimulationOptimizerAgent(config)
    engine = TCLEngine(config.vivado_path)

    project_dir = args.project_dir or Path.cwd()
    project_files = agent.auto_detect(project_dir)
    top = args.top or project_files.top_module or config.get("project.top_module", "top")
    rtl_dir = args.rtl_dir or config.get("project.rtl_dir", "./src/hdl")
    tb_path = _find_tb(args, project_files)

    # 1) Optimize
    plan = agent.optimize_simulation(top, rtl_dir, tb_path, project_files)

    # 2) Abort check
    if plan.get("abort", {}).get("abort"):
        print(f"\n  [ABORT] {plan['abort']['reason']}")
        for iss in plan["abort"].get("issues", []):
            print(f"    L{iss.line} {iss.description}")
        print("  Fix these issues before running simulation.")
        return

    # 3) Show plan
    script = agent.generate_simulation_script(plan)
    _print_plan(plan, script)

    # 4) Execute
    result = engine.run_script(script)
    elapsed = result.get("elapsed", 0)

    # 5) Record result for adaptive tuning
    result["errors"] = engine.extract_errors(result.get("stdout", "") + result.get("stderr", ""))
    agent.record_result(plan, result)

    # 6) Report
    errs = result["errors"]
    if errs:
        print(f"\n  [RESULT] {len(errs)} issues in {elapsed:.1f}s")
        for e in errs[:5]:
            print(f"    [{e['type']}] {e['message'][:120]}")
    else:
        print(f"\n  [RESULT] PASS in {elapsed:.1f}s (0 tool errors)")
    return result


def _monitor(config: Config, log_path: str | None):
    from src.tools.runtime_monitor import SimulationMonitor
    if not log_path:
        logger.error("--log required")
        return
    import threading, time
    monitor = SimulationMonitor(log_path)
    stop_event = threading.Event()

    def run():
        monitor.start_monitoring()
        while not stop_event.is_set():
            time.sleep(1)
            if monitor.has_errors():
                print(monitor.report())
                break
        monitor.stop()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    print("Monitoring... Ctrl+C to stop")
    try:
        while t.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_event.set()
        print("\n" + monitor.report())


def _debug(config: Config, args):
    from src.agent.debug_orchestrator import DebugOrchestrator
    if not args.log or not args.wdb:
        logger.error("--log and --wdb required")
        return

    orchestrator = DebugOrchestrator(config)
    result = orchestrator.run_debug_cycle(args.log, args.wdb, args.top or "top", args.clock)

    for i, it in enumerate(result.get("iterations", [])):
        print(f"\n=== Iteration {i+1} ===")
        for d in it.get("decisions", []):
            print(f"  [DECISION] {d}")
        fix = it.get("proposed_fix", "")
        if fix and not fix.startswith("# LLM"):
            print(f"  LLM fix: {len(fix)} chars")
        else:
            print(f"  No LLM fix (check API key)")

    if result.get("aborted"):
        print(f"\n[ABORTED] {result.get('abort_reason', '')}")
    elif result.get("fixed"):
        print("\n[FIXED] No errors remaining")
    else:
        print(f"\n[DONE] {len(result['iterations'])} iterations, {result.get('fixes_applied', 0)} fixes applied")


def _detect(config: Config, args):
    from src.tools.project_detector import ProjectDetector
    pf = ProjectDetector().detect(args.project_dir or Path.cwd())
    print(f"Project    : {pf.project_name}")
    print(f"Source     : {pf.source}")
    print(f"Device     : {pf.device or 'unknown'}")
    print(f"Family     : {pf.device_family or 'unknown'}")
    print(f"Vivado     : {pf.vivado_version or 'unknown'}")
    print(f"Top        : {pf.top_module or 'unknown'}")
    extras = []
    if pf.has_petalinux: extras.append("PetaLinux")
    if pf.has_vitis_hls: extras.append("VitisHLS")
    if pf.has_hls_source: extras.append("HLS_C")
    if extras: print(f"Extras     : {', '.join(extras)}")
    print(f"RTL        : {len(pf.rtl_files)} files")
    for f in pf.rtl_files[:10]:
        print(f"  {f.relative_to(Path.cwd()) if f.is_relative_to(Path.cwd()) else f}")
    if len(pf.rtl_files) > 10:
        print(f"  ... +{len(pf.rtl_files)-10} more")
    print(f"TB         : {len(pf.tb_files)} files")
    for f in pf.tb_files[:5]:
        print(f"  {f.relative_to(Path.cwd()) if f.is_relative_to(Path.cwd()) else f}")


def _clear_cache(config: Config):
    from src.tools.incremental_compile import IncrementalCompileManager
    IncrementalCompileManager(config.xsim_cache).clear()
    print("Cache cleared")


# ---- helpers ----

def _find_tb(args, project_files):
    if args.tb_dir:
        tbs = list(Path(args.tb_dir).glob("*.sv")) or list(Path(args.tb_dir).glob("*.v"))
        return tbs[0] if tbs else None
    return project_files.tb_files[0] if project_files.tb_files else None


def _print_plan(plan: dict, script: str):
    print("\n" + "=" * 60)
    print("  VIVADO AGENT — OPTIMIZATION PLAN")
    print("=" * 60)

    pj = plan.get("project", {})
    print(f"\n  Project       : {pj.get('name', '?')}")
    print(f"  Files         : {pj.get('rtl_count', 0)} RTL + {pj.get('tb_count', 0)} TB")
    print(f"  Modules       : {plan.get('stats', {}).get('module_count', 0)}")

    print(f"\n  -- Decisions --")
    for d in plan.get("decisions", []):
        print(f"  {d}")

    pred = plan.get("time_prediction", {})
    if pred.get("predicted_s"):
        t = pred["predicted_s"]
        print(f"  Estimated     : {t}s ({t/60:.1f} min) [{pred.get('confidence', '?')}]")

    scans = plan.get("scan_issues", [])
    if scans:
        print(f"\n  -- Static Scan ({len(scans)} issues) --")
        for iss in scans:
            tag = "FAIL" if iss.severity == "error" else "WARN"
            print(f"  [{tag}] L{iss.line}")

    stats = plan.get("stats", {})
    print(f"\n  Threads       : {stats.get('threads_selected', 'auto')}")
    print(f"  RTL lines     : {stats.get('total_rtl_lines', 0):,}")
    print(f"  Analysis      : {stats.get('analysis_time_s', 0):.2f}s")

    if plan.get("abort", {}).get("abort"):
        print(f"\n  [ABORT] {plan['abort']['reason']}")

    print(f"\n  -- Generated TCL ({len(script.splitlines())} lines) --")
    print(script[:600] + ("\n  ... (truncated)" if len(script) > 600 else ""))


if __name__ == "__main__":
    main()