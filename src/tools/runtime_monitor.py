import re
import time
import threading
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("runtime_monitor")


@dataclass
class SimEvent:
    timestamp_ns: float
    severity: str
    message: str
    source: str = ""
    signal: str = ""
    value: str = ""


@dataclass
class XPropagation:
    first_x_time_ns: float
    first_x_signal: str
    propagation_chain: list[dict]
    x_signals: set[str]


class SimulationMonitor:
    """Real-time monitor: X/Z tracing, signal sampling, assertion auto-stop."""

    X_PATTERNS = [
        (r"('h?[0-9a-fA-F]*[xX]+[0-9a-fA-F]*)", "hex_x"),
        (r"('b[01]*[xXzZ]+[01]*)", "binary_xz"),
        (r"(\w+(?:/\w+)*)\s*=\s*[']?\s*[0-9a-fA-F]*[xXzZ]", "signal_xz"),
    ]

    TIMEOUT_PATTERNS = [
        r"#\d+\s*(ms|us|ns|ps)\s*$",
        r"Timeout\s+occurred",
        r"Reached\s+maximum\s+simulation\s+time",
    ]

    ASSERT_FAIL_PATTERNS = [
        r"(assertion|assert)\s+(failed|error|violated)",
        r"\$error\s*\(.*assert",
        r"\$fatal\s*\(",
        r"Assertion\s+(\w+)\s+failed",
        r"UVM_(ERROR|FATAL)",
    ]

    def __init__(self, log_path: str | Path | None = None):
        self.log_path = Path(log_path) if log_path else None
        self.events: list[SimEvent] = []
        self.x_propagation: XPropagation | None = None
        self.assertions_failed: list[SimEvent] = []
        self.signal_samples: dict[str, list[tuple[float, str]]] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._position = 0
        self._stop_on_assert = False
        self._stop_on_x = False
        self._stop_callback = None

    def start_monitoring(
        self,
        log_path: str | Path | None = None,
        stop_on_assert: bool = True,
        stop_on_x: bool = True,
        stop_callback=None,
    ):
        """Start tailing the simulation log with triggers."""
        if log_path:
            self.log_path = Path(log_path)
        if not self.log_path or not self.log_path.exists():
            logger.warning(f"Log not found: {self.log_path}")
            return

        self._stop_on_assert = stop_on_assert
        self._stop_on_x = stop_on_x
        self._stop_callback = stop_callback
        self._running = True
        self._position = self.log_path.stat().st_size
        self._thread = threading.Thread(target=self._tail_log, daemon=True)
        self._thread.start()
        logger.info(f"Monitoring started: {self.log_path}")
        logger.info(f"  Triggers: assert-stop={stop_on_assert}, X-stop={stop_on_x}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info(f"Monitor stopped: {len(self.events)} events, {len(self.assertions_failed)} assertions")

    def _tail_log(self):
        while self._running:
            try:
                current_size = self.log_path.stat().st_size
                if current_size > self._position:
                    with open(self.log_path, encoding="utf-8", errors="replace") as f:
                        f.seek(self._position)
                        for line in f:
                            self._analyze_line(line.strip())
                    self._position = f.tell()
                time.sleep(0.3)
            except Exception as e:
                logger.debug(f"Monitor tail error: {e}")
                time.sleep(1)

    def _analyze_line(self, line: str):
        if not line:
            return

        # X/Z detection + propagation tracking
        for pat, ptype in self.X_PATTERNS:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                ts = self._extract_time(line)
                signal_name = ""
                value_str = m.group(0)
                if ptype == "signal_xz":
                    signal_name = m.group(1)
                self.events.append(SimEvent(
                    timestamp_ns=ts,
                    severity="error",
                    message=f"X/Z: {' '.join(line.split()[:8])}",
                    source="XZ_monitor",
                    signal=signal_name,
                    value=value_str,
                ))
                self._track_x_propagation(ts, signal_name or m.group(0))
                logger.warning(f"[X/Z @{ts:.0f}ns] {line[:120]}")
                if self._stop_on_x and self._stop_callback:
                    self._stop_callback("X/Z propagation detected")
                break

        # Timeout detection
        for pat in self.TIMEOUT_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                ts = self._extract_time(line)
                self.events.append(SimEvent(
                    timestamp_ns=ts, severity="error",
                    message=line[:200], source="timeout",
                ))
                logger.warning(f"[TIMEOUT @{ts:.0f}ns] {line[:120]}")
                if self._stop_callback:
                    self._stop_callback("Simulation timeout")
                break

        # Assertion failure detection
        for pat in self.ASSERT_FAIL_PATTERNS:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                ts = self._extract_time(line)
                event = SimEvent(
                    timestamp_ns=ts, severity="error",
                    message=line[:300], source="assertion_fail",
                )
                self.events.append(event)
                self.assertions_failed.append(event)
                logger.error(f"[ASSERT FAIL @{ts:.0f}ns] {line[:150]}")
                if self._stop_on_assert and self._stop_callback:
                    self._stop_callback(f"Assertion failed at {ts:.0f}ns")
                break

    def _track_x_propagation(self, ts: float, signal_or_val: str):
        if self.x_propagation is None:
            self.x_propagation = XPropagation(
                first_x_time_ns=ts,
                first_x_signal=signal_or_val,
                propagation_chain=[{"time_ns": ts, "signal": signal_or_val}],
                x_signals={signal_or_val},
            )
        else:
            if signal_or_val not in self.x_propagation.x_signals:
                self.x_propagation.propagation_chain.append({
                    "time_ns": ts,
                    "signal": signal_or_val,
                })
                self.x_propagation.x_signals.add(signal_or_val)

    def sample_signal(self, signal_path: str, tcl_engine=None) -> str:
        """Generate TCL to sample a specific signal value at current time."""
        sp = signal_path
        return f"""
set sig_val [read_wave_value {{{sp}}}]
puts "SAMPLE:{sp}=$sig_val"
"""

    def get_events(self, severity: str | None = None) -> list[SimEvent]:
        if severity:
            return [e for e in self.events if e.severity == severity]
        return self.events

    def has_errors(self) -> bool:
        return any(e.severity == "error" for e in self.events)

    def has_assertions_failed(self) -> bool:
        return len(self.assertions_failed) > 0

    def get_x_report(self) -> str:
        if not self.x_propagation:
            return "No X/Z propagation detected."
        xp = self.x_propagation
        lines = [
            "=== X/Z Propagation Report ===",
            f"First occurrence: @{xp.first_x_time_ns:.0f}ns",
            f"First signal: {xp.first_x_signal}",
            f"Affected signals: {len(xp.x_signals)}",
            "Propagation chain:",
        ]
        for entry in xp.propagation_chain[:15]:
            lines.append(f"  @{entry['time_ns']:.0f}ns  {entry['signal']}")
        if len(xp.propagation_chain) > 15:
            lines.append(f"  ... +{len(xp.propagation_chain)-15} more")
        return "\n".join(lines)

    def get_assertion_report(self) -> str:
        if not self.assertions_failed:
            return "No assertion failures detected."
        lines = [f"=== Assertion Failures ({len(self.assertions_failed)}) ==="]
        for i, ev in enumerate(self.assertions_failed, 1):
            lines.append(f"  #{i} @{ev.timestamp_ns:.0f}ns: {ev.message[:120]}")
        return "\n".join(lines)

    def report(self) -> str:
        if not self.events:
            return "No issues detected during simulation — simulation appears clean."
        parts = [f"=== Simulation Monitor Report ({len(self.events)} events) ==="]
        parts.append(self.get_assertion_report())
        parts.append(self.get_x_report())
        # General events
        other = [e for e in self.events if e.source not in ("assertion_fail", "XZ_monitor")]
        if other:
            parts.append(f"Other events ({len(other)}):")
            for e in other[:5]:
                parts.append(f"  [{e.source} @{e.timestamp_ns:.0f}ns] {e.message[:120]}")
        return "\n".join(parts)

    @staticmethod
    def generate_auto_stop_tcl(window_ns: float = 500.0) -> str:
        """Generate TCL to auto-stop on assertion failure and dump waveform."""
        return f"""
# Auto-stop on assertion failure
set on_assert_fail {{1}}
set assert_fail_action {{
    puts "ASSERTION FAILED at [current_time]"
    stop
    log_wave -depth 5 -signal /*
    run {{{window_ns}}}ns
    write_waveform -force assertion_fault_{{.wdb}}
    puts "Waveform saved to assertion_fault.wdb"
}}
when {{$assertion_failed}} $assert_fail_action

# Auto-stop on X/Z
set on_x_detected {{1}}
set x_action {{
    set x_sig [lindex [read_wave_value -all -filter {{value =~ /[xXzZ]/}}] 0]
    puts "X/Z DETECTED at [current_time] on $x_sig"
    stop
    log_wave -depth 3 -signal $x_sig
    run 100ns
    write_waveform -force x_propagation_fault.wdb
    puts "Waveform saved to x_propagation_fault.wdb"
}}
when {{[string match "*x*" [read_wave_value -all -hex]]}} $x_action
"""