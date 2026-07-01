import re
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("synth_checker")


@dataclass
class SynthResult:
    passed: bool
    lut_count: int = 0
    reg_count: int = 0
    dsp_count: int = 0
    bram_count: int = 0
    max_logic_levels: int = 0
    max_fanout: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report_text: str = ""


class SynthChecker:
    """Run Vivado synthesis to check synthesizability and estimate PPA.

    Uses a minimal synth_design to validate that RTL is synthesizable,
    and reports logic levels, fanout, and resource utilization.
    """

    def __init__(self, vivado_path: str = "vivado", part: str = "xc7a35tcpg236-1"):
        self.vivado_path = vivado_path
        self.part = part

    def check_file(self, rtl_path: Path, top_module: str) -> SynthResult:
        """Run synthesis on a single file to check synthesizability."""
        if not rtl_path.exists():
            return SynthResult(passed=False, errors=[f"File not found: {rtl_path}"])

        src_dir = rtl_path.parent
        tcl = f"""
create_project -force synth_check ./synth_check_tmp -part {self.part} -quiet
add_files -norecurse [glob -dir {{{src_dir}}} *.v *.sv]
set_property top {top_module} [current_fileset]
launch_runs synth_1 -jobs 4
wait_on_run synth_1
set report [get_property REPORT_PREFIX [get_runs synth_1]]
puts "===SYNTH_DONE==="
report_utilization -hierarchical -file synth_util.rpt
report_timing -max_paths 10 -file synth_timing.rpt
puts "===REPORTS_GENERATED==="
close_project -quiet
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl", delete=False) as f:
            f.write(tcl)
            tcl_path = f.name

        try:
            proc = subprocess.run(
                [self.vivado_path, "-mode", "batch", "-source", tcl_path, "-nojournal"],
                capture_output=True, text=True, timeout=600,
            )
            output = proc.stdout + proc.stderr
            result = self._parse_output(output)
            # Parse utilization report
            util_path = Path("synth_util.rpt")
            if util_path.exists():
                result = self._parse_utilization(util_path.read_text(), result)
                util_path.unlink()
            # Parse timing report
            timing_path = Path("synth_timing.rpt")
            if timing_path.exists():
                result = self._parse_timing(timing_path.read_text(), result)
                timing_path.unlink()
            return result
        except subprocess.TimeoutExpired:
            return SynthResult(passed=False, errors=["Synthesis timed out (600s)"])
        except FileNotFoundError:
            return SynthResult(passed=False, errors=["Vivado not found"])
        finally:
            Path(tcl_path).unlink(missing_ok=True)
            import shutil
            shutil.rmtree("./synth_check_tmp", ignore_errors=True)

    def check_synthesizability(self, rtl_path: Path, top_module: str) -> dict:
        """High-level check: returns {synthesizable, error_count, warning_count, summary}."""
        result = self.check_file(rtl_path, top_module)
        return {
            "synthesizable": result.passed,
            "error_count": len(result.errors),
            "warning_count": len(result.warnings),
            "lut_count": result.lut_count,
            "reg_count": result.reg_count,
            "max_logic_levels": result.max_logic_levels,
            "max_fanout": result.max_fanout,
            "summary": self._format_summary(result),
        }

    def _parse_output(self, output: str) -> SynthResult:
        result = SynthResult(passed=True)
        for line in output.splitlines():
            if "ERROR:" in line:
                result.errors.append(line.strip())
                result.passed = False
            elif "CRITICAL WARNING:" in line:
                result.warnings.append(line.strip())
            elif "WARNING:" in line:
                if "Webtalk" not in line and "filemgmt" not in line:
                    result.warnings.append(line.strip())
        result.report_text = output[:2000]
        return result

    @staticmethod
    def _parse_utilization(text: str, result: SynthResult) -> SynthResult:
        lut_m = re.search(r"Slice LUTs\s+(\d+)", text)
        if lut_m:
            result.lut_count = int(lut_m.group(1))
        reg_m = re.search(r"Register\s+(\d+)", text)
        if reg_m:
            result.reg_count = int(reg_m.group(1))
        dsp_m = re.search(r"DSP\s+(\d+)", text)
        if dsp_m:
            result.dsp_count = int(dsp_m.group(1))
        bram_m = re.search(r"Block RAM Tile\s+(\d+)", text)
        if bram_m:
            result.bram_count = int(bram_m.group(1))
        return result

    @staticmethod
    def _parse_timing(text: str, result: SynthResult) -> SynthResult:
        levels = re.findall(r"Levels of Logic\s*:\s*(\d+)", text)
        if levels:
            result.max_logic_levels = max(int(x) for x in levels)
        fanouts = re.findall(r"Fanout\s*:\s*(\d+)", text)
        if fanouts:
            result.max_fanout = max(int(x) for x in fanouts)
        return result

    @staticmethod
    def _format_summary(result: SynthResult) -> str:
        if not result.passed:
            return f"NOT SYNTHESIZABLE: {len(result.errors)} errors"
        parts = [
            f"LUT={result.lut_count} REG={result.reg_count}",
        ]
        if result.dsp_count:
            parts.append(f"DSP={result.dsp_count}")
        if result.bram_count:
            parts.append(f"BRAM={result.bram_count}")
        if result.max_logic_levels:
            parts.append(f"max_logic_levels={result.max_logic_levels}")
        if result.max_fanout:
            parts.append(f"max_fanout={result.max_fanout}")
        if result.warnings:
            parts.append(f"warnings={len(result.warnings)}")
        return " | ".join(parts)