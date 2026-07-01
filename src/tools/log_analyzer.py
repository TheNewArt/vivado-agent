import re
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("log_analyzer")


@dataclass
class LogError:
    category: str
    severity: str
    line_no: int | None
    message: str
    suggestion: str | None = None


@dataclass
class TimingViolation:
    slack: float
    path_type: str
    from_reg: str
    to_reg: str
    logic_levels: int
    fanout: int


class LogAnalyzer:
    """Parse and categorize Vivado tool errors from logs."""

    ERROR_PATTERNS = {
        "latch_inference": (r"Latch\s+inferred.*?(line\s+\d+)", "warning"),
        "combinational_loop": (r"Combinational\s+loop.*?(line\s+\d+)", "error"),
        "CDC_violation": (r"Crossing\s+clock\s+domain|CDC\s+violation", "warning"),
        "timing_setup": (r"Timing.*?Setup.*?violation", "error"),
        "timing_hold": (r"Timing.*?Hold.*?violation", "error"),
        "inferred_latch": (r"inferred\s+latch.*?(line\s+\d+)", "warning"),
        "multiple_driver": (r"Multiple\s+driver.*?(line\s+\d+)", "error"),
        "port_mismatch": (r"port\s+(size|width|direction)\s+mismatch", "error"),
        "unresolved_reference": (r"(Unresolved|Undefined)\s+reference", "error"),
        "elaboration_failure": (r"Elaboration\s+failure", "error"),
        "xilinx_known_bug": (r"(Xilinx|Vivado)\s+(Bug|Known\s+Issue)", "warning"),
        "simulation_failure": (r"Simulation\s+(failure|error|fatal)", "error"),
    }

    SUGGESTIONS = {
        "latch_inference": "Missing default assignment in combinational always block; add 'else' or default",
        "combinational_loop": "Combinational feedback detected; add register stage or break the loop",
        "CDC_violation": "Cross-clock domain signal without synchronizer; add 2-FF synchronizer",
        "timing_setup": "Setup time violation; reduce logic levels (~{levels}), increase clock period, or add pipeline",
        "timing_hold": "Hold time violation; add delay buffers or check clock skew",
        "inferred_latch": "Incomplete case/if statement; add 'default' or 'else' clause",
        "multiple_driver": "Signal driven from multiple always blocks; merge drivers or separate signals",
        "port_mismatch": "Module port width/type mismatch; check port declarations",
        "unresolved_reference": "Module or signal not found; check file inclusion and module names",
        "elaboration_failure": "Design elaboration failed; check syntax and hierarchy",
        "simulation_failure": "Simulation execution error; check runtime assertions and X-propagation",
        "default": "Review the error context in log for details",
    }

    def parse_errors(self, log_text: str) -> list[LogError]:
        errors = []
        lines = log_text.splitlines()

        for i, line in enumerate(lines, 1):
            for category, (pattern, severity) in self.ERROR_PATTERNS.items():
                m = re.search(pattern, line, re.IGNORECASE)
                if not m:
                    continue

                # Extract line number if present
                line_match = re.search(r"line\s+(\d+)", line, re.IGNORECASE)
                line_no = int(line_match.group(1)) if line_match else i

                suggestion = self.SUGGESTIONS.get(category, self.SUGGESTIONS["default"])
                if "{levels}" in suggestion:
                    levels = self._extract_logic_levels(line)
                    suggestion = suggestion.replace("{levels}", str(levels))

                errors.append(LogError(
                    category=category,
                    severity=severity,
                    line_no=line_no,
                    message=line.strip()[:200],
                    suggestion=suggestion,
                ))

        # Deduplicate near-identical errors
        seen = set()
        unique = []
        for e in errors:
            key = (e.category, e.line_no, e.message[:80])
            if key not in seen:
                seen.add(key)
                unique.append(e)
        return unique

    def extract_timing_violations(self, log_text: str) -> list[TimingViolation]:
        violations = []
        for m in re.finditer(
            r"Slack\s*:\s*(-?\d+\.?\d*).*?From\s*:\s*(\S+).*?To\s*:\s*(\S+).*?"
            r"Logic\s+Levels\s*:\s*(\d+).*?Fanout\s*:\s*(\d+)",
            log_text, re.DOTALL | re.IGNORECASE
        ):
            violations.append(TimingViolation(
                slack=float(m.group(1)),
                path_type="setup",
                from_reg=m.group(2),
                to_reg=m.group(3),
                logic_levels=int(m.group(4)),
                fanout=int(m.group(5)),
            ))
        # Return worst 10
        violations.sort(key=lambda v: v.slack)
        return violations[:10]

    @staticmethod
    def _extract_logic_levels(line: str) -> int:
        m = re.search(r"logic\s+level[s]?\s*:\s*(\d+)", line, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    def summarize(self, errors: list[LogError], timing: list[TimingViolation]) -> str:
        if not errors and not timing:
            return "No errors or timing violations found."
        parts = []
        by_cat = {}
        for e in errors:
            by_cat.setdefault(e.category, []).append(e)
        for cat, items in sorted(by_cat.items()):
            parts.append(f"{cat}: {len(items)} occurrence(s)")
            for item in items[:3]:
                parts.append(f"  L{item.line_no}: {item.message[:100]}")
                if item.suggestion:
                    parts.append(f"  -> Fix: {item.suggestion}")
        if timing:
            parts.append(f"Timing violations: {len(timing)} worst paths")
            for t in timing[:3]:
                parts.append(f"  Slack={t.slack:.2f}ns  {t.from_reg} -> {t.to_reg}  levels={t.logic_levels}")
        return "\n".join(parts)