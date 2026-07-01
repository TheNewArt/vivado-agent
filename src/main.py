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
  optimize        Analyze project and generate optimization plan + TCL
  monitor         Real-time watch simulation log for X/Z/timeout
  debug           Full debug cycle: log -> waveform -> LLM fix -> re-run
  run             optimize + launch simulation + monitor pipeline
  clear-cache     Reset incremental compilation cache
  detect          Auto-detect project structure and display summary
        """,
    )
    parser.add_argument("action", nargs="?", default="status", help="See above")
    parser.add_argument("--config", "-c", default="config/default.yaml", help="Config file path")
    parser.add_argument("--top", "-t", help="Top module name (auto-detect if not given)")
    parser.add_argument("--rtl-dir", "-r", help="RTL source directory")
    parser.add_argument("--tb-dir", "-b", help="Testbench directory")
    parser.add_argument("--project-dir", "-p", help="Project root directory (for auto-detect)")
    parser.add_argument("--log", "-l", help="Simulation log path")
    parser.add_argument("--wdb", "-w", help="Waveform database path")
    parser.add_argument("--clock", type=float, default=10.0, help="Clock period in ns")
    parser.add_argument("--output", "-o", help="Output TCL script path (default: vivado_agent_sim.tcl)")

    args = parser.parse_args()

    config = Config()
    config.load(args.config)

    if args.top:
        config.data.setdefault("project", {})["top_module"] = args.top
    if args.rtl_dir:
        config.data.setdefault("project", {})["rtl_dir"] = args.rtl_dir
    if args.tb_dir:
        config.data.setdefault("project", {})["tb_dir"] = args.tb_dir

    action = args.action

    if action == "status":
        _status(config)
    elif action == "optimize":
        _optimize(config, args)
    elif action == "monitor":
        _monitor(config, args.log)
    elif action == "debug":
        _debug(config, args)
    elif action == "run":
        _run_pipeline(config, args)
    elif action == "clear-cache":
        _clear_cache(config)
    elif action == "detect":
        _detect(config, args)
    else:
        logger.error(f"Unknown action: {action}")
        sys.exit(1)


def _status(config: Config):
    print("Vivado Agent v0.1.0")
    print(f"  Config file: config/default.yaml")
    print(f"  Vivado path: {config.vivado_path}")
    print(f"  Incremental compile: {config.incremental_enabled}")
    print(f"  Waveform crop: {config.waveform_crop_enabled}")
    print(f"  Threads: {config.max_threads or 'auto'}")
    print(f"  LLM: {'enabled' if config.get('llm.enabled') else 'disabled'}")
    print(f"  LLM model: {config.get('llm.model', 'N/A')}")
    llm_key = config.build_llm_config().api_key
    print(f"  LLM API key: {'****' + llm_key[-4:] if llm_key else 'not set (env LLM_API_KEY)'}")


def _optimize(config: Config, args):
    from src.agent.simulation_optimizer import SimulationOptimizerAgent
    agent = SimulationOptimizerAgent(config)

    project_dir = args.project_dir or Path.cwd()
    project_files = agent.auto_detect(project_dir)

    top = args.top or project_files.top_module or config.get("project.top_module", "top")
    rtl_dir = args.rtl_dir or config.get("project.rtl_dir", "./src/hdl")
    tb_path = None
    if args.tb_dir:
        tbs = list(Path(args.tb_dir).glob("*.sv")) or list(Path(args.tb_dir).glob("*.v"))
        tb_path = tbs[0] if tbs else None
    elif project_files.tb_files:
        tb_path = project_files.tb_files[0]

    plan = agent.optimize_simulation(
        top_module=top,
        rtl_dir=rtl_dir,
        tb_path=tb_path,
        project_files=project_files,
    )
    script = agent.generate_simulation_script(plan)

    print("\n" + "=" * 60)
    print("  VIVADO AGENT — OPTIMIZATION PLAN")
    print("=" * 60)

    pj = plan.get("project", {})
    print(f"\n  Project       : {pj.get('name', 'unknown')}")
    print(f"  Source        : {pj.get('source', 'N/A')}")
    print(f"  Files         : {pj.get('rtl_count', 0)} RTL + {pj.get('tb_count', 0)} TB")
    print(f"  Top module    : {pj.get('top_module', 'auto')}")
    print(f"  Modules found : {plan.get('stats', {}).get('module_count', 0)}")

    print(f"\n  -- Incremental Compilation --")
    inc = plan.get("incremental", {})
    print(f"  Status    : {inc.get('status', 'N/A')}")
    if inc.get("details"):
        print(f"  Details   : {inc['details']}")
    pred = plan.get("time_prediction", {})
    if pred.get("predicted_s"):
        print(f"  Predicted : {pred['predicted_s']}s ({pred['predicted_min']} min) — {pred.get('confidence', '?')} confidence")

    print(f"\n  -- Performance Tuning --")
    stats = plan.get("stats", {})
    print(f"  Threads   : {stats.get('threads_recommended', 'auto')}")
    print(f"  Analysis  : {stats.get('analysis_time_s', 0):.2f}s")
    print(f"  RTL lines : {stats.get('total_rtl_lines', 0):,}")

    scans = plan.get("scan_issues", [])
    if scans:
        print(f"\n  -- Static Scan ({len(scans)} issues) --")
        for issue in scans:
            tag = "FAIL" if issue.severity == "error" else "WARN"
            print(f"  [{tag}] L{issue.line}: {issue.description}")

    print(f"\n  -- Generated TCL ({len(script.splitlines())} lines) --")
    print(script[:600] + ("\n  ... (truncated)" if len(script) > 600 else ""))

    out_path = args.output or "vivado_agent_sim.tcl"
    Path(out_path).write_text(script)
    print(f"\n  [OK] Script written to: {Path(out_path).resolve()}")


def _monitor(config: Config, log_path: str | None):
    from src.tools.runtime_monitor import SimulationMonitor
    if not log_path:
        logger.error("--log required for monitor action")
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
        logger.error("--log and --wdb required for debug action")
        return
    orchestrator = DebugOrchestrator(config)
    result = orchestrator.run_debug_cycle(args.log, args.wdb, args.top or "top", args.clock)

    for i, iteration in enumerate(result["iterations"]):
        print(f"\n=== Iteration {i+1} ===")
        errors = iteration.get("log_errors", [])
        timing = iteration.get("timing_violations", [])
        print(f"  Errors: {len(errors)}")
        for e in errors[:5]:
            print(f"  [{e.severity}] L{e.line_no} {e.category}: {e.message[:100]}")
        print(f"  Timing violations: {len(timing)}")
        for t in timing[:3]:
            print(f"  Slack={t.slack:.2f}  {t.from_reg} -> {t.to_reg}")
        fix = iteration.get("proposed_fix", "")
        if fix and "API error" not in fix:
            print(f"  LLM fix generated: {len(fix)} chars")
        else:
            print(f"  No LLM fix (check API key)")

    if result.get("fixed"):
        print("\nNo errors remaining — debug complete")
    else:
        print(f"\nDebug cycles: {len(result['iterations'])}")


def _run_pipeline(config: Config, args):
    from src.agent.simulation_optimizer import SimulationOptimizerAgent
    from src.core.tcl_engine import TCLEngine

    logger.info("Starting full pipeline: optimize -> simulate -> monitor")
    agent = SimulationOptimizerAgent(config)
    engine = TCLEngine(config.vivado_path)

    project_dir = args.project_dir or Path.cwd()
    project_files = agent.auto_detect(project_dir)
    top = args.top or project_files.top_module or config.get("project.top_module", "top")
    rtl_dir = args.rtl_dir or config.get("project.rtl_dir", "./src/hdl")
    tb_path = project_files.tb_files[0] if project_files.tb_files else None

    plan = agent.optimize_simulation(top, rtl_dir, tb_path, project_files)
    if plan.get("has_critical_issues"):
        logger.warning("Critical issues detected — aborting pipeline")
        for issue in plan["scan_issues"]:
            if issue.severity == "error":
                print(f"  [{issue.severity}] L{issue.line}: {issue.description}")
        return

    script = agent.generate_simulation_script(plan)
    result = engine.run_script(script)

    errors = engine.extract_errors(result["stdout"] + result["stderr"])
    if errors:
        logger.error(f"Simulation finished with {len(errors)} issues")
        for e in errors[:10]:
            print(f"  [{e['type']}] {e['message'][:120]}")
    else:
        logger.info("Simulation completed with no tool-level errors")

    print(f"  Elapsed: {result.get('elapsed', 0):.1f}s")
    return result


def _clear_cache(config: Config):
    from src.tools.incremental_compile import IncrementalCompileManager
    mgr = IncrementalCompileManager(config.xsim_cache)
    mgr.clear()
    print("Incremental compilation cache cleared")


def _detect(config: Config, args):
    from src.tools.project_detector import ProjectDetector
    project_dir = args.project_dir or Path.cwd()
    detector = ProjectDetector()
    pf = detector.detect(project_dir)
    print(f"Project: {pf.project_name}")
    print(f"Source: {pf.source}")
    print(f"Top module (guessed): {pf.top_module or 'unknown'}")
    print(f"RTL files ({len(pf.rtl_files)}):")
    for f in pf.rtl_files[:10]:
        print(f"  {f}")
    if len(pf.rtl_files) > 10:
        print(f"  ... +{len(pf.rtl_files)-10} more")
    print(f"TB files ({len(pf.tb_files)}):")
    for f in pf.tb_files[:5]:
        print(f"  {f}")


if __name__ == "__main__":
    main()