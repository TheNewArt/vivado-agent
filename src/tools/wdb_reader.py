import csv
import io
import re
import subprocess
import tempfile
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
    snapshots: list[SignalSnapshot]
    fault_chain: list[dict]
    x_signals: set[str]
    total_signals: int


class WDBReader:
    """Read Vivado waveform database (WDB) via TCL execution.

    WDB is a closed binary format — cannot read directly from Python.
    Must go through Vivado's TCL interface: open database, read values,
    optionally export to VCD then parse.
    """

    def __init__(self, vivado_path: str = "vivado"):
        self.vivado_path = vivado_path

    def _run_tcl(self, script: str) -> dict:
        """Execute TCL script in Vivado batch mode."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tcl", delete=False, encoding="utf-8"
        ) as f:
            f.write(script)
            tcl_path = f.name

        try:
            proc = subprocess.run(
                [self.vivado_path, "-mode", "batch", "-source", tcl_path],
                capture_output=True, text=True, timeout=300,
            )
            return {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Timeout", "returncode": -1}
        finally:
            Path(tcl_path).unlink(missing_ok=True)

    def open_waveform(self, wdb_path: str | Path) -> str:
        """Generate TCL to open a WDB file."""
        wdb = Path(wdb_path).resolve()
        return f"""
open_wave_database {{{wdb}}}
open_wave_config [current_wave_config]
"""

    def get_signal_names(self, wdb_path: str | Path, pattern: str = "*") -> list[str]:
        """Get all signal names from WDB matching pattern."""
        wdb = Path(wdb_path).resolve()
        tcl = f"""
open_wave_database {{{wdb}}}
set sigs [get_wave_objects -filter {{NAME =~ {pattern}}}]
set result ""
foreach sig $sigs {{
    append result "[get_property NAME $sig]\n"
}}
puts "===SIGNALS==="
puts $result
puts "===END==="
exit
"""
        result = self._run_tcl(tcl)
        signals = []
        in_section = False
        for line in result.get("stdout", "").splitlines():
            if "===SIGNALS===" in line:
                in_section = True
                continue
            if "===END===" in line:
                break
            if in_section and line.strip():
                signals.append(line.strip())
        return signals

    def export_vcd(self, wdb_path: str | Path, output_vcd: str | Path,
                   start_ns: float = 0, end_ns: float = 1000) -> dict:
        """Export WDB to VCD format for parsing."""
        wdb = Path(wdb_path).resolve()
        vcd = Path(output_vcd).resolve()
        tcl = f"""
