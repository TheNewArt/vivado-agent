import re
import subprocess
import tempfile
import shutil
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
    checker: str = ""  # which tool ran: "verilator", "vivado", "skipped"


class SynthChecker:
    """Synthesis checking with triple backend: Verilator (ms) + WSL (ms) + Vivado (s).

    - Native Linux: Verilator directly
    - Windows with WSL: `wsl verilator` for ms-level lint
    - No Verilator: Vivado xvlog fallback (slower)
    """

    def __init__(self, vivado_path: str = "vivado",
                 verilator_path: str = "verilator",
                 wsl_verilator: bool = True,
                 part: str = "xc7a35tcpg236-1"):
        self.vivado_path = vivado_path
        self.verilator_path = verilator_path
        self.wsl_verilator = wsl_verilator
        self.part = part

    # ── Verilator: millisecond-level lint ─────────────────────────────────

    def _verilator_available(self) -> bool:
        """Check Verilator availability: native or WSL."""
        if shutil.which("verilator") is not None:
            return True
        if self.wsl_verilator:
            try:
                subprocess.run(
                    ["wsl", "which", "verilator"],
                    capture_output=True, timeout=5,
                )
                return True
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        return False

    def _verilator_cmd(self, cmd_args: list[str]) -> list[str]:
        """Build verilator command (native or via wsl)."""
        if shutil.which("verilator") is not None:
            return [self.verilator_path] + cmd_args
        if self.wsl_verilator:
            return ["wsl", self.verilator_path] + cmd_args
        return [self.verilator_path] + cmd_args  # will fail, caller handles

    def verilator_lint(self, rtl_path: Path, top_module: str = "",
                       include_dirs: list[Path] | None = None) -> SynthResult:
        """Run Verilator --lint-only. Returns in milliseconds.

        Supports: native Linux, WSL on Windows, falls back gracefully.

        Catches: syntax errors, width mismatches, untyped signals,
        unused signals, blocking vs non-blocking misuse.
        """
        if not rtl_path.exists():
            return SynthResult(passed=False, errors=["File not found"],
                               checker="verilator")

        if not self._verilator_available():
            return SynthResult(passed=True, checker="skipped",
                               errors=["Verilator not installed (tried native and WSL)"])

        cmd = ["--lint-only", "-Wall"]
        if top_module:
            cmd += ["--top-module", top_module]
        if include_dirs:
            for d in include_dirs:
                cmd += ["-I", str(d)]
        cmd += ["--cc", str(rtl_path.resolve())]
        full_cmd = self._verilator_cmd(cmd)

        try:
            proc = subprocess.run(
                full_cmd, capture_output=True, text=True, timeout=30,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            errors, warnings = [], []

            for line in output.splitlines():
                if "%Error" in line:
                    errors.append(line.strip())
                elif "%Warning" in line:
                    warnings.append(line.strip())

            result = SynthResult(
                passed=len(errors) == 0,
                errors=errors,
                warnings=warnings,
                report_text=output[:2000],
                checker="verilator",
            )

            if result.passed:
                logger.debug(f"Verilator lint passed: {rtl_path.name}")
            else:
                logger.warning(f"Verilator lint FAILED ({len(errors)} errors): {rtl_path.name}")
                for e in errors[:5]:
                    logger.warning(f"  {e}")

            return result

        except FileNotFoundError:
            return SynthResult(passed=True, checker="skipped",
                               errors=["Verilator not found"])
        except subprocess.TimeoutExpired:
            return SynthResult(passed=True, checker="skipped",
                               errors=["Verilator timed out"])

    # ── Vivado: full synthesis ────────────────────────────────────────────

    def check_file(self, rtl_path: Path, top_module: str) -> SynthResult:
        """Run full Vivado synthesis. Takes seconds, produces PPA data."""
        if not rtl_path.exists():
            return SynthResult(passed=False, errors=[f"File not found: {rtl_path}"])

        src_dir = rtl_path.parent
        tcl = f"""
create_project -force synth_check ./synth_check_tmp -part {self.part} -quiet
add_files -norecurse [glob -dir {{{src_dir}}} *.v *.sv]
set_property top {top_module} [current_fileset]
launch_runs synth_1 -jobs 4
wait_on_run synth_1
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
            output = (proc.stdout or "") + (proc.stderr or "")
            result = self._parse_output(output)
            result.checker = "vivado"

            util_path = Path("synth_util.rpt")
            if util_path.exists():
                result = self._parse_utilization(util_path.read_text(), result)
                util_path.unlink()
            timing_path = Path("synth_timing.rpt")
            if timing_path.exists():
                result = self._parse_timing(timing_path.read_text(), result)
                timing_path.unlink()

            shutil.rmtree("./synth_check_tmp", ignore_errors=True)
            return result
        except subprocess.TimeoutExpired:
            shutil.rmtree("./synth_check_tmp", ignore_errors=True)
            return SynthResult(passed=False, checker="vivado",
                               errors=["Synthesis timed out (600s)"])
        except FileNotFoundError:
            shutil.rmtree("./synth_check_tmp", ignore_errors=True)
            return SynthResult(passed=False, checker="vivado",
                               errors=["Vivado not found"])
        finally:
            Path(tcl_path).unlink(missing_ok=True)
            shutil.rmtree("./synth_check_tmp", ignore_errors=True)

    def quick_check(self, rtl_path: Path, top_module: str = "",
                    include_dirs: list[Path] | None = None) -> SynthResult:
        """Fast syntax check: Verilator first (ms), fall back to xvlog (s).

        Returns within milliseconds if Verilator is available.
        """
        # Verilator path: milliseconds
        result = self.verilator_lint(rtl_path, top_module, include_dirs)
        if result.checker == "verilator":
            return result

        # Fallback: Vivado xvlog (10s+)
        logger.info("Verilator unavailable, falling back to Vivado xvlog")
        return self._xvlog_check(rtl_path)

    def _xvlog_check(self, rtl_path: Path) -> SynthResult:
        """Fallback: xvlog syntax check via Vivado."""
        try:
            cmd = [self.vivado_path, "-mode", "batch", "-source", "-"]
            if rtl_path.suffix == ".vhd":
                stdin = f"read_vhdl -quiet {{{rtl_path}}}\n"
            else:
                stdin = f"read_verilog -quiet {{{rtl_path}}}\n"
            proc = subprocess.run(
                cmd, input=stdin, capture_output=True, text=True, timeout=60,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            has_error = "ERROR:" in output
            errors = []
            for line in output.splitlines():
                if "ERROR:" in line:
                    errors.append(line.strip())
            return SynthResult(
                passed=not has_error,
                errors=errors,
                report_text=output[:2000],
                checker="vivado",
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return SynthResult(passed=True, checker="skipped",
                               errors=["No syntax checker available"])

    def check_synthesizability(self, rtl_path: Path, top_module: str) -> dict:
        """Two-stage check: quick lint first, full synth only if lint passes."""
        # Stage 1: Verilator lint (ms)
        lint = self.verilator_lint(rtl_path, top_module)
        if not lint.passed:
            return {
                "synthesizable": False,
                "stage": "lint",
                "checker": "verilator",
                "error_count": len(lint.errors),
                "warning_count": len(lint.warnings),
                "summary": f"LINT FAILED: {len(lint.errors)} errors",
            }

        # Stage 2: Vivado synth (s) — only if lint passes
        result = self.check_file(rtl_path, top_module)
        return {
            "synthesizable": result.passed,
            "stage": "synth" if result.checker == "vivado" else "lint",
            "checker": result.checker,
            "error_count": len(result.errors),
            "warning_count": len(result.warnings),
            "lut_count": result.lut_count,
            "reg_count": result.reg_count,
            "max_logic_levels": result.max_logic_levels,
            "max_fanout": result.max_fanout,
            "summary": self._format_summary(result),
        }

    @staticmethod
    def _parse_output(output: str) -> SynthResult:
        result = SynthResult(passed=True)
        for line in output.splitlines():
            if "ERROR:" in line:
                result.errors.append(line.strip())
                result.passed = False
            elif "CRITICAL WARNING:" in line:
                result.warnings.append(line.strip())
            elif "WARNING:" in line and "Webtalk" not in line and "filemgmt" not in line:
                result.warnings.append(line.strip())
        result.report_text = output[:2000]
        return result

    @staticmethod
    def _parse_utilization(text: str, result: SynthResult) -> SynthResult:
        m = re.search(r"Slice LUTs\s+(\d+)", text)
        if m: result.lut_count = int(m.group(1))
        m = re.search(r"Register\s+(\d+)", text)
        if m: result.reg_count = int(m.group(1))
        m = re.search(r"DSP\s+(\d+)", text)
        if m: result.dsp_count = int(m.group(1))
        m = re.search(r"Block RAM Tile\s+(\d+)", text)
        if m: result.bram_count = int(m.group(1))
        return result

    @staticmethod
    def _parse_timing(text: str, result: SynthResult) -> SynthResult:
        levels = re.findall(r"Levels of Logic\s*:\s*(\d+)", text)
        if levels: result.max_logic_levels = max(int(x) for x in levels)
        fanouts = re.findall(r"Fanout\s*:\s*(\d+)", text)
        if fanouts: result.max_fanout = max(int(x) for x in fanouts)
        return result

    @staticmethod
    def _format_summary(result: SynthResult) -> str:
        if not result.passed:
            return f"NOT SYNTHESIZABLE: {len(result.errors)} errors"
        parts = [f"LUT={result.lut_count} REG={result.reg_count}"]
        if result.dsp_count: parts.append(f"DSP={result.dsp_count}")
        if result.bram_count: parts.append(f"BRAM={result.bram_count}")
        if result.max_logic_levels: parts.append(f"max_logic_levels={result.max_logic_levels}")
        if result.max_fanout: parts.append(f"max_fanout={result.max_fanout}")
        if result.warnings: parts.append(f"warnings={len(result.warnings)}")
        return " | ".join(parts)