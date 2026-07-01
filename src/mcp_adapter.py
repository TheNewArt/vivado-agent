"""
MCP (Model Context Protocol) adapter for Vivado Agent.

Exposes the agent as a set of MCP tools for integration with LLM
orchestrators like OpenClaw. Each tool is a self-contained function
with typed inputs and outputs.

Usage:
  from src.mcp_adapter import mcp_tools
  # tools = mcp_tools(config)
  # Each tool is callable: tools['detect'](project_dir='/path')
"""

from pathlib import Path
from src.utils.logger import setup_logger
from src.core.config import Config

logger = setup_logger("mcp_adapter")


def mcp_tools(config: Config | None = None) -> dict:
    """Return a dict of MCP-compatible tool functions."""
    if config is None:
        config = Config()
        config.load()

    def detect(project_dir: str = ".") -> dict:
        from src.tools.project_detector import ProjectDetector
        pf = ProjectDetector().detect(project_dir)
        return {
            "project": pf.project_name,
            "source": pf.source,
            "top_module": pf.top_module or "",
            "rtl_files": [str(f) for f in pf.rtl_files],
            "tb_files": [str(f) for f in pf.tb_files],
        }

    def optimize(project_dir: str = ".", top_module: str = "") -> dict:
        from src.agent.simulation_optimizer import SimulationOptimizerAgent
        agent = SimulationOptimizerAgent(config)
        pf = agent.auto_detect(project_dir)
        top = top_module or pf.top_module or "top"
        plan = agent.optimize_simulation(top, str(Path(project_dir) / "src" / "hdl"),
                                         pf.tb_files[0] if pf.tb_files else None, pf)
        tcl = agent.generate_simulation_script(plan)
        return {
            "decisions": plan.get("decisions", []),
            "tcl_script": tcl,
            "abort": plan.get("abort", {}).get("abort", False),
            "abort_reason": plan.get("abort", {}).get("reason", ""),
            "scan_issues": [{"line": i.line, "severity": i.severity, "description": i.description}
                            for i in plan.get("scan_issues", [])],
            "threads": plan.get("stats", {}).get("threads_selected", 0),
        }

    def run_simulation(project_dir: str = ".", top_module: str = "") -> dict:
        from src.agent.simulation_optimizer import SimulationOptimizerAgent
        from src.core.tcl_engine import TCLEngine
        agent = SimulationOptimizerAgent(config)
        engine = TCLEngine(config.vivado_path)
        pf = agent.auto_detect(project_dir)
        top = top_module or pf.top_module or "top"
        plan = agent.optimize_simulation(top)
        if plan.get("abort", {}).get("abort"):
            return {"status": "aborted", "reason": plan["abort"]["reason"]}
        script = agent.generate_simulation_script(plan)
        result = engine.run_script(script)
        result["errors"] = engine.extract_errors(result.get("stdout", "") + result.get("stderr", ""))
        agent.record_result(plan, result)
        return {
            "status": "completed" if not result["errors"] else "errors",
            "elapsed_s": result.get("elapsed", 0),
            "error_count": len(result["errors"]),
        }

    def scan(project_dir: str = ".") -> dict:
        from src.agent.simulation_optimizer import SimulationOptimizerAgent
        agent = SimulationOptimizerAgent(config)
        pf = agent.auto_detect(project_dir)
        from src.tools.static_scanner import StaticScanner
        scanner = StaticScanner()
        issues = []
        for tb in pf.tb_files:
            issues.extend(scanner.scan_testbench(tb))
        if pf.rtl_files:
            issues.extend(scanner.scan_rtl(Path(project_dir)))
        return {
            "total_issues": len(issues),
            "errors": [{"line": i.line, "description": i.description, "suggestion": i.suggestion}
                       for i in issues if i.severity == "error"],
            "warnings": [{"line": i.line, "description": i.description, "suggestion": i.suggestion}
                         for i in issues if i.severity == "warning"],
        }

    def debug(log_path: str, wdb_path: str, top_module: str = "top") -> dict:
        from src.agent.debug_orchestrator import DebugOrchestrator
        orch = DebugOrchestrator(config)
        result = orch.run_debug_cycle(log_path, wdb_path, top_module)
        return {
            "fixed": result.get("fixed", False),
            "aborted": result.get("aborted", False),
            "iterations": len(result.get("iterations", [])),
            "fixes_applied": result.get("fixes_applied", 0),
        }

    def check_synth(project_dir: str = ".", top_module: str = "") -> dict:
        from src.tools.synth_checker import SynthChecker
        pf = detect(project_dir)
        top = top_module or pf.get("top_module", "top")
        checker = SynthChecker(config.vivado_path)
        return checker.check_synthesizability(Path(pf["rtl_files"][0]).parent, top)

    def ppa(project_dir: str = ".", top_module: str = "") -> dict:
        from src.tools.ppa_analyzer import PPAAnalyzer
        pf = detect(project_dir)
        top = top_module or pf.get("top_module", "top")
        rtl_dir = Path(pf["rtl_files"][0]).parent if pf["rtl_files"] else Path(project_dir)
        analyzer = PPAAnalyzer(config.vivado_path)
        result = analyzer.analyze(rtl_dir, top)
        return {
            "timing_met": result.timing_met,
            "wns_ns": result.worst_slack_ns,
            "tns_ns": result.total_negative_slack_ns,
            "lut": result.lut_count,
            "reg": result.reg_count,
            "dsp": result.dsp_count,
            "bram": result.bram_count,
            "power_w": result.total_power_w,
        }

    return {
        "detect": detect,
        "optimize": optimize,
        "run_simulation": run_simulation,
        "scan": scan,
        "debug": debug,
        "check_synth": check_synth,
        "ppa": ppa,
    }