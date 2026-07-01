import re
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("ppa_analyzer")


@dataclass
class PPAResult:
    timing_met: bool = False
    worst_slack_ns: float = 0.0
    total_negative_slack_ns: float = 0.0
    failing_paths: int = 0
    total_paths: int = 0
    lut_count: int = 0
    reg_count: int = 0
    dsp_count: int = 0
    bram_count: int = 0
    lut_util_pct: float = 0.0
    reg_util_pct: float = 0.0
    total_power_w: float = 0.0
    dynamic_power_w: float = 0.0
    static_power_w: float = 0.0
    report_text: str = ""


class PPAAnalyzer:
    """Run Vivado implementation to get PPA (Power, Performance, Area) metrics.

    Runs synth + place + route, then extracts timing closure, utilization, and power.
    """

    def __init__(self, vivado_path: str = "vivado", part: str = "xc7a35tcpg236-1"):
        self.vivado_path = vivado_path
        self.part = part

    def analyze(self, rtl_dir: Path, top_module: str) -> PPAResult:
        """Full PPA analysis: synthesize + place + route + report."""
        if not rtl_dir.exists():
            return PPAResult()

        tcl = f"""
create_project -force ppa_check ./ppa_check_tmp -part {self.part} -quiet
add_files -norecurse [glob -dir {{{rtl_dir}}} *.v *.sv]
set_property top {top_module} [current_fileset]
launch_runs synth_1 -jobs 4
wait_on_run synth_1
launch_runs impl_1 -jobs 4
wait_on_run impl_1
puts "===IMPL_DONE==="
report_timing_summary -file ppa_timing.rpt
report_utilization -file ppa_util.rpt
report_power -file ppa_power.rpt
puts "===PPA_DONE==="
close_project -quiet
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl", delete=False) as f:
            f.write(tcl)
            tcl_path = f.name

        try:
            proc = subprocess.run(
                [self.vivado_path, "-mode", "batch", "-source", tcl_path, "-nojournal"],
                capture_output=True, text=True, timeout=1800,
            )
            result = PPAResult()
            result.report_text = (proc.stdout + proc.stderr)[:3000]

            # Parse timing
            timing_path = Path("ppa_timing.rpt")
            if timing_path.exists():
                result = self._parse_timing(timing_path.read_text(), result)
                timing_path.unlink()
            # Parse utilization
            util_path = Path("ppa_util.rpt")
            if util_path.exists():
                result = self._parse_utilization(util_path.read_text(), result)
                util_path.unlink()
            # Parse power
            power_path = Path("ppa_power.rpt")
            if power_path.exists():
                result = self._parse_power(power_path.read_text(), result)
                power_path.unlink()

            # Check if timing was met
            has_timing_error = "Timing constraints are not met" in result.report_text
            result.timing_met = not has_timing_error and result.worst_slack_ns >= 0

            import shutil
            shutil.rmtree("./ppa_check_tmp", ignore_errors=True)
            return result

        except subprocess.TimeoutExpired:
            logger.error("PPA analysis timed out (1800s)")
            return PPAResult()
        except FileNotFoundError:
            logger.error("Vivado not found for PPA analysis")
            return PPAResult()
        finally:
            Path(tcl_path).unlink(missing_ok=True)
            import shutil
            shutil.rmtree("./ppa_check_tmp", ignore_errors=True)

    @staticmethod
    def _parse_timing(text: str, result: PPAResult) -> PPAResult:
        m = re.search(r"Worst Negative Slack \(WNS\):\s*([-\d.]+)", text)
        if m:
            result.worst_slack_ns = float(m.group(1))
        m = re.search(r"Total Negative Slack \(TNS\):\s*([-\d.]+)", text)
        if m:
            result.total_negative_slack_ns = float(m.group(1))
        m = re.search(r"Number of Failing Endpoints:\s*(\d+)", text)
        if m:
            result.failing_paths = int(m.group(1))
        m = re.search(r"Total Number of Endpoints:\s*(\d+)", text)
        if m:
            result.total_paths = int(m.group(1))
        return result

    @staticmethod
    def _parse_utilization(text: str, result: PPAResult) -> PPAResult:
        m = re.search(r"Slice LUTs\s+(\d+)\s+\((\d+)\)", text)
        if m:
            result.lut_count = int(m.group(1))
            result.lut_util_pct = float(m.group(2).rstrip("%)"))
        m = re.search(r"Register\s+(\d+)\s+\((\d+)\)", text)
        if m:
            result.reg_count = int(m.group(1))
            result.reg_util_pct = float(m.group(2).rstrip("%)"))
        m = re.search(r"DSP\s+(\d+)", text)
        if m:
            result.dsp_count = int(m.group(1))
        m = re.search(r"Block RAM Tile\s+(\d+)", text)
        if m:
            result.bram_count = int(m.group(1))
        return result

    @staticmethod
    def _parse_power(text: str, result: PPAResult) -> PPAResult:
        m = re.search(r"Total On-Chip Power\s+\(W\)\s*:\s*([\d.]+)", text)
        if m:
            result.total_power_w = float(m.group(1))
        m = re.search(r"Dynamic\s+([\d.]+)", text)
        if m:
            result.dynamic_power_w = float(m.group(1))
        m = re.search(r"Static\s+([\d.]+)", text)
        if m:
            result.static_power_w = float(m.group(1))
        return result

    def format_report(self, result: PPAResult) -> str:
        lines = ["=== PPA Report ==="]
        lines.append(f"Timing: {'MET' if result.timing_met else 'NOT MET'}"
                     f"  WNS={result.worst_slack_ns:.3f}ns  TNS={result.total_negative_slack_ns:.3f}ns"
                     f"  ({result.failing_paths}/{result.total_paths} paths failing)")
        lines.append(f"Area: LUT={result.lut_count} ({result.lut_util_pct:.1f}%)"
                     f"  REG={result.reg_count} ({result.reg_util_pct:.1f}%)"
                     f"  DSP={result.dsp_count}  BRAM={result.bram_count}")
        lines.append(f"Power: Total={result.total_power_w:.3f}W"
                     f"  Dynamic={result.dynamic_power_w:.3f}W"
                     f"  Static={result.static_power_w:.3f}W")
        return "\n".join(lines)