open_wave_database {{{wdb}}}
open_wave_config [current_wave_config]
write_wave -format vcd -force -start {start_ns}ns -end {end_ns}ns {{{vcd}}}
puts "VCD exported to {vcd}"
exit
"""
        return self._run_tcl(tcl)

    def extract_signal_values(self, wdb_path: str | Path, signals: list[str],
                              time_ns: float) -> list[SignalSnapshot]:
        """Extract signal values at end of simulation using xsim --tclbatch.

        Vivado 2020.2 batch mode does not support seek_wave/read_wave_value.
        Instead, we use xsim.exe directly with a TCL script that reads values
        via get_value after running the simulation.

        If signals is ['*'] or ['/*'], auto-discovers all signals via get_objects.
        """
        wdb = Path(wdb_path).resolve()
        snap_name = wdb.stem

        auto_discover = len(signals) == 1 and signals[0] in ("*", "/*")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl",
                                         delete=False, encoding="utf-8") as f:
            f.write("log_wave -r /\n")
            if auto_discover:
                f.write("run 1ns\n")
                f.write('set f [open "sigs.csv" w]\n')
                f.write('puts $f "time,signal,value"\n')
                f.write('set sigs [get_objects -r /*]\n')
                f.write('foreach s $sigs {\n')
                f.write('    set name [get_property NAME $s]\n')
                f.write('    set val [get_value $s]\n')
                f.write('    puts $f "0,$name,$val"\n')
                f.write('}\n')
            else:
                f.write(f"run {max(1, time_ns)}ns\n")
                f.write('set f [open "sigs.csv" w]\n')
                f.write('puts $f "time,signal,value"\n')
                for sig in signals:
                    f.write(f'if {{![catch {{set val [get_value {{{sig}}}]}}]}} {{\n')
                    f.write(f'    puts $f "{time_ns},{sig},$val"\n')
                    f.write('}\n')
            f.write("close $f\n")
            f.write("puts \"===EXTRACT_DONE===\"\n")
            tcl_path = f.name

        try:
            xsim = self.vivado_path.replace("vivado.bat", "xsim.bat")
            if not Path(xsim).exists():
                xsim = str(Path(self.vivado_path).parent / "xsim.bat")
            # Use forward slashes for TCL path
            tcl_path_tcl = tcl_path.replace("\\", "/")
            proc = subprocess.run(
                [xsim, snap_name, "--tclbatch", tcl_path_tcl],
                capture_output=True, text=True, timeout=120,
                cwd=str(wdb.parent),
            )
            csv_path = wdb.parent / "sigs.csv"
            if csv_path.exists():
                result = self._parse_csv_snapshot(csv_path)
                csv_path.unlink()
                return result
            logger.warning(f"xsim OK but no CSV: {proc.stdout[-200:]}")
            return []
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning(f"xsim extraction failed: {e}")
            return []
        finally:
            Path(tcl_path).unlink(missing_ok=True)

    def analyze_around_error(self, wdb_path: str | Path, top_module: str,
                              error_time_ns: float, clock_period_ns: float = 10.0,
                              cycles_before: int = 50, cycles_after: int = 50) -> WaveformAnalysis:
        """Comprehensive analysis around error: export VCD then parse."""
        start_ns = max(0, error_time_ns - cycles_before * clock_period_ns)
        end_ns = error_time_ns + cycles_after * clock_period_ns

        with tempfile.NamedTemporaryFile(suffix=".vcd", delete=False) as tmp:
            vcd_path = tmp.name

        export_result = self.export_vcd(wdb_path, vcd_path, start_ns, end_ns)
        if export_result.get("returncode", 0) != 0:
            logger.warning(f"VCD export failed: {export_result.get('stderr', '')[:200]}")
            return WaveformAnalysis(snapshots=[], fault_chain=[], x_signals=set(), total_signals=0)

        snapshots = self._parse_vcd(vcd_path)
        Path(vcd_path).unlink(missing_ok=True)

        x_signals = {s for s in snapshots if 'x' in s.value.lower() or 'z' in s.value.lower()}
        fault_chain = self._trace_fault_chain(snapshots, top_module)

        return WaveformAnalysis(
            snapshots=snapshots,
            fault_chain=fault_chain,
            x_signals=x_signals,
            total_signals=len(snapshots),
        )

    @staticmethod
    def _parse_extract(stdout: str) -> list[SignalSnapshot]:
        """Parse signal extraction output."""
        snapshots = []
        in_data = False
        for line in stdout.splitlines():
            if "===EXTRACT_START===" in line:
                in_data = True
                continue
            if "===EXTRACT_END===" in line:
                break
            if in_data and line.strip():
                parts = line.split(",", 2)
                if len(parts) >= 3:
                    try:
                        snapshots.append(SignalSnapshot(
                            name=parts[0].strip(),
                            width=int(parts[1].strip()),
                            value=parts[2].strip(),
                            time_ns=0.0,
                        ))
                    except (ValueError, IndexError):
                        pass
        return snapshots

    @staticmethod
    def _parse_csv_snapshot(csv_path: Path) -> list[SignalSnapshot]:
        """Parse CSV file produced by xsim --tclbatch get_value."""
        snapshots = []
        try:
            import csv
            with open(csv_path, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 3 and row[0] != "time":
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

    @staticmethod
    def _parse_vcd(vcd_path: Path) -> list[SignalSnapshot]:
        """Parse VCD file (subset of VCD format for signal values)."""
        snapshots = []
        signals = {}       # id -> name
        current_time = 0.0
        timescale = 1.0    # default ns
        value_pattern = re.compile(r'^([01xXzZ])\s*(\S+)$')

        try:
            with open(vcd_path, errors="replace") as f:
                for line in f:
                    line = line.strip()

                    # Timescale
                    m = re.match(r'\$timescale\s+(\d+)\s*(\S+)\s+\$end', line, re.IGNORECASE)
                    if m:
                        mult = float(m.group(1))
                        unit = m.group(2).lower()
                        mult_map = {'s': 1e9, 'ms': 1e6, 'us': 1e3, 'ns': 1.0, 'ps': 1e-3, 'fs': 1e-6}
                        timescale = mult * mult_map.get(unit, 1.0)

                    # Signal definitions
                    m = re.match(r'\$var\s+\S+\s+(\S+)\s+(\S+)\s+(\S+)', line, re.IGNORECASE)
                    if m:
                        signals[m.group(2)] = m.group(3)

                    # Time dump
                    m = re.match(r'#(\d+)', line)
                    if m:
                        current_time = int(m.group(1)) * timescale

                    # Value change
                    m = re.match(r'^([01xXzZ])(\S+)$', line)
                    if m:
                        val, code = m.group(1), m.group(2)
                        sig_name = signals.get(code, code)
                        snapshots.append(SignalSnapshot(
                            name=sig_name,
                            width=1,
                            value=val.upper(),
                            time_ns=current_time,
                        ))
        except Exception as e:
            logger.warning(f"VCD parse error: {e}")

        return snapshots

    @staticmethod
    def _trace_fault_chain(snapshots: list[SignalSnapshot], target_signal: str) -> list[dict]:
        """Trace backward from target signal to find fault propagation."""
        chain = []
        target_vals = [s for s in snapshots if target_signal in s.name]

        if not target_vals:
            return [{"signal": target_signal, "status": "not_found"}]

        # Find first X/Z transition
        fault_time = None
        fault_val = None
        for s in sorted(target_vals, key=lambda x: x.time_ns):
            if s.value in ('X', 'Z'):
                fault_time = s.time_ns
                fault_val = s.value
                break

        if fault_time is None:
            return [{"signal": target_signal, "status": "no_xz_found"}]

        chain.append({
            "signal": target_signal,
            "time_ns": fault_time,
            "value": fault_val,
            "type": "target_output",
        })

        # Find signals that changed at or before fault_time
        candidates = sorted(
            [s for s in snapshots if s.time_ns <= fault_time and target_signal not in s.name],
            key=lambda x: (fault_time - x.time_ns, x.name),
        )

        seen = set()
        for s in candidates[:20]:
            if s.name not in seen and s.value in ('X', 'Z'):
                seen.add(s.name)
                chain.append({
                    "signal": s.name,
                    "time_ns": s.time_ns,
                    "value": s.value,
                    "type": "upstream_xz",
                })

        return chain

    @staticmethod
    def format_analysis_text(analysis: WaveformAnalysis, max_signals: int = 20) -> str:
        """Format analysis results as readable text."""
        lines = [
            f"Waveform Analysis: {analysis.total_signals} signals, "
            f"{len(analysis.x_signals)} X/Z signals",
        ]
        if analysis.x_signals:
            lines.append(f"X/Z signals: {', '.join(sorted(analysis.x_signals)[:20])}")
        lines.append("\nFault Chain:")
        for entry in analysis.fault_chain[:10]:
            lines.append(f"  @{entry.get('time_ns', 0):.0f}ns {entry.get('signal', '?')} = "
                         f"{entry.get('value', '?')} [{entry.get('type', '?')}]")
        if analysis.snapshots:
            lines.append(f"\nSignal snapshots ({min(len(analysis.snapshots), max_signals)} shown):")
            for s in analysis.snapshots[:max_signals]:
                lines.append(f"  @{s.time_ns:.0f}ns {s.name} = {s.value}")
        return "\n".join(lines)