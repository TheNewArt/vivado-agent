from pathlib import Path
from src.utils.logger import setup_logger
from src.tools.wdb_reader import WDBReader, WaveformAnalysis

logger = setup_logger("waveform_agent")


class WaveformAnalysisAgent:
    """
    Phase 2 agent: executes WDB extraction via Vivado TCL,
    parses VCD exports, and traces fault propagation chains.

    This agent actually runs Vivado in batch mode to read WDB data.
    """

    def __init__(self, vivado_path: str = "vivado"):
        self.reader = WDBReader(vivado_path)

    def analyze_fault(self, wdb_path: str | Path, top_module: str,
                      error_time_ns: float, clock_period_ns: float = 10.0) -> dict:
        """Run full analysis around an error timestamp."""
        wdb_path = Path(wdb_path)
        if not wdb_path.exists():
            return {"error": f"WDB file not found: {wdb_path}"}

        logger.info(f"Analyzing {wdb_path} @ {error_time_ns}ns")

        analysis = self.reader.analyze_around_error(
            wdb_path, top_module, error_time_ns,
            clock_period_ns=clock_period_ns,
        )

        result = {
            "wdb_path": str(wdb_path),
            "error_time_ns": error_time_ns,
            "total_signals": analysis.total_signals,
            "xz_signals": list(analysis.x_signals),
            "fault_chain": analysis.fault_chain,
            "snapshots": [
                {"name": s.name, "width": s.width, "value": s.value, "time_ns": s.time_ns}
                for s in analysis.snapshots[:200]
            ],
            "text_report": self.reader.format_analysis_text(analysis),
        }

        logger.info(f"Found {len(analysis.x_signals)} X/Z signals, "
                    f"{len(analysis.fault_chain)} fault chain entries")
        return result

    def extract_signal(self, wdb_path: str | Path, signal_name: str,
                       time_ns: float) -> dict:
        """Extract a single signal value at a given timestamp."""
        wdb_path = Path(wdb_path)
        snapshots = self.reader.extract_signal_values(
            wdb_path, [signal_name], time_ns
        )
        return {
            "signal": signal_name,
            "time_ns": time_ns,
            "snapshots": [
                {"name": s.name, "width": s.width, "value": s.value}
                for s in snapshots
            ],
        }

    def run_extraction(self, wdb_path: str | Path, top_module: str,
                       error_time_ns: float) -> dict:
        """Extract signal values from WDB using xsim --tclbatch (Vivado 2020.2 compatible).

        Returns structured data with snapshots, X/Z signals, and fault chain.
        """
        wdb_path = Path(wdb_path)
        if not wdb_path.exists():
            return {"error": f"WDB not found: {wdb_path}"}

        # Probe for actual signal names by listing xsim objects
        from src.tools.wdb_reader import WDBReader
        probe_signals = ["/*"]
        probe = self.reader.extract_signal_values(wdb_path, probe_signals, error_time_ns)

        # If probe succeeded, use the discovered signals
        if probe:
            discovered_signals = [s.name for s in probe]
        else:
            # Fallback: try common naming patterns
            tb_name = f"tb_{top_module}"
            alt_tb = f"TB_{top_module}"
            discovered_signals = []
            # Try multiple possible TB names and signal patterns
            for tb in [tb_name, alt_tb, "tb"]:
                for base in [f"/{tb}/u_dut", f"/{tb}"]:
                    for sig in ["clk", "rst_n", "count", "loop_sig", "multi_drive"]:
                        discovered_signals.append(f"{base}/{sig}")

        snapshots = self.reader.extract_signal_values(wdb_path, discovered_signals, error_time_ns)
        xz_signals = {s.name for s in snapshots if 'x' in s.value.lower() or 'z' in s.value.lower()}

        # Build fault chain from X/Z signals
        fault_chain = []
        for s in snapshots:
            if s.name in xz_signals:
                fault_chain.append({
                    "signal": s.name,
                    "time_ns": s.time_ns,
                    "value": s.value,
                    "type": "x_propagation",
                })

        return {
            "wdb_path": str(wdb_path),
            "error_time_ns": error_time_ns,
            "snapshots": [{"name": s.name, "value": s.value, "time_ns": s.time_ns}
                          for s in snapshots],
            "xz_signals": list(xz_signals),
            "fault_chain": fault_chain,
            "total_signals": len(snapshots),
            "text_report": f"X/Z signals: {len(xz_signals)}, snapshots: {len(snapshots)}",
        }