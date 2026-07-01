import csv
import io
import re
from pathlib import Path
from src.utils.logger import setup_logger
from src.tools.wdb_reader import WDBReader, SignalSnapshot

logger = setup_logger("waveform_agent")


class WaveformAnalysisAgent:
    """
    Phase 2 agent: executes WDB extraction via Vivado TCL,
    parses CSV snapshots, and traces fault propagation chains.
    """

    def __init__(self, vivado_path: str = "vivado"):
        self.reader = WDBReader(vivado_path)

    def analyze_fault(
        self,
        wdb_path: str | Path,
        top_module: str,
        error_time_ns: float,
        clock_period_ns: float = 10.0,
        target_signals: list[str] | None = None,
    ) -> dict:
        """Generate TCL + parse spec for fault analysis."""
        wdb_path = Path(wdb_path)
        analysis = {
            "wdb_path": str(wdb_path),
            "error_time_ns": error_time_ns,
            "tcl_commands": [],
            "fault_chain": [],
            "snapshots": [],
            "signal_summary": {},
        }

        tcl = self.reader.analyze_around_error(
            wdb_path, top_module, error_time_ns,
            clock_period_ns, 50, 50,
        )
        analysis["tcl_commands"].append(tcl)

        logger.info(f"Generated WDB analysis TCL for {wdb_path.name} @ {error_time_ns}ns")
        return analysis

    def run_extraction(self, wdb_path: str | Path, top_module: str, error_time_ns: float) -> dict:
        """Execute Vivado to extract waveform data around error. Returns parsed snapshots."""
        wdb_path = Path(wdb_path)
        from src.core.tcl_engine import TCLEngine

        engine = TCLEngine()
        tcl = self.reader.extract_signal_snapshot(
            wdb_path,
            [f"{top_module}/*"],
            error_time_ns,
            window_ns=500.0,
        )

        result = engine.run_script(tcl)
        stdout = result.get("stdout", "")

        # Parse CSV snapshot from output
        snapshots = self.reader.parse_csv_snapshot(stdout)

        # If CSV file was written, parse it
        csv_path = Path("fault_analysis.csv")
        if csv_path.exists():
            snapshots = self._parse_csv_file(csv_path)
            csv_path.unlink()

        # Build fault chain
        fault_chain = []
        if snapshots:
            # Group by signal, find first fault
            signal_values: dict[str, list[tuple[float, str]]] = {}
            for s in snapshots:
                signal_values.setdefault(s.name, []).append((s.time_ns, s.value))

            for sig, vals in signal_values.items():
                unique_vals = set(v for _, v in vals)
                if len(unique_vals) > 1:
                    fault_chain.append({
                        "signal": sig,
                        "transitions": len(unique_vals),
                        "first_val": vals[0][1],
                        "last_val": vals[-1][1],
                    })

            fault_chain.sort(key=lambda x: x["transitions"], reverse=True)

        logger.info(f"Extracted {len(snapshots)} samples, {len(fault_chain)} signals with transitions")
        return {
            "wdb_path": str(wdb_path),
            "error_time_ns": error_time_ns,
            "snapshots": snapshots[:100],
            "fault_chain": fault_chain[:20],
            "total_snapshots": len(snapshots),
        }

    @staticmethod
    def _parse_csv_file(csv_path: Path) -> list[SignalSnapshot]:
        """Parse fault_analysis.csv into structured data."""
        snapshots = []
        try:
            with open(csv_path, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 3 and row[0] != "Time":
                        try:
                            snapshots.append(SignalSnapshot(
                                name=row[1].strip(),
                                width=0,
                                value=row[2].strip(),
                                time_ns=float(row[0].strip()),
                            ))
                        except (ValueError, IndexError):
                            pass
        except Exception as e:
            logger.warning(f"CSV parse error: {e}")
        return snapshots

    def trace_fault_chain(self, fault_signal: str, snapshots: list[SignalSnapshot]) -> list[str]:
        """Trace backward from a faulty DUT output to root cause."""
        return self.reader.trace_fault_chain(snapshots, fault_signal)