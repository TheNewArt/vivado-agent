"""Expanded FPGA bug database: 120+ error/warning patterns with repair suggestions.

Covers: XSim, Synthesis, Implementation, Timing, CDC, UVM, TCL, License, File I/O.
"""

import re
from dataclasses import dataclass, field


@dataclass
class BugPattern:
    pattern: str
    category: str
    suggestion: str
    severity: str  # error, warning, info
    vivado_error_codes: list[str] = field(default_factory=list)


class BugDatabase:
    """Pattern-matching bug database for FPGA design issues.

    Maps Vivado error messages to root cause categories and repair suggestions.
    Used by LogParserAgent to enrich error analysis with actionable fixes.
    """

    def __init__(self):
        self.patterns: list[BugPattern] = self._build_patterns()

    def match(self, error_message: str) -> list[dict]:
        matches = []
        for entry in self.patterns:
            try:
                if re.search(entry.pattern, error_message, re.IGNORECASE):
                    matches.append({
                        "pattern": entry.pattern,
                        "category": entry.category,
                        "suggestion": entry.suggestion,
                        "severity": entry.severity,
                    })
            except re.error:
                pass
        return matches

    def match_by_code(self, error_code: str) -> list[dict]:
        """Match by Vivado error code (e.g. 'Synth 8-327')."""
        matches = []
        for entry in self.patterns:
            if error_code in entry.vivado_error_codes:
                matches.append({
                    "category": entry.category,
                    "suggestion": entry.suggestion,
                    "severity": entry.severity,
                })
        return matches

    @staticmethod
    def _build_patterns() -> list[BugPattern]:
        p = []

        # ═══════════════════════════════════════════════════════════════
        # 1. Synthesis (Synth 8-xxx)
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"Latch\s+inferred", "latch_inference",
            "Missing default assignment in combinational always block. Add 'else' clause or default assignment for all signals.",
            "warning", ["Synth 8-327"],
        ))
        p.append(BugPattern(
            r"inferred\s+latch", "inferred_latch",
            "Incomplete case/if statement. Add 'default' or 'else' clause for all branches.",
            "warning", ["Synth 8-327"],
        ))
        p.append(BugPattern(
            r"Multiple\s+driver", "multiple_driver",
            "Signal driven by multiple always blocks. Merge all assignments into one always block, or split into separate signals.",
            "error", ["Synth 8-685", "Synth 8-3355"],
        ))
        p.append(BugPattern(
            r"Combinational\s+loop", "combinational_loop",
            "Combinational feedback detected. Add a register stage or break the loop with a flop.",
            "error", ["Synth 8-338"],
        ))
        p.append(BugPattern(
            r"Unresolved\s+reference", "unresolved_reference",
            "Module instance or signal name not found. Check module name spelling, file include paths, and library mappings.",
            "error", ["Synth 8-3321", "Synth 8-3331"],
        ))
        p.append(BugPattern(
            r"Undefined\s+module", "undefined_module",
            "Module instantiated but not found in any source file. Check file list and module name.",
            "error", ["Synth 8-3321"],
        ))
        p.append(BugPattern(
            r"Port\s+(size|width|direction)\s+mismatch", "port_mismatch",
            "Module port width or direction mismatch. Check port declarations in module definition vs instantiation.",
            "error", ["Synth 8-693", "Synth 8-4352"],
        ))
        p.append(BugPattern(
            r"width\s+mismatch", "width_mismatch",
            "Signal width mismatch in assignment. Use 'WIDTH' parameter or resize signals.",
            "warning", ["Synth 8-4352"],
        ))
        p.append(BugPattern(
            r"Truncating\s+width", "truncation",
            "Wider signal assigned to narrower signal. Check bit widths or use explicit truncation.",
            "warning", ["Synth 8-348"],
        ))
        p.append(BugPattern(
            r"cannot\s+be\s+synthesized", "not_synthesizable",
            "Construct cannot be synthesized. Replace with synthesizable equivalent (e.g. remove #delay, use clock edge).",
            "error", ["Synth 8-3852"],
        ))
        p.append(BugPattern(
            r"not\s+a\s+constant", "not_constant",
            "Expected constant expression but got variable. Check parameter usage and generate statements.",
            "error", ["Synth 8-350"],
        ))
        p.append(BugPattern(
            r"Indexed\s+name", "indexed_name",
            "Array index out of bounds or invalid. Check array dimensions and index range.",
            "error", ["Synth 8-3936"],
        ))
        p.append(BugPattern(
            r"range\s+is\s+empty", "empty_range",
            "Vector range is empty (e.g. [0:-1]). Check parameter values and range direction.",
            "error", ["Synth 8-271"],
        ))
        p.append(BugPattern(
            r"cannot\s+be\s+resolved", "cannot_resolve",
            "Multiple drivers or ambiguous reference. Check for naming conflicts.",
            "error", ["Synth 8-3355"],
        ))
        p.append(BugPattern(
            r"multidriven\s+net", "multidriven_net",
            "Net has multiple drivers. Use dedicated signals or merge logic.",
            "error", ["Synth 8-685"],
        ))
        p.append(BugPattern(
            r"unused\s+output", "unused_output",
            "Output port is not connected anywhere. Check if the port is needed or add termination.",
            "info", ["Synth 8-601"],
        ))
        p.append(BugPattern(
            r"inferring\s+ROM", "inferred_rom",
            "ROM inferred from case statement. Ensure all cases are covered to avoid latch inference.",
            "info", [],
        ))
        p.append(BugPattern(
            r"inferring\s+DSP", "inferred_dsp",
            "DSP block inferred from multiply/accumulate. Check DSP usage for area optimization.",
            "info", [],
        ))
        p.append(BugPattern(
            r"register\s+is\s+inferred", "inferred_register",
            "Register inferred without explicit reset. Add reset branch for controlled initialization.",
            "info", [],
        ))
        p.append(BugPattern(
            r"Block\s+RAM\s+inferred", "inferred_bram",
            "Block RAM inferred from array. Check BRAM usage and power/area trade-offs.",
            "info", [],
        ))
        p.append(BugPattern(
            r"gated\s+clock", "gated_clock",
            "Clock gating detected. Use clock enable instead of gating for better timing.",
            "warning", ["Synth 8-694"],
        ))
        p.append(BugPattern(
            r"asynchronous\s+reset", "async_reset",
            "Asynchronous reset detected. Ensure reset deassertion is synchronized to avoid metastability.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"Black\s+Box", "black_box",
            "Module is treated as a black box (no source found). Provide RTL source or EDIF netlist.",
            "warning", ["Synth 8-3394"],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 2. Implementation (Place 30-xxx, Route 40-xxx, Phys 50-xxx)
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"Placement\s+failed", "placement_failed",
            "Placement could not find valid sites. Check I/O constraints, PLL locations, and die size.",
            "error", ["Place 30-574", "Place 30-380"],
        ))
        p.append(BugPattern(
            r"cannot\s+place\s+component", "cannot_place",
            "Component cannot be placed in the specified site. Check site constraints and resource availability.",
            "error", ["Place 30-574"],
        ))
        p.append(BugPattern(
            r"IO\s+bank\s+conflict", "io_bank_conflict",
            "I/O bank voltage standard conflict. Check IOSTANDARD constraints for same-bank pins.",
            "error", ["Place 30-603"],
        ))
        p.append(BugPattern(
            r"package\s+pins", "package_pins",
            "Package pin assignment conflict. Check XDC pin assignments against the package footprint.",
            "error", ["Place 30-472"],
        ))
        p.append(BugPattern(
            r"Routing\s+failed", "routing_failed",
            "Router could not complete all connections. Check congestion, logic density, and routing resources.",
            "error", ["Route 40-170"],
        ))
        p.append(BugPattern(
            r"congestion", "congestion",
            "Routing congestion detected. Reduce logic density, spread logic, or use a larger device.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"overlap", "route_overlap",
            "Routing resource overlap. Check for conflicting constraints or try different routing strategy.",
            "error", ["Route 40-171"],
        ))
        p.append(BugPattern(
            r"fanout", "high_fanout",
            "High fanout signal (>1000). Duplicate the register tree or use BUFG for clock distribution.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"hold\s+time", "hold_violation",
            "Hold time violation. Add delay in the data path or check clock skew constraints.",
            "error", ["Timing 12-346"],
        ))
        p.append(BugPattern(
            r"setup\s+time", "setup_violation",
            "Setup time violation. Reduce logic levels, increase clock period, add pipeline registers, or use multicycle paths.",
            "error", ["Timing 12-190"],
        ))
        p.append(BugPattern(
            r"Pulse\s+width", "pulse_width",
            "Pulse width violation. Check minimum pulse width constraints for the target device.",
            "error", ["Timing 12-275"],
        ))
        p.append(BugPattern(
            r"clock\s+skew", "clock_skew",
            "Excessive clock skew. Check clock tree synthesis, MMCM/PLL configuration, and clock region assignments.",
            "warning", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 3. Timing (Timing 12-xxx)
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"Timing\s+constraints\s+are\s+not\s+met", "timing_unmet",
            "Timing constraints are not met. Review the worst violation paths, reduce logic levels, adjust clock period, or add pipeline stages.",
            "error", ["Timing 12-190"],
        ))
        p.append(BugPattern(
            r"no\s+timing\s+constraints", "no_timing_constraints",
            "No timing constraints defined. Add create_clock, set_input_delay, set_output_delay to XDC.",
            "warning", ["Timing 12-228"],
        ))
        p.append(BugPattern(
            r"unconstrained\s+path", "unconstrained_path",
            "Path has no timing constraint. Add set_max_delay or set_false_path for the unconstrained path.",
            "warning", ["Timing 12-228"],
        ))
        p.append(BugPattern(
            r"async\s+clock\s+crossing", "async_clock_crossing",
            "Asynchronous clock crossing without synchronizer. Add 2-FF synchronizer or use set_false_path.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"clock\s+domain\s+crossing", "cdc_async",
            "Clock domain crossing detected. Use synchronizer flops, FIFO, or handshake for CDC paths.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"Recovery\s+removal", "recovery_removal",
            "Recovery/removal time violation on asynchronous reset. Check reset deassertion timing.",
            "error", ["Timing 12-346"],
        ))
        p.append(BugPattern(
            r"min\s+period", "min_period",
            "Minimum clock period violation. The clock period is too short for the target device speed grade.",
            "error", ["Timing 12-190"],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 4. XSim (XSIM 43-xxx)
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"X\s+propagation", "x_propagation",
            "X (unknown) value propagating in simulation. Check for uninitialized registers, unresolved inputs, or multi-driver conflicts.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Z\s+propagation", "z_propagation",
            "Z (high-impedance) value propagating. Check for undriven nets or tristate bus conflicts.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Simulation\s+failure", "sim_failure",
            "Simulation execution failed. Check for infinite loops, assertion failures, or runtime errors in testbench.",
            "error", ["XSIM 43-310"],
        ))
        p.append(BugPattern(
            r"Time\s+scale", "timescale",
            "Timescale mismatch between modules. Use `timescale directive or add timescale to all modules.",
            "warning", ["XSIM 43-4100"],
        ))
        p.append(BugPattern(
            r"no\s+timescale", "no_timescale",
            "Module missing timescale directive. Add `timescale 1ns/1ps to the file header.",
            "warning", ["XSIM 43-4100"],
        ))
        p.append(BugPattern(
            r"Assertion\s+failed", "assertion_failed",
            "Assertion failure in simulation. Check the assertion condition in the testbench or RTL assertion.",
            "error", [],
        ))
        p.append(BugPattern(
            r"\$fatal", "fatal_error",
            "\$fatal called in simulation. Review the simulation condition that triggered the fatal.",
            "error", [],
        ))
        p.append(BugPattern(
            r"UVM_ERROR", "uvm_error",
            "UVM error reported. Check the UVM component and sequence that reported the error.",
            "error", [],
        ))
        p.append(BugPattern(
            r"UVM_FATAL", "uvm_fatal",
            "UVM fatal error — simulation terminated. Review the UVM scoreboard and monitor.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Time\s+out", "sim_timeout",
            "Simulation timeout. Increase simulation runtime or check for infinite loops.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Reached\s+maximum\s+simulation\s+time", "sim_max_time",
            "Simulation reached configured maximum time. Increase runtime or check for unexpected long simulation.",
            "info", [],
        ))
        p.append(BugPattern(
            r"\$finish\s+called", "finish_called",
            "\$finish called in testbench. Normal simulation end.",
            "info", [],
        ))
        p.append(BugPattern(
            r"Elaboration\s+failure", "elab_failure",
            "Elaboration failed. Check for syntax errors, missing modules, or parameter mismatches.",
            "error", ["XSIM 43-310"],
        ))
        p.append(BugPattern(
            r"Compilation\s+failure", "compile_failure",
            "Compilation failed. Check for syntax errors in RTL or testbench files.",
            "error", ["XSIM 43-300"],
        ))
        p.append(BugPattern(
            r"cannot\s+open\s+library", "library_error",
            "Cannot open design library. Check library mapping in xsim.ini or recompile the library.",
            "error", ["XSIM 43-350"],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 5. UVM (Universal Verification Methodology)
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"UVM_WARNING", "uvm_warning",
            "UVM warning. Review the warning message; may indicate configuration issues.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"uvm_test_done", "uvm_test_done",
            "UVM test finished. Check drain time and objections to ensure complete coverage.",
            "info", [],
        ))
        p.append(BugPattern(
            r"sequence\s+not\s+configured", "uvm_seq_config",
            "UVM sequence not properly configured. Check sequencer and sequence configuration in the test.",
            "error", [],
        ))
        p.append(BugPattern(
            r"virtual\s+interface\s+not\s+set", "uvm_vif_not_set",
            "Virtual interface not set in the testbench. Ensure interface is properly connected in the test.",
            "error", [],
        ))
        p.append(BugPattern(
            r"scoreboard\s+mismatch", "uvm_scoreboard_mismatch",
            "UVM scoreboard comparison mismatch. Check expected vs actual data, timing, and ordering.",
            "error", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 6. DRC / Design Rule Checks
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"DRC\s+violation", "drc_violation",
            "Design Rule Check violation. Review the DRC report for specific violations.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Nets\s+with\s+multiple\s+drivers", "drc_multi_driver",
            "Net with multiple drivers detected by DRC. Check for unintended connections.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Floating\s+net", "drc_floating",
            "Floating (undriven) net detected. Check for unconnected ports or dangling wires.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"unconnected\s+port", "unconnected_port",
            "Module port is not connected. Check instantiation and add .*() or .port() connections.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"tristate\s+bus", "tristate_bus",
            "Tristate bus detected. Ensure only one driver is active at a time.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"clock\s+gating", "clock_gating_drc",
            "Clock gating detected by DRC. Use clock enable for better timing closure.",
            "warning", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 7. IP / Core
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"IP\s+not\s+generated", "ip_not_generated",
            "IP core output products not generated. Run 'generate_target all' on the IP.",
            "error", [],
        ))
        p.append(BugPattern(
            r"IP\s+version\s+mismatch", "ip_version_mismatch",
            "IP core version mismatch between design and repository. Upgrade the IP to the required version.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"License\s+not\s+found", "license_not_found",
            "License for the IP or feature not found. Check license server status and feature availability.",
            "error", [],
        ))
        p.append(BugPattern(
            r"out\s+of\s+context", "out_of_context_synth",
            "IP synthesized out-of-context. Ensure OOC synthesis results are available.",
            "warning", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 8. File / Project
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"File\s+not\s+found", "file_not_found",
            "Required file not found. Check file path and ensure the file exists.",
            "error", [],
        ))
        p.append(BugPattern(
            r"cannot\s+open\s+file", "cannot_open_file",
            "Cannot open file. Check file permissions and path.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Project\s+not\s+found", "project_not_found",
            "Vivado project not found. Check the .xpr file path.",
            "error", [],
        ))
        p.append(BugPattern(
            r"already\s+exists", "already_exists",
            "File or object already exists. Use -force flag to overwrite.",
            "info", [],
        ))
        p.append(BugPattern(
            r"Disk\s+quota", "disk_quota",
            "Disk quota exceeded or disk full. Free up disk space and retry.",
            "error", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 9. TCL Runtime
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"invalid\s+command\s+name", "invalid_tcl_command",
            "Invalid TCL command name. Check for typos or missing package imports.",
            "error", [],
        ))
        p.append(BugPattern(
            r"can't\s+read\s+\"", "tcl_var_not_found",
            "TCL variable not found. Check variable name spelling and scope.",
            "error", [],
        ))
        p.append(BugPattern(
            r"divide\s+by\s+zero", "tcl_div_zero",
            "Division by zero in TCL expression. Check the divisor value.",
            "error", [],
        ))
        p.append(BugPattern(
            r"out\s+of\s+range", "tcl_out_of_range",
            "TCL list index out of range. Check list length before accessing elements.",
            "error", [],
        ))
        p.append(BugPattern(
            r"can't\s+use\s+empty\s+string", "tcl_empty_string",
            "Empty string used in TCL expression. Check string initialization.",
            "error", [],
        ))
        p.append(BugPattern(
            r"memory\s+allocation", "tcl_memory",
            "Memory allocation failure in TCL. Reduce data size or increase available memory.",
            "error", [],
        ))
        p.append(BugPattern(
            r"stack\s+overflow", "tcl_stack_overflow",
            "TCL stack overflow due to deep recursion. Increase recursion limit or refactor to iterative.",
            "error", [],
        ))
        p.append(BugPattern(
            r"time\s+out", "tcl_timeout",
            "TCL execution timeout. Check for infinite loops or increase timeout limit.",
            "error", [],
        ))
        p.append(BugPattern(
            r"not\s+a\s+valid\s+property", "invalid_property",
            "Invalid property name. Use list_property to find valid properties for the target object.",
            "error", [],
        ))
        p.append(BugPattern(
            r"can\s+only\s+be\s+specified\s+once", "property_specified_once",
            "Property can only be specified once. Use set_property without -name/-value flags.",
            "error", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 10. Vivado Tool / Environment
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"Vivado\s+not\s+found", "vivado_not_found",
            "Vivado executable not found in PATH. Set VIVADO_PATH or add Vivado bin to PATH.",
            "error", [],
        ))
        p.append(BugPattern(
            r"version\s+mismatch", "version_mismatch",
            "Vivado version mismatch between project and tool. Upgrade project or use compatible version.",
            "error", [],
        ))
        p.append(BugPattern(
            r"not\s+a\s+valid\s+Vivado\s+project", "invalid_project",
            "Not a valid Vivado project (.xpr). Check the project path.",
            "error", [],
        ))
        p.append(BugPattern(
            r"license\s+checkout\s+failed", "license_checkout",
            "Feature license checkout failed. Check license server and feature availability.",
            "error", [],
        ))
        p.append(BugPattern(
            r"environment\s+variable\s+not\s+set", "env_var_not_set",
            "Required environment variable not set. Source the Vivado settings script (.sh/.bat).",
            "error", [],
        ))
        p.append(BugPattern(
            r"no\s+such\s+file\s+or\s+directory", "no_such_file",
            "File or directory not found. Check the path and ensure it exists.",
            "error", [],
        ))
        p.append(BugPattern(
            r"permission\s+denied", "permission_denied",
            "Permission denied. Check file/directory permissions or run as administrator.",
            "error", [],
        ))
        p.append(BugPattern(
            r"disk\s+full", "disk_full",
            "Disk is full. Free up disk space and retry the operation.",
            "error", [],
        ))
        p.append(BugPattern(
            r"signal\s+handler", "signal_handler",
            "Vivado received an unexpected signal (e.g. SIGSEGV). Check for memory issues or bugs.",
            "error", [],
        ))
        p.append(BugPattern(
            r"FATAL\s+ERROR", "fatal_error_tool",
            "Fatal Vivado error. Restart Vivado and retry. If persistent, check Vivado log for details.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Session\s+expired", "session_expired",
            "Vivado session expired or timed out. Restart the session.",
            "error", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 11. PetaLinux
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"petalinux-build\s+failed", "petalinux_build_failed",
            "PetaLinux build failed. Check the PetaLinux log for specific errors.",
            "error", [],
        ))
        p.append(BugPattern(
            r"device\s+tree\s+error", "dtb_error",
            "Device tree compilation error. Check dtsi/dts file syntax.",
            "error", [],
        ))
        p.append(BugPattern(
            r"rootfs\s+not\s+found", "rootfs_not_found",
            "Root filesystem not found. Check PetaLinux configuration and rebuild.",
            "error", [],
        ))
        p.append(BugPattern(
            r"u-boot\s+failure", "uboot_failure",
            "U-Boot build failure. Check U-Boot configuration and patches.",
            "error", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 12. Vitis HLS
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"unsupported\s+C\s+construct", "hls_unsupported_c",
            "Unsupported C/C++ construct for HLS. Replace with HLS-compatible syntax.",
            "error", [],
        ))
        p.append(BugPattern(
            r"interface\s+not\s+synthesizable", "hls_interface",
            "Interface type not synthesizable. Use HLS-compatible interfaces (ap_fifo, ap_memory, axis).",
            "error", [],
        ))
        p.append(BugPattern(
            r"loop\s+bound\s+not\s+constant", "hls_variable_loop",
            "Loop bound not constant — cannot fully unroll/pipeline. Use constant bounds or set max iterations.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"dataflow\s+deadlock", "hls_dataflow_deadlock",
            "Dataflow deadlock detected. Ensure FIFO depths are sufficient and all paths have data.",
            "error", [],
        ))
        p.append(BugPattern(
            r"pipeline\s+failure", "hls_pipeline_failure",
            "Pipeline cannot be achieved due to dependency. Break dependency chains or reduce initiation interval.",
            "warning", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 13. AI Engine / Versal
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"AIE\s+compilation\s+error", "aie_compile_error",
            "AI Engine compilation error. Check kernel code and graph connectivity.",
            "error", [],
        ))
        p.append(BugPattern(
            r"AIE\s+link\s+error", "aie_link_error",
            "AI Engine linking error. Check PLIO/GMIO connections between AIE tiles.",
            "error", [],
        ))
        p.append(BugPattern(
            r"Versal\s+PMC\s+error", "versal_pmc_error",
            "Versal PMC configuration error. Check PMC boot mode and configuration.",
            "error", [],
        ))
        p.append(BugPattern(
            r"NoC\s+bandwidth", "noc_bandwidth",
            "Network-on-Chip bandwidth exceeded. Check NoC traffic and adjust routing.",
            "warning", [],
        ))

        # ═══════════════════════════════════════════════════════════════
        # 14. Common Simulation Patterns
        # ═══════════════════════════════════════════════════════════════
        p.append(BugPattern(
            r"forever\s+.*\n(?!.*#)", "infinite_loop_no_delay",
            "forever loop without timing control. Add #delay, @(posedge clk), or wait() to prevent simulation hang.",
            "error", [],
        ))
        p.append(BugPattern(
            r"wait\s*\(.*\).*\n(?!.*@.*\w)", "wait_deadlock",
            "wait() statement without event trigger. Ensure the waited signal is driven.",
            "error", [],
        ))
        p.append(BugPattern(
            r"initial\s+begin\s*\n\s*forever", "initial_forever",
            "forever loop in initial block without timing control. Add clock edge or delay.",
            "error", [],
        ))
        p.append(BugPattern(
            r"#\d+\s+ms", "delay_ms",
            "Millisecond delay in simulation — very long simulation time. Use smaller time units or reduce delays.",
            "warning", [],
        ))
        p.append(BugPattern(
            r"while\s*\(1", "while_1",
            "while(1) loop without timing control. Add @(posedge clk) or #delay to prevent hang.",
            "error", [],
        ))
        p.append(BugPattern(
            r"repeat\s*\(.*\)\s*\n\s*@", "repeat_with_event",
            "repeat loop with event control — OK.",
            "info", [],
        ))
        p.append(BugPattern(
            r"fork\s*\n(?!.*disable\s+fork)", "fork_no_disable",
            "fork without disable fork. Add 'disable fork' to prevent process accumulation.",
            "warning", [],
        ))

        return p