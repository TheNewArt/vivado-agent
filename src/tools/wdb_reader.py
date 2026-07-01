import subprocess
import tempfile
import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("wdb_reader")


@dataclass
class SignalSnapshot:
    name: str
    width: int
    value: str
    time_ns: float


@dataclass
class WaveformAnalysis:
    fault_signals: list[SignalSnapshot]
    fault_chain: list[str]
    timestamp: float
    clock_cycles: int


class WDBReader:
    """Read Vivado waveform database via TCL and extract signal snapshots."""

    def __init__(self, vivado_path: str = "vivado"):
        self.vivado_path = vivado_path

    def open_database(self, wdb_path: str | Path) -> str:
        """Generate TCL to open a WDB file."""
        wdb_path = Path(wdb_path).resolve()
        return f"""
open_wave_database {{{wdb_path}}}
set wdb_objects [get_wave_objects -all]
"""

    def extract_signal_snapshot(
        self,
        wdb_path: str | Path,
        signals: list[str],
        time_ns: float,
        window_ns: float = 100.0,
    ) -> str:
        """Generate TCL to extract signal values at a given time window."""
        wdb_path = Path(wdb_path).resolve()
        start_ns = max(0, time_ns - window_ns / 2)
        end_ns = time_ns + window_ns / 2
        sig_list = " ".join(f"{{{s}}}" for s in signals)
        return f"""
open_wave_database {{{wdb_path}}}
open_wave_config [current_wave_config]
set wave_objects [list {sig_list}]
add_wave $wave_objects
seek_wave $start_ns
set snapshot [report_wave -format csv -range {{{start_ns}ns}} {{{end_ns}ns}}]
puts "---SNAPSHOT_START---"
puts $snapshot
puts "---SNAPSHOT_END---"
"""

    def analyze_around_error(
        self,
        wdb_path: str | Path,
        top_module: str,
        error_time_ns: float,
        clock_period_ns: float = 10.0,
        cycles_before: int = 50,
        cycles_after: int = 50,
    ) -> str:
        """Generate TCL to analyze signal states around an error timestamp."""
        wdb_path = Path(wdb_path).resolve()
        start = error_time_ns - cycles_before * clock_period_ns
        end_val = error_time_ns + cycles_after * clock_period_ns
        return f"""
open_wave_database {{{wdb_path}}}
open_wave_config [current_wave_config]

# Add top-level signals
add_wave -r /
seek_wave {start}ns

# Export fault window
set fptr [open "fault_analysis.csv" w]
puts $fptr "Time,Signal,Value"
set current_time $start
while {{ $current_time <= {end_val} }} {{
    seek_wave $current_time
    set sigs [get_wave_objects -all]
    foreach sig $sigs {{
        set val [read_wave_value $sig]
        puts $fptr "$current_time,$sig,$val"
    }}
    set current_time [expr {{$current_time + {clock_period_ns}}}]
}}
close $fptr
puts "Fault analysis exported to fault_analysis.csv"
"""

    @staticmethod
    def parse_csv_snapshot(csv_text: str) -> list[SignalSnapshot]:
        """Parse CSV output from waveform snapshot into structured data."""
        snapshots = []
        in_snapshot = False
        for line in csv_text.splitlines():
            if "---SNAPSHOT_START---" in line:
                in_snapshot = True
                continue
            if "---SNAPSHOT_END---" in line:
                break
            if not in_snapshot:
                continue
            parts = line.strip().split(",")
            if len(parts) >= 3 and parts[0] != "Time":
                try:
                    snapshots.append(SignalSnapshot(
                        name=parts[1].strip(),
                        width=0,
                        value=parts[2].strip(),
                        time_ns=float(parts[0].strip()),
                    ))
                except (ValueError, IndexError):
                    pass
        return snapshots

    @staticmethod
    def trace_fault_chain(snapshots: list[SignalSnapshot], target_signal: str) -> list[str]:
        """Trace back the signal path that leads to a faulty value."""
        chain = []
        target_val = ""
        for s in snapshots:
            if s.name == target_signal:
                target_val = s.value
                chain.append(f"{s.name} = {s.value} @ {s.time_ns}ns")
                break

        # Find driving signals
        driver_prefix = target_signal.rsplit("/", 1)[0] if "/" in target_signal else ""
        for s in snapshots:
            if driver_prefix and s.name.startswith(driver_prefix) and s.name != target_signal:
                chain.append(f"  ├─ {s.name} = {s.value}")
            elif not driver_prefix and "/" not in s.name:
                chain.append(f"  ├─ {s.name} = {s.value}")

        return chain if chain else [f"Signal {target_signal} not found in snapshot"]