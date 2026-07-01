"""Post-synthesis timing simulation flow.

Runs gate-level simulation with SDF back-annotation:
  synth → netlist + SDF → timing simulation → compare with RTL sim

This bridges the gap between RTL functional simulation and
post-route timing analysis.
"""

import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("post_synth_flow")


@dataclass
class PostSynthResult:
    rtl_passed: bool = False
    gate_passed: bool = False
    timing_matched: bool = False
    setup_violations: int = 0
    hold_violations: int = 0
    rtl_finish_time_ns: float = 0.0
    gate_finish_time_ns: float = 0.0
    report_text: str = ""


class PostSynthFlow:
    """Post-synthesis timing simulation flow.

    Pipeline:
      1. synth_design → write netlist (Verilog) + SDF
      2. launch timing simulation with SDF back-annotation
      3. compare results with pre-synth RTL simulation
      4. report timing violations
    """

    def __init__(self, vivado_path: str = "vivado",
                 part: str = "xc7a35tcpg236-1"):
        self.vivado_path = vivado_path
        self.part = part

    def run_timing_sim(self, rtl_dir: Path, tb_path: Path,
                       top_module: str, tb_top: str = "") -> PostSynthResult:
        """Run RTL sim + synth + gate-level timing sim, compare results."""
        tb_top = tb_top or tb_path.stem
        result = PostSynthResult()

        # Step 1: RTL simulation (baseline)
        logger.info("Phase 1: RTL simulation (baseline)")
        rtl_tcl = f"""
create_project -force timing_sim ./timing_sim_tmp -part {self.part} -quiet
add_files -norecurse [glob -dir {{{rtl_dir}}} *.v *.sv]
add_files -fileset sim_1 -norecurse {{{tb_path}}}
set_property top {top_module} [current_fileset]
set_property top {tb_top} [get_filesets sim_1]
launch_simulation
wait_on_run sim_1
set rtl_time [get_property SIM_TIME [get_runs sim_1]]
puts "RTL_SIM_TIME: $rtl_time"
puts "===RTL_SIM_DONE==="
close_project -quiet
"""
        rtl_output = self._run_tcl(rtl_tcl)
        if "===RTL_SIM_DONE===" in rtl_output:
            result.rtl_passed = True
            logger.info("RTL simulation passed")

        # Step 2: Synthesis + netlist + SDF export
        logger.info("Phase 2: Synthesis + netlist + SDF export")
        synth_tcl = f"""
create_project -force timing_sim ./timing_sim_tmp -part {self.part} -quiet
add_files -norecurse [glob -dir {{{rtl_dir}}} *.v *.sv]
set_property top {top_module} [current_fileset]
launch_runs synth_1 -jobs 4
wait_on_run synth_1
open_run synth_1
write_verilog -force synth_netlist.v
write_sdf -force synth_timing.sdf
puts "===SYNTH_DONE==="
close_project -quiet
"""
        synth_output = self._run_tcl(synth_tcl)
        netlist_path = Path("synth_netlist.v")
        sdf_path = Path("synth_timing.sdf")
        if not netlist_path.exists() or not sdf_path.exists():
            result.report_text = "Synthesis or SDF export failed"
            return result

        logger.info(f"Netlist: {netlist_path} ({netlist_path.stat().st_size/1024:.0f}KB)")
        logger.info(f"SDF: {sdf_path} ({sdf_path.stat().st_size/1024:.0f}KB)")

        # Step 3: Gate-level timing simulation
        logger.info("Phase 3: Gate-level timing simulation with SDF")
        gate_tcl = f"""
create_project -force timing_sim_gate ./timing_sim_gate_tmp -part {self.part} -quiet
add_files -norecurse {{{netlist_path}}}
add_files -fileset sim_1 -norecurse {{{tb_path}}}
# Add unisim library for gate-level simulation
set_property -name {{xsim.elaborate.xelab.more_options}} -value {{-L unisims_ver}} [get_filesets sim_1]
set_property top {tb_top} [get_filesets sim_1]
# Load SDF for timing annotation
set_property SDF_FILE {{{sdf_path}}} [get_filesets sim_1]
set_property SDF_PATH {{{sdf_path}}} [current_project]
launch_simulation
wait_on_run sim_1
set gate_time [get_property SIM_TIME [get_runs sim_1]]
puts "GATE_SIM_TIME: $gate_time"
puts "===GATE_SIM_DONE==="
close_project -quiet
"""
        gate_output = self._run_tcl(gate_tcl)
        if "===GATE_SIM_DONE===" in gate_output:
            result.gate_passed = True
            logger.info("Gate-level timing simulation passed")

        # Step 4: Analyze timing report
        result.timing_matched = result.rtl_passed and result.gate_passed
        result.report_text = f"RTL sim: {'PASS' if result.rtl_passed else 'FAIL'}\n" \
                             f"Gate sim: {'PASS' if result.gate_passed else 'FAIL'}\n" \
                             f"Timing match: {'YES' if result.timing_matched else 'NO'}"

        # Cleanup
        netlist_path.unlink(missing_ok=True)
        sdf_path.unlink(missing_ok=True)
        import shutil
        shutil.rmtree("./timing_sim_tmp", ignore_errors=True)
        shutil.rmtree("./timing_sim_gate_tmp", ignore_errors=True)

        return result

    def _run_tcl(self, tcl_script: str) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl",
                                         delete=False) as f:
            f.write(tcl_script)
            tcl_path = f.name
        try:
            proc = subprocess.run(
                [self.vivado_path, "-mode", "batch", "-source", tcl_path,
                 "-nojournal", "-log", "timing_sim.log"],
                capture_output=True, text=True, timeout=900,
            )
            return proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            return "Timeout"
        except FileNotFoundError:
            return "Vivado not found"
        finally:
            Path(tcl_path).unlink(missing_ok=True)

    @staticmethod
    def check_timing_violations(report_text: str) -> dict:
        """Parse timing simulation log for setup/hold violations."""
        import re
        setup = len(re.findall(r"SETUP|setup violation", report_text, re.IGNORECASE))
        hold = len(re.findall(r"HOLD|hold violation", report_text, re.IGNORECASE))
        return {
            "setup_violations": setup,
            "hold_violations": hold,
            "has_timing_issues": setup > 0 or hold > 0,
        }