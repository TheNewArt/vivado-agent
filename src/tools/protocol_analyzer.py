"""Protocol-level signal analysis from VCD waveform data.

Detects AXI, AXI-Lite, and basic handshake patterns from signal snapshots.
Enables intelligent filter logic for wave trimming and debug targeting.
"""

import re
from dataclasses import dataclass, field


@dataclass
class AXI4Channel:
    """Represents a single AXI4 channel snapshot"""
    valid: bool = False
    ready: bool = False
    data: str = ""
    addr: str = ""
    id: str = ""
    last: bool = False
    strb: str = ""
    resp: str = ""


@dataclass
class AXI4Burst:
    address: int = 0
    length: int = 0
    size: int = 0
    burst_type: str = "INCR"
    data_words: list[str] = field(default_factory=list)
    complete: bool = False
    first_beat: float = 0.0
    last_beat: float = 0.0


class AXI4Analyzer:
    """Detect and decode AXI4 / AXI4-Lite transactions from signal traces."""

    # Common AXI signal name patterns
    AXI_SIGNS = {
        "AW": ["AWVALID", "AWREADY", "AWADDR", "AWID",
               "AWLEN", "AWSIZE", "AWBURST"],
        "W":  ["WVALID", "WREADY", "WDATA", "WLAST",
               "WSTRB"],
        "B":  ["BVALID", "BREADY", "BRESP", "BID"],
        "AR": ["ARVALID", "ARREADY", "ARADDR", "ARID",
               "ARLEN", "ARSIZE", "ARBURST"],
        "R":  ["RVALID", "RREADY", "RDATA", "RLAST",
               "RID", "RRESP"],
    }

    def __init__(self, name: str = ""):
        self.name = name
        self.channels: dict[str, dict[str, str]] = {}
        self.burst_history: list[AXI4Burst] = []

    def feed(self, signal_name: str, value: str, time_ns: float):
        """Feed a signal snapshot value for analysis."""
        # Normalize: strip hierarchy, keep last component
        short_name = signal_name.split("/")[-1].split(".")[-1]
        for prefix, signames in self.AXI_SIGNS.items():
            for s in signames:
                if short_name.upper() == s.upper() or \
                   short_name.upper().endswith("_" + s):
                    self.channels.setdefault(prefix, {})[s] = value
                    break

    def detect_write_transaction(self) -> dict:
        """Detect if a complete write transaction occurred."""
        aw = self.channels.get("AW", {})
        w  = self.channels.get("W", {})
        b  = self.channels.get("B", {})

        aw_valid = aw.get("AWVALID", "0") == "1" and aw.get("AWREADY", "0") == "1"
        w_valid  = w.get("WVALID", "0") == "1" and w.get("WREADY", "0") == "1"
        b_valid  = b.get("BVALID", "0") == "1" and b.get("BREADY", "0") == "1"

        if aw_valid and w_valid:
            return {
                "type": "WRITE",
                "addr": aw.get("AWADDR", "?"),
                "data": w.get("WDATA", "?"),
                "resp": b.get("BRESP", "?"),
                "complete": b_valid,
                "burst_len": int(aw.get("AWLEN", "0"), 2) if aw.get("AWLEN", "") else 0,
                "burst_size": int(aw.get("AWSIZE", "0"), 2) if aw.get("AWSIZE", "") else 0,
            }
        return {}

    def detect_read_transaction(self) -> dict:
        """Detect if a complete read transaction occurred."""
        ar = self.channels.get("AR", {})
        r  = self.channels.get("R", {})

        ar_valid = ar.get("ARVALID", "0") == "1" and ar.get("ARREADY", "0") == "1"
        r_valid  = r.get("RVALID", "0") == "1" and r.get("RREADY", "0") == "1"

        if ar_valid and r_valid:
            return {
                "type": "READ",
                "addr": ar.get("ARADDR", "?"),
                "data": r.get("RDATA", "?"),
                "resp": r.get("RRESP", "?"),
                "complete": r.get("RLAST", "0") == "1",
                "burst_len": int(ar.get("ARLEN", "0"), 2) if ar.get("ARLEN", "") else 0,
                "burst_size": int(ar.get("ARSIZE", "0"), 2) if ar.get("ARSIZE", "") else 0,
            }
        return {}

    def detect_handshake(self) -> list[dict]:
        """Return all detected handshake events."""
        events = []
        wt = self.detect_write_transaction()
        if wt:
            events.append(wt)
        rt = self.detect_read_transaction()
        if rt:
            events.append(rt)
        return events

    def get_relevant_signals(self) -> set[str]:
        """Return signal names that are AXI-related for waveform trimming."""
        sigs = set()
        for prefix, signames in self.AXI_SIGNS.items():
            for s in signames:
                sigs.add(s)
        return sigs


class ProtocolAnalyzer:
    """Aggregate protocol analyzers for multiple interfaces."""

    def __init__(self):
        self.analyzers: list[AXI4Analyzer] = []

    def add_axi(self, name: str = "axi") -> AXI4Analyzer:
        a = AXI4Analyzer(name)
        self.analyzers.append(a)
        return a

    def feed_all(self, signal_name: str, value: str, time_ns: float):
        for a in self.analyzers:
            a.feed(signal_name, value, time_ns)

    def detect_all_events(self) -> list[dict]:
        events = []
        for a in self.analyzers:
            events.extend(a.detect_handshake())
        return events

    def relevant_signals(self) -> set[str]:
        all_sigs: set[str] = set()
        for a in self.analyzers:
            all_sigs.update(a.get_relevant_signals())
        return all_sigs