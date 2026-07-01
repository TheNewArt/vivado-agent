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
        rtl_files = [Path(f) for f in pf.get("rtl_files", [])]
        if rtl_files:
            return checker.check_synthesizability(rtl_files[0].parent, top)
        return {"synthesizable": False, "error": "no RTL files"}

    def fix_timing(project_dir: str = ".", top_module: str = "") -> dict:
        """Analyze timing report and auto-generate SDC constraints."""
        from src.tools.timing_constraint_gen import TimingConstraintGenerator
        from src.tools.ppa_analyzer import PPAAnalyzer
        pf = detect(project_dir)
        top = top_module or pf.get("top_module", "top")
        rtl_dir = Path(pf["rtl_files"][0]).parent if pf["rtl_files"] else Path(project_dir)
        analyzer = PPAAnalyzer(config.vivado_path)
        ppa = analyzer.analyze(rtl_dir, top)
        tcg = TimingConstraintGenerator()
        # Try to parse the timing report from the PPA run
        paths = tcg.parse_timing_report(ppa.report_text)
        constraints = tcg.generate_constraints(paths)
        xdc = tcg.generate_xdc()
        return {
            "violations": len(paths),
            "worst_slack_ns": ppa.worst_slack_ns,
            "constraints_generated": len(constraints),
            "xdc_file": "auto_fix_constraints.xdc",
        }

    def analyze_protocol(log_path: str = "", wdb_path: str = "") -> dict:
        """Analyze AXI protocol transactions from WDB."""
        from src.tools.protocol_analyzer import ProtocolAnalyzer
        from src.tools.wdb_reader import WDBReader
        if wdb_path:
            reader = WDBReader(config.vivado_path)
            signals = reader.get_signal_names(wdb_path)
            analyzer = ProtocolAnalyzer()
            axi = analyzer.add_axi("axi")
            for sig in signals:
                if any(s in sig.upper() for s in ["AWVALID", "WVALID", "ARVALID", "RVALID"]):
                    snapshots = reader.extract_signal_values(wdb_path, [sig], 0)
                    for s in snapshots:
                        axi.feed(s.name, s.value, s.time_ns)
            events = analyzer.detect_all_events()
            return {
                "axi_signals_found": len(axi.get_relevant_signals()),
                "transactions": events,
            }
        return {"error": "wdb_path required"}

    def timing_sim(project_dir: str = ".", top_module: str = "", tb_top: str = "") -> dict:
        """Run post-synthesis timing simulation with SDF."""
        from src.tools.post_synth_flow import PostSynthFlow
        pf = detect(project_dir)
        top = top_module or pf.get("top_module", "top")
        rtl_dir = Path(pf["rtl_files"][0]).parent if pf["rtl_files"] else Path(project_dir)
        tb_path = Path(pf["tb_files"][0]) if pf["tb_files"] else None
        if not tb_path:
            return {"error": "no testbench found"}
        flow = PostSynthFlow()
        result = flow.run_timing_sim(rtl_dir, tb_path, top, tb_top)
        violations = flow.check_timing_violations(result.report_text)
        return {
            "rtl_passed": result.rtl_passed,
            "gate_passed": result.gate_passed,
            "timing_matched": result.timing_matched,
            "setup_violations": violations["setup_violations"],
            "hold_violations": violations["hold_violations"],
        }

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