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
    """Static analysis of testbench and RTL for simulation deadlocks,
    CDC issues, state machine hazards, and other common FPGA bugs."""

    PATTERNS = [
        # ── Deadlock / Infinite Loop ──
        ScanIssue(
            line=0, severity="error", category="infinite_loop",
            description="forever loop without timing control",
            suggestion="Add #delay, @(posedge clk), or wait() inside the forever block",
        ),
        ScanIssue(
            line=0, severity="error", category="handshake_deadlock",
            description="waiting for signal that is never assigned",
            suggestion="Add driver for the signal or check signal name spelling",
        ),
        ScanIssue(
            line=0, severity="warning", category="fork_join_none",
            description="fork..join_none without disable fork — processes may accumulate",
            suggestion="Add 'disable fork' before fork or use join/join_any",
        ),

        # ── Reset / Initialization ──
        ScanIssue(
            line=0, severity="warning", category="missing_reset",
            description="No reset assertion found — simulation may start in X state",
            suggestion="Add assert(rst) or check reset initialization in initial block",
        ),
        ScanIssue(
            line=0, severity="warning", category="uninitialized_register",
            description="Register without initial value or reset — starts as X",
            suggestion="Add initial value or reset branch in always_ff",
        ),
        ScanIssue(
            line=0, severity="warning", category="async_reset_no_meta",
            description="Asynchronous reset without metastability guard",
            suggestion="Add synchronizer flops for async reset deassertion",
        ),

        # ── Clock Domain Crossing (CDC) ──
        ScanIssue(
            line=0, severity="warning", category="cdc_no_sync",
            description="Signal crossing clock domains without synchronizer",
            suggestion="Add 2-FF synchronizer for each cross-domain signal",
        ),
        ScanIssue(
            line=0, severity="warning", category="cdc_multi_bit",
            description="Multi-bit signal crossing clock domains without handshake/DMUX",
            suggestion="Use handshake protocol, DMUX, or FIFO for multi-bit CDC",
        ),
        ScanIssue(
            line=0, severity="warning", category="clock_gating",
            description="Gated clock detected — may cause simulation/timing mismatches",
            suggestion="Use clock enable instead of gating the clock directly",
        ),

        # ── State Machine ──
        ScanIssue(
            line=0, severity="error", category="fsm_no_default",
            description="State machine case statement without default — may latch",
            suggestion="Add 'default: next_state = IDLE;' or similar safe state",
        ),
        ScanIssue(
            line=0, severity="warning", category="fsm_no_else",
            description="State machine next-state logic without else — open branch",
            suggestion="Add else clause for each state in next-state logic",
        ),
        ScanIssue(
            line=0, severity="warning", category="fsm_one_hot_no_assert",
            description="One-hot state machine without illegal state recovery",
            suggestion="Add assert/check for illegal state encoding",
        ),

        # ── Synthesis Issues ──
        ScanIssue(
            line=0, severity="error", category="inferred_latch",
            description="Incomplete if/case — latch inferred",
            suggestion="Add 'default' or 'else' clause for all branches",
        ),
        ScanIssue(
            line=0, severity="error", category="combinational_loop",
            description="Combinational feedback loop detected",
            suggestion="Add register stage or break the combinational loop",
        ),
        ScanIssue(
            line=0, severity="error", category="multiple_driver",
            description="Signal driven from multiple always blocks",
            suggestion="Merge all assignments into one always block",
        ),

        # ── Timing / Simulation ──
        ScanIssue(
            line=0, severity="warning", category="sim_time_overflow",
            description="Simulation time may overflow — check time variables",
            suggestion="Use 64-bit $time or limit simulation runtime",
        ),
        ScanIssue(
            line=0, severity="warning", category="delay_in_always",
            description="#delay in always block — not synthesizable",
            suggestion="Remove #delay for synthesis; use clock edge instead",
        ),
    ]

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

        self._check_infinite_forever(lines, text)
        self._check_uninitialized_reset(lines, text)
        self._check_handshake_deadlock(lines, text)
        self._check_fork_join_none(lines, text)
        self._check_state_machine(lines, text)
        self._check_clock_gating(lines, text)
        self._check_cdc(lines, text)
        self._check_inferred_latch(lines, text)
        self._check_delay_in_always(lines, text)

        if self.issues:
            logger.info(f"Static scan: {len(self.issues)} issues found")
            for iss in self.issues:
                logger.info(f"  [{iss.severity.upper()}] L{iss.line}: {iss.description}")
        else:
            logger.info("Static scan: clean — no issues found")

        return self.issues

    def scan_rtl(self, rtl_dir: str | Path) -> list[ScanIssue]:
        """Scan all RTL files in directory for design issues."""
        rtl_dir = Path(rtl_dir)
        issues = []
        for ext in ("*.v", "*.sv"):
            for f in rtl_dir.rglob(ext):
                text = f.read_text(encoding="utf-8", errors="replace")
                lines = text.splitlines()
                self._check_state_machine(lines, text, issues)
                self._check_clock_gating(lines, text, issues)
                self._check_cdc(lines, text, issues)
                self._check_inferred_latch(lines, text, issues)
                self._check_combinational_loop(lines, text, issues)
                self._check_uninitialized_register(lines, text, issues)
                self._check_multiple_driver(lines, text, issues)
        logger.info(f"RTL scan: {len(issues)} issues in {rtl_dir}")
        return issues

    # ── Individual checkers ──

    def _check_infinite_forever(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        in_forever = False
        forever_start = 0
        brace_depth = 0
        has_timing = False

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "forever" in stripped and not stripped.startswith("//") and "forever" not in text[max(0, text.find(stripped)-3):text.find(stripped)]:
                pass
            if "forever" in stripped and not stripped.startswith(("//", "/*")):
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
                        target.append(ScanIssue(
                            line=forever_start, severity="error", category="infinite_loop",
                            description="forever loop without timing control",
                            suggestion="Add #delay, @(posedge clk), or wait() inside forever",
                            snippet=lines[forever_start - 1] if forever_start <= len(lines) else "",
                        ))
                    in_forever = False

    def _check_uninitialized_reset(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        has_reset = bool(re.search(r"@\(posedge\s+\w+\s+or\s+negedge\s+rst", text, re.IGNORECASE))
        has_assert = bool(re.search(r"assert\s*\(.*rst", text, re.IGNORECASE))
        if not has_reset and not has_assert:
            for i, line in enumerate(lines, 1):
                if re.search(r"initial\s+begin", line, re.IGNORECASE):
                    target.append(ScanIssue(
                        line=i, severity="warning", category="missing_reset",
                        description="No reset assertion found",
                        suggestion="Add 'assert(rst)' or check reset initialization",
                        snippet=line,
                    ))
                    break

    def _check_uninitialized_register(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        for i, line in enumerate(lines, 1):
            if re.search(r"reg\s+.+;\s*$", line) and "=" not in line:
                if not any(f"rst" in l.lower() for l in lines[max(0,i-5):i+5]):
                    target.append(ScanIssue(
                        line=i, severity="warning", category="uninitialized_register",
                        description="Register without initial value or reset",
                        suggestion="Add '= 0' or reset branch in always_ff",
                        snippet=line,
                    ))

    def _check_handshake_deadlock(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        waits = []
        for i, line in enumerate(lines, 1):
            m = re.search(r"@\(posedge\s+(\w+)\)", line)
            if m and m.group(1).lower() not in ("clk", "clock"):
                waits.append((i, m.group(1)))
        for i, sig in waits:
            driven = any(
                re.search(rf"\b{sig}\b\s*<=", l) or re.search(rf"\b{sig}\b\s*=", l)
                for l in lines
            )
            if not driven:
                target.append(ScanIssue(
                    line=i, severity="error", category="handshake_deadlock",
                    description=f"Waiting for '{sig}' but never assigned",
                    suggestion=f"Add driver for '{sig}' or check spelling",
                    snippet=lines[i-1],
                ))

    def _check_fork_join_none(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        for i, line in enumerate(lines, 1):
            if "join_none" in line:
                nearby = "\n".join(lines[max(0,i-10):i+10])
                if "disable fork" not in nearby:
                    target.append(ScanIssue(
                        line=i, severity="warning", category="fork_join_none",
                        description="fork..join_none without disable fork",
                        suggestion="Add 'disable fork' before fork or use join/join_any",
                        snippet=line,
                    ))

    def _check_state_machine(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        in_case = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if re.search(r"case\s*\(", stripped) and not stripped.startswith("//"):
                in_case = True
            if in_case and "default:" in stripped:
                in_case = False
            if in_case and "endcase" in stripped:
                target.append(ScanIssue(
                    line=i, severity="error", category="fsm_no_default",
                    description="case without default — may latch",
                    suggestion="Add 'default:' clause",
                    snippet=stripped,
                ))
                in_case = False

    def _check_clock_gating(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        for i, line in enumerate(lines, 1):
            if re.search(r"and\s*\(\s*\w+.*clk", line, re.IGNORECASE):
                target.append(ScanIssue(
                    line=i, severity="warning", category="clock_gating",
                    description="Gated clock detected",
                    suggestion="Use clock enable instead of gating clock",
                    snippet=line,
                ))

    def _check_cdc(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        for m in re.finditer(r"@\(posedge\s+(\w+)\).*?(\w+)\s*<=", text, re.IGNORECASE):
            clk = m.group(1)
            sig = m.group(2)
            if clk.lower() not in ("clk", "clock"):
                # Signal assigned on non-primary clock — potential CDC
                for prev_m in re.finditer(rf"@\(posedge\s+(?!{clk}\b)(\w+)\).*?\b{sig}\b", text, re.IGNORECASE):
                    other_clk = prev_m.group(1)
                    if other_clk != clk:
                        # Check if there's a synchronizer
                        line_no = text[:m.start()].count('\n') + 1
                        if "synch" not in text[max(0,m.start()-100):m.end()+100].lower():
                            target.append(ScanIssue(
                                line=line_no, severity="warning", category="cdc_no_sync",
                                description=f"Signal '{sig}' crosses from {other_clk} to {clk} without sync",
                                suggestion="Add 2-FF synchronizer",
                                snippet=lines[line_no-1] if line_no <= len(lines) else "",
                            ))
                        break

    def _check_inferred_latch(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        for i, line in enumerate(lines, 1):
            if re.search(r"always\s*@\s*\([^)]*\)", line, re.IGNORECASE) and "posedge" not in line and "negedge" not in line:
                # Combinational always block — check for missing else/default
                block_end = i + 20
                block_text = "\n".join(lines[i:block_end])
                if "if" in block_text and "else" not in block_text:
                    target.append(ScanIssue(
                        line=i, severity="warning", category="inferred_latch",
                        description="Combinational always block with if but no else",
                        suggestion="Add else clause for all conditions",
                        snippet=line,
                    ))

    def _check_combinational_loop(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        assign_pattern = re.compile(r"assign\s+(\w+)\s*=")
        for m in assign_pattern.finditer(text):
            lhs = m.group(1)
            if lhs in text[m.end():m.end()+200]:
                line_no = text[:m.start()].count('\n') + 1
                target.append(ScanIssue(
                    line=line_no, severity="error", category="combinational_loop",
                    description=f"Signal '{lhs}' appears on both sides of assign",
                    suggestion="Add register stage to break the loop",
                    snippet=lines[line_no-1] if line_no <= len(lines) else "",
                ))

    def _check_multiple_driver(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        always_blocks = list(re.finditer(r"always\s*@", text, re.IGNORECASE))
        for i, m1 in enumerate(always_blocks):
            for m2 in always_blocks[i+1:]:
                block1 = text[m1.end():m2.start()]
                block2 = text[m2.end():m2.end()+200]
                sigs1 = set(re.findall(r"(\w+)\s*<=", block1))
                sigs2 = set(re.findall(r"(\w+)\s*<=", block2))
                overlap = sigs1 & sigs2
                if overlap:
                    for sig in overlap:
                        line_no = text[:m2.start()].count('\n') + 1
                        target.append(ScanIssue(
                            line=line_no, severity="error", category="multiple_driver",
                            description=f"Signal '{sig}' driven by multiple always blocks",
                            suggestion="Merge all assignments into one block",
                            snippet=lines[line_no-1] if line_no <= len(lines) else "",
                        ))

    def _check_delay_in_always(self, lines: list[str], text: str, target: list | None = None):
        target = target if target is not None else self.issues
        for i, line in enumerate(lines, 1):
            if re.search(r"always\s*@", line, re.IGNORECASE) and re.search(r"#\d+", line):
                target.append(ScanIssue(
                    line=i, severity="warning", category="delay_in_always",
                    description="#delay in always block — not synthesizable",
                    suggestion="Use clock edge for timing instead of #delay",
                    snippet=line,
                ))

    def has_critical_issues(self) -> bool:
        return any(i.severity == "error" for i in self.issues)