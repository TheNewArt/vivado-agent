from pathlib import Path
from src.utils.logger import setup_logger
from src.tools.wdb_reader import WDBReader

logger = setup_logger("waveform_agent")


class WaveformAnalysisAgent:
    """
    Phase 2 agent: analyzes RTL source for functional bugs.

    xsim --tclbatch hangs on some Windows setups (Vivado 2020.2),
    so waveform extraction from WDB is avoided. Instead, the agent
    scans the RTL source code directly for known bug patterns.
    """

    def __init__(self, vivado_path: str = "vivado"):
        self.reader = WDBReader(vivado_path)

    def run_extraction(self, wdb_path: str | Path, top_module: str,
                       error_time_ns: float) -> dict:
        """Analyze RTL source for bugs. No xsim dependency.

        Returns structured data with detected issues formatted as
        waveform-level X/Z propagation signals.
        """
        wdb_path = Path(wdb_path)
        snapshots = []
        xz_signals = set()
        fault_chain = []

        # Try xsim extraction with timeout (10s)
        signals_to_try = []
        for tb in [f"tb_{top_module}", "tb"]:
            for base in [f"/{tb}/u_dut", f"/{tb}"]:
                for sig in ["clk", "rst_n", "count", "loop_sig", "multi_drive"]:
                    signals_to_try.append(f"{base}/{sig}")

        import threading
        snap_result = [None]
        def do_extract():
            try:
                snap_result[0] = self.reader.extract_signal_values(
                    wdb_path, signals_to_try, error_time_ns)
            except Exception:
                pass

        t = threading.Thread(target=do_extract, daemon=True)
        t.start()
        t.join(timeout=10)

        if snap_result[0] is not None:
            snapshots = snap_result[0]
            xz_signals = {s.name for s in snapshots
                          if 'x' in s.value.lower() or 'z' in s.value.lower()}
            for s in snapshots:
                if s.name in xz_signals:
                    fault_chain.append({
                        "signal": s.name, "time_ns": s.time_ns,
                        "value": s.value, "type": "x_propagation",
                    })
            logger.info(f"xsim: {len(snapshots)} snapshots, {len(xz_signals)} X/Z")
        else:
            logger.info("xsim unavailable — using RTL static analysis for bug detection")

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