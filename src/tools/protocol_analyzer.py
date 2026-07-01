"""Protocol-level signal analysis from VCD waveform data.

Detects AXI4/PCIe/UDP/SPI/I2C bus transaction patterns from signal snapshots.
Enables intelligent filter logic for wave trimming and debug targeting.
"""

import re
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────
# Common helpers
# ─────────────────────────────────────────────────────────────────

def _to_int(val: str) -> int:
    try:
        return int(val, 2) if val.startswith("'b") or all(c in "01xXzZ" for c in val) \
               else int(val, 16) if val.startswith("'h") or all(c in "0123456789abcdefABCDEFxXzZ" for c in val) \
               else int(val, 10) if val.isdigit() else 0
    except (ValueError, TypeError):
        return 0


# ─────────────────────────────────────────────────────────────────
# AXI4 / AXI4-Lite
# ─────────────────────────────────────────────────────────────────

@dataclass
class AXI4Channel:
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

    AXI_SIGNS = {
        "AW": ["AWVALID", "AWREADY", "AWADDR", "AWID",
               "AWLEN", "AWSIZE", "AWBURST", "AWLOCK",
               "AWCACHE", "AWPROT", "AWQOS", "AWREGION"],
        "W":  ["WVALID", "WREADY", "WDATA", "WLAST", "WSTRB"],
        "B":  ["BVALID", "BREADY", "BRESP", "BID"],
        "AR": ["ARVALID", "ARREADY", "ARADDR", "ARID",
               "ARLEN", "ARSIZE", "ARBURST", "ARLOCK",
               "ARCACHE", "ARPROT", "ARQOS", "ARREGION"],
        "R":  ["RVALID", "RREADY", "RDATA", "RLAST",
               "RID", "RRESP"],
    }

    def __init__(self, name: str = ""):
        self.name = name
        self.last_time: float = 0.0
        self.channels: dict[str, dict[str, str]] = {}
        self.burst_history: list[AXI4Burst] = []

    def feed(self, signal_name: str, value: str, time_ns: float):
        self.last_time = time_ns
        short = signal_name.split("/")[-1].split(".")[-1]
        for prefix, sigs in self.AXI_SIGNS.items():
            for s in sigs:
                if short.upper() == s.upper() or \
                   short.upper().endswith("_" + s):
                    self.channels.setdefault(prefix, {})[s] = value
                    return

    def detect_write_transaction(self) -> dict:
        aw = self.channels.get("AW", {})
        w  = self.channels.get("W", {})
        b  = self.channels.get("B", {})
        aw_ok = aw.get("AWVALID", "0") == "1" and aw.get("AWREADY", "0") == "1"
        w_ok  = w.get("WVALID", "0") == "1"  and w.get("WREADY", "0") == "1"
        if aw_ok and w_ok:
            return {
                "type": "AXI_WRITE",
                "addr": aw.get("AWADDR", "?"),
                "data": w.get("WDATA", "?"),
                "resp": b.get("BRESP", "?"),
                "burst_len": _to_int(aw.get("AWLEN", "0")),
                "burst_size": _to_int(aw.get("AWSIZE", "0")),
                "id": aw.get("AWID", "") or b.get("BID", ""),
            }
        return {}

    def detect_read_transaction(self) -> dict:
        ar = self.channels.get("AR", {})
        r  = self.channels.get("R", {})
        ar_ok = ar.get("ARVALID", "0") == "1" and ar.get("ARREADY", "0") == "1"
        r_ok  = r.get("RVALID", "0") == "1"  and r.get("RREADY", "0") == "1"
        if ar_ok and r_ok:
            return {
                "type": "AXI_READ",
                "addr": ar.get("ARADDR", "?"),
                "data": r.get("RDATA", "?"),
                "resp": r.get("RRESP", "?"),
                "burst_len": _to_int(ar.get("ARLEN", "0")),
                "burst_size": _to_int(ar.get("ARSIZE", "0")),
                "id": ar.get("ARID", "") or r.get("RID", ""),
            }
        return {}

    def detect_handshake(self) -> list[dict]:
        events = []
        for wt in [self.detect_write_transaction()]:
            if wt: events.append(wt)
        for rt in [self.detect_read_transaction()]:
            if rt: events.append(rt)
        return events

    def get_relevant_signals(self) -> set[str]:
        sigs = set()
        for prefix, signames in self.AXI_SIGNS.items():
            for s in signames:
                sigs.add(s)
        return sigs


# ─────────────────────────────────────────────────────────────────
# PCI Express (TLP header fields)
# ─────────────────────────────────────────────────────────────────

@dataclass
class PCIeTLP:
    fmt_type: int = 0
    length: int = 0
    requester_id: str = ""
    tag: str = ""
    address: int = 0
    data: list[str] = field(default_factory=list)
    is_memory_write: bool = False
    is_memory_read: bool = False
    is_completion: bool = False


class PCIeAnalyzer:
    """Detect PCIe TLP transaction patterns from signal traces."""

    PCIE_SIGNS = [
        "pci_exp_txp", "pci_exp_txn", "pci_exp_rxp", "pci_exp_rxn",
        "tlp_data", "tlp_valid", "tlp_ready",
        "completer_id", "requester_id", "tag",
        "memory_addr", "memory_data",
        "cfg_read", "cfg_write", "mem_read", "mem_write",
        "completion_done", "completion_data",
    ]

    def __init__(self, name: str = "pcie"):
        self.name = name
        self.mem_writes: int = 0
        self.mem_reads: int = 0
        self.completions: int = 0

    def feed(self, signal_name: str, value: str, time_ns: float):
        short = signal_name.split("/")[-1].lower()
        if "mem_write" in short and value == "1":
            self.mem_writes += 1
        if "mem_read" in short and value == "1":
            self.mem_reads += 1
        if "completion_done" in short and value == "1":
            self.completions += 1

    def detect_transactions(self) -> dict:
        return {
            "pcie_mem_writes": self.mem_writes,
            "pcie_mem_reads": self.mem_reads,
            "pcie_completions": self.completions,
        }

    def get_relevant_signals(self) -> set[str]:
        return set(self.PCIE_SIGNS)


# ─────────────────────────────────────────────────────────────────
# UDP / Ethernet
# ─────────────────────────────────────────────────────────────────

@dataclass
class UDPPacket:
    src_port: int = 0
    dst_port: int = 0
    length: int = 0
    data: str = ""


class UDPAnalyzer:
    """Detect UDP packet boundaries from RTL-level signals."""

    UDP_SIGNS = [
        "udp_tx_valid", "udp_tx_ready", "udp_tx_data", "udp_tx_last",
        "udp_tx_src_port", "udp_tx_dst_port", "udp_tx_length",
        "udp_rx_valid", "udp_rx_ready", "udp_rx_data", "udp_rx_last",
        "udp_rx_src_port", "udp_rx_dst_port", "udp_rx_length",
        "eth_tx_valid", "eth_tx_ready", "eth_tx_data", "eth_tx_last",
        "eth_rx_valid", "eth_rx_ready", "eth_rx_data", "eth_rx_last",
        "axis_tvalid", "axis_tready", "axis_tdata", "axis_tlast",
        "s_axis_tvalid", "s_axis_tready", "s_axis_tdata", "s_axis_tlast",
        "m_axis_tvalid", "m_axis_tready", "m_axis_tdata", "m_axis_tlast",
    ]

    def __init__(self, name: str = "udp"):
        self.name = name
        self.packet_count: int = 0

    def feed(self, signal_name: str, value: str, time_ns: float):
        short = signal_name.split("/")[-1].lower()
        # Detect AXI-Stream last signal assertion (end of packet)
        if short in ("axis_tlast", "udp_tx_last", "m_axis_tlast") and value == "1":
            self.packet_count += 1

    def detect_traffic(self) -> dict:
        return {"udp_packets": self.packet_count}

    def get_relevant_signals(self) -> set[str]:
        return set(self.UDP_SIGNS)


# ─────────────────────────────────────────────────────────────────
# SPI
# ─────────────────────────────────────────────────────────────────

class SPIAnalyzer:
    SPI_SIGNS = ["sclk", "mosi", "miso", "cs_n", "cs", "spi_clk", "spi_mosi", "spi_miso", "spi_cs"]

    def __init__(self, name: str = "spi"):
        self.name = name
        self.transactions: int = 0

    def feed(self, signal_name: str, value: str, time_ns: float):
        short = signal_name.split("/")[-1].lower()
        if short in ("cs_n", "cs", "spi_cs") and value == "1":
            self.transactions += 1

    def get_relevant_signals(self) -> set[str]:
        return set(self.SPI_SIGNS)


# ─────────────────────────────────────────────────────────────────
# I2C
# ─────────────────────────────────────────────────────────────────

class I2CAnalyzer:
    I2C_SIGNS = ["scl", "sda", "i2c_scl", "i2c_sda"]

    def __init__(self, name: str = "i2c"):
        self.name = name
        self.starts: int = 0
        self.stops: int = 0

    def feed(self, signal_name: str, value: str, time_ns: float):
        short = signal_name.split("/")[-1].lower()
        pass  # I2C start/stop detection requires edge detection not feasible from single snapshots

    def get_relevant_signals(self) -> set[str]:
        return set(self.I2C_SIGNS)


# ─────────────────────────────────────────────────────────────────
# Aggregate protocol analyzer
# ─────────────────────────────────────────────────────────────────

class ProtocolAnalyzer:
    """Aggregate protocol analyzers for multiple interfaces."""

    def __init__(self):
        self.analyzers: list = []
        self._axi: AXI4Analyzer | None = None
        self._pcie: PCIeAnalyzer | None = None
        self._udp: UDPAnalyzer | None = None
        self._spi: SPIAnalyzer | None = None
        self._i2c: I2CAnalyzer | None = None
        self._others: list = []

    def add_axi(self, name: str = "axi") -> AXI4Analyzer:
        self._axi = AXI4Analyzer(name)
        self.analyzers.append(self._axi)
        return self._axi

    def add_pcie(self, name: str = "pcie") -> PCIeAnalyzer:
        self._pcie = PCIeAnalyzer(name)
        self.analyzers.append(self._pcie)
        return self._pcie

    def add_udp(self, name: str = "udp") -> UDPAnalyzer:
        self._udp = UDPAnalyzer(name)
        self.analyzers.append(self._udp)
        return self._udp

    def add_spi(self, name: str = "spi") -> SPIAnalyzer:
        self._spi = SPIAnalyzer(name)
        self.analyzers.append(self._spi)
        return self._spi

    def add_i2c(self, name: str = "i2c") -> I2CAnalyzer:
        self._i2c = I2CAnalyzer(name)
        self.analyzers.append(self._i2c)
        return self._i2c

    def add_custom(self, analyzer) -> None:
        self._others.append(analyzer)
        self.analyzers.append(analyzer)

    def feed_all(self, signal_name: str, value: str, time_ns: float):
        for a in self.analyzers:
            a.feed(signal_name, value, time_ns)

    def detect_all_events(self) -> dict:
        events = {}
        if self._axi: events["axi"] = self._axi.detect_handshake()
        if self._pcie: events["pcie"] = self._pcie.detect_transactions()
        if self._udp: events["udp"] = self._udp.detect_traffic()
        for i, o in enumerate(self._others):
            events[f"custom_{i}"] = {}
        return events

    def relevant_signals(self) -> set[str]:
        all_sigs: set[str] = set()
        if self._axi: all_sigs |= self._axi.get_relevant_signals()
        if self._pcie: all_sigs |= self._pcie.get_relevant_signals()
        if self._udp: all_sigs |= self._udp.get_relevant_signals()
        if self._spi: all_sigs |= self._spi.get_relevant_signals()
        if self._i2c: all_sigs |= self._i2c.get_relevant_signals()
        for o in self._others:
            all_sigs |= getattr(o, "get_relevant_signals", lambda: set())()
        return all_sigs