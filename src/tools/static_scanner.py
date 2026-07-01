import re
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("static_scanner")


@dataclass
class ScanIssue:
    line: int
    severity: str  # error, warning, info
    category: str
    description: str
    suggestion: str | None = None
    snippet: str = ""


class StaticScanner:
    """Static scan testbench & RTL for simulation deadlock / infinite loop patterns."""

    def __init__(self):
        self.issues: list[ScanIssue] = []

    def scan_testbench(self, tb_path: str | Path) -> list[ScanIssue]:
        tb_path = Path(tb_path)
        if not tb_path.exists():
            logger.warning(f"Testbench not found: {tb_path}")
            return []

        text = tb_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        self.issues = []

        self._check_infinite_forever(lines)
        self._check_uninitialized_reset(lines)
        self._check_handshake_deadlock(lines)
        self._check_clock_gating(lines)
        self._check_fork_join_none(lines)

        for issue in self.issues:
            logger.info(f"[{issue.severity.upper()}] L{issue.line}: {issue.description}")

        return self.issues

    def _check_infinite_forever(self, lines: list[str]):
        """Detect forever without any #delay, @event, or wait statement."""
        in_forever = False
        forever_start = 0
        brace_depth = 0
        has_timing = False

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            if "forever" in stripped and not stripped.startswith("//") and not stripped.startswith("/*"):
                in_forever = True
                forever_start = i
                brace_depth = stripped.count("begin") - stripped.count("end")
                has_timing = bool(re.search(r"#\d+|@\(|wait\s*\(", stripped))

            elif in_forever:
                if not has_timing:
                    has_timing = bool(re.search(r"#\d+|@\(|wait\s*\(", stripped))
                brace_depth += stripped.count("begin") - stripped.count("end")
                if brace_depth <= 0 and ("end" in stripped or stripped == ""):
                    if not has_timing:
                        self.issues.append(ScanIssue(
                            line=forever_start,
                            severity="error",
                            category="infinite_loop",
                            description="forever loop without timing control — simulation will hang",
                            suggestion="Add #delay, @(posedge clk), or wait() inside the forever block",
                            snippet=lines[forever_start - 1] if forever_start <= len(lines) else "",
                        ))
                    in_forever = False

    def _check_uninitialized_reset(self, lines: list[str]):
        """Flag missing initial reset assertion before always_ff."""
        has_reset_check = False
        for i, line in enumerate(lines, 1):
            if re.search(r"@\(posedge\s+\w+\s+or\s+negedge\s+rst", line, re.IGNORECASE):
                has_reset_check = True
            if re.search(r"assert\s*\(.*rst", line, re.IGNORECASE):
                has_reset_check = True

        if not has_reset_check:
            for i, line in enumerate(lines, 1):
                if re.search(r"initial\s+begin", line, re.IGNORECASE):
                    self.issues.append(ScanIssue(
                        line=i,
                        severity="warning",
                        category="missing_reset",
                        description="No reset assertion found — simulation may start in X state",
                        suggestion="Add 'assert(rst)' or check reset initialization in initial block",
                        snippet=line,
                    ))
                    break

    def _check_handshake_deadlock(self, lines: list[str]):
        """Detect potential handshake deadlock: waiting for signal that's never asserted."""
        wait_pairs = []
        for i, line in enumerate(lines, 1):
            m = re.search(r"@\(posedge\s+(\w+)\)", line)
            if m:
                sig = m.group(1)
                if sig.lower() not in ("clk", "clock"):
                    wait_pairs.append((i, sig, "posedge"))

        for i, sig, _ in wait_pairs:
            driven = any(
                re.search(rf"\b{sig}\b\s*<=", l) or re.search(rf"\b{sig}\b\s*=", l)
                for l in lines
            )
            if not driven:
                self.issues.append(ScanIssue(
                    line=i,
                    severity="error",
                    category="handshake_deadlock",
                    description=f"Waiting for '{sig}' but it's never assigned — simulation will hang",
                    suggestion=f"Add driver for '{sig}' or check signal name spelling",
                    snippet=lines[i - 1],
                ))

    def _check_clock_gating(self, lines: list[str]):
        """Warn about gated clocks that might cause simulation mismatches."""
        for i, line in enumerate(lines, 1):
            if re.search(r"and\s*\(\s*\w+.*clk", line, re.IGNORECASE):
                self.issues.append(ScanIssue(
                    line=i,
                    severity="warning",
                    category="clock_gating",
                    description="Gated clock detected — may cause simulation/timing mismatches",
                    suggestion="Use clock enable instead of gating the clock directly",
                    snippet=line,
                ))

    def _check_fork_join_none(self, lines: list[str]):
        """Flag fork..join_none without disable fork — potential process leak."""
        for i, line in enumerate(lines, 1):
            if "join_none" in line:
                has_disable = any(
                    "disable fork" in l for l in lines[max(0, i - 10):i + 10]
                )
                if not has_disable:
                    self.issues.append(ScanIssue(
                        line=i,
                        severity="warning",
                        category="fork_join_none",
                        description="fork..join_none without disable fork — processes may accumulate",
                        suggestion="Add 'disable fork' before fork or use join/join_any",
                        snippet=line,
                    ))

    def has_critical_issues(self) -> bool:
        return any(i.severity == "error" for i in self.issues)