"""Auto-generate timing constraints from timing report analysis.

Reads Vivado timing report, identifies worst violation paths,
and generates SDC/XDC constraints to fix them.
"""

import re
from dataclasses import dataclass, field


@dataclass
class TimingPath:
    slack: float
    path_type: str  # setup, hold, recovery, removal
    from_reg: str
    to_reg: str
    from_clk: str
    to_clk: str
    logic_levels: int
    fanout: int
    data_path_delay: float = 0.0
    clock_skew: float = 0.0


class TimingConstraintGenerator:
    """Generate SDC constraints from timing analysis."""

    def __init__(self, clock_period_ns: float = 10.0):
        self.clock_period_ns = clock_period_ns
        self.generated_constraints: list[str] = []

    def parse_timing_report(self, report_text: str) -> list[TimingPath]:
        """Parse Vivado timing report and extract violation paths."""
        paths = []
        # Parse setup timing paths
        current_path = None
        for line in report_text.splitlines():
            # Slack
            m = re.search(r"Slack\s*:\s*([-\d.]+)", line)
            if m:
                if current_path and current_path.slack < 0:
                    paths.append(current_path)
                current_path = TimingPath(
                    slack=float(m.group(1)),
                    path_type="setup",
                    from_reg="", to_reg="",
                    from_clk="", to_clk="",
                    logic_levels=0, fanout=0,
                )
            if not current_path:
                continue

            m = re.search(r"From\s*:\s*(\S+)", line)
            if m: current_path.from_reg = m.group(1)
            m = re.search(r"To\s*:\s*(\S+)", line)
            if m: current_path.to_reg = m.group(1)
            m = re.search(r"From Clock\s*:\s*(\S+)", line)
            if m: current_path.from_clk = m.group(1)
            m = re.search(r"To Clock\s*:\s*(\S+)", line)
            if m: current_path.to_clk = m.group(1)
            m = re.search(r"Logic Levels\s*:\s*(\d+)", line)
            if m: current_path.logic_levels = int(m.group(1))
            m = re.search(r"Fanout\s*:\s*(\d+)", line)
            if m: current_path.fanout = int(m.group(1))
            m = re.search(r"Data Path Delay\s*:\s*([-\d.]+)", line)
            if m: current_path.data_path_delay = float(m.group(1))
            m = re.search(r"Clock Skew\s*:\s*([-\d.]+)", line)
            if m: current_path.clock_skew = float(m.group(1))

        if current_path and current_path.slack < 0:
            paths.append(current_path)

        return paths

    def generate_constraints(self, paths: list[TimingPath]) -> list[str]:
        """Generate SDC constraints for worst violation paths."""
        constraints = []
        constraints.append("# Auto-generated timing constraints")
        constraints.append(f"# Generated from {len(paths)} violation paths")
        constraints.append("")

        # Group by violation type
        setup_paths = [p for p in paths if p.path_type == "setup" and p.slack < 0]
        hold_paths = [p for p in paths if p.path_type == "hold" and p.slack < 0]

        if not setup_paths and not hold_paths:
            constraints.append("# No timing violations found")
            return constraints

        # 1) Handle setup violations
        if setup_paths:
            worst = min(setup_paths, key=lambda p: p.slack)
            constraints.append("# ----------------------------------------")
            constraints.append(f"# Setup violations: {len(setup_paths)} paths")
            constraints.append(f"# Worst slack: {worst.slack:.3f}ns")
            constraints.append(f"#   {worst.from_reg} -> {worst.to_reg}")
            constraints.append(f"#   Logic levels: {worst.logic_levels}")
            constraints.append(f"#   Fanout: {worst.fanout}")
            constraints.append("# ----------------------------------------")

            # Strategy 1: Multicycle path for slow paths
            if worst.logic_levels > 20:
                constraints.append("")
                constraints.append("# Multicycle: path requires >20 logic levels")
                constraints.append(
                    f"set_multicycle_path -setup 2 -from [get_cells {worst.from_reg}] "
                    f"-to [get_cells {worst.to_reg}]"
                )
                constraints.append(
                    f"set_multicycle_path -hold 1 -from [get_cells {worst.from_reg}] "
                    f"-to [get_cells {worst.to_reg}]"
                )

            # Strategy 2: False path for cross-clock (if applicable)
            if worst.from_clk and worst.to_clk and worst.from_clk != worst.to_clk:
                constraints.append("")
                constraints.append("# False path: cross-clock domain")
                constraints.append(
                    f"set_false_path -from [get_clocks {worst.from_clk}] "
                    f"-to [get_clocks {worst.to_clk}]"
                )

            # Strategy 3: Pipeline registers for high-fanout
            if worst.fanout > 1000:
                constraints.append("")
                constraints.append("# Max delay: high fanout path")
                target = worst.to_reg.split("/")[0] if "/" in worst.to_reg else worst.to_reg
                constraints.append(
                    f"set_max_delay -from [get_cells {worst.from_reg}] "
                    f"-to [get_cells {target}] {self.clock_period_ns * 0.8:.1f}"
                )

        # 2) Handle hold violations
        if hold_paths:
            constraints.append("")
            constraints.append("# ----------------------------------------")
            constraints.append(f"# Hold violations: {len(hold_paths)} paths")
            constraints.append("# ----------------------------------------")
            # Hold violations typically need delay insertion
            constraints.append(
                "# Hold violations: add delay buffers in the data path"
            )

        # 3) General constraints
        constraints.append("")
        constraints.append("# General constraints")
        constraints.append(
            f"set_clock_uncertainty {max(0.05, self.clock_period_ns * 0.02):.3f} "
            "[all_clocks]"
        )

        self.generated_constraints = constraints
        return constraints

    def generate_xdc(self, output_path: str = "auto_fix_constraints.xdc") -> str:
        """Write generated constraints to XDC file."""
        text = "\n".join(self.generated_constraints)
        with open(output_path, "w") as f:
            f.write(text)
        return text

    @staticmethod
    def analyze_timing_violations(paths: list[TimingPath]) -> dict:
        """High-level summary of timing issues."""
        if not paths:
            return {"status": "clean", "total_paths": 0}

        setup = [p for p in paths if p.path_type == "setup"]
        hold = [p for p in paths if p.path_type == "hold"]

        return {
            "status": "violations",
            "total_paths": len(paths),
            "setup_violations": len(setup),
            "hold_violations": len(hold),
            "worst_setup_slack": min((p.slack for p in setup), default=0.0),
            "worst_hold_slack": min((p.slack for p in hold), default=0.0),
            "avg_logic_levels": (
                sum(p.logic_levels for p in paths) / len(paths)
                if paths else 0
            ),
            "avg_fanout": (
                sum(p.fanout for p in paths) / len(paths)
                if paths else 0
            ),
        }