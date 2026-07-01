import re
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger("waveform_trimmer")


class WaveformTrimmer:
    """Intelligently select signals for waveform capture (Vivado 2020.2 compatible)."""

    def __init__(self):
        self.critical_signals: set[str] = set()
        self.assertion_signals: set[str] = set()
        self.io_signals: set[str] = set()

    def parse_testbench_assertions(self, tb_path: str | Path) -> list[str]:
        tb_path = Path(tb_path)
        if not tb_path.exists():
            return []
        text = tb_path.read_text(encoding="utf-8", errors="replace")
        signals = set()
        for pat in [
            r"assert\s*\(?\s*(\w+)",
            r"\$error\s*\(\s*\"[^\"]*\"\s*,\s*(\w+)",
            r"\$display\s*\(\s*\"[^\"]*\"\s*,\s*(\w+)",
        ]:
            for m in re.finditer(pat, text, re.IGNORECASE):
                signals.add(m.group(1))
        self.assertion_signals.update(signals)
        return list(signals)

    def find_io_signals(self, top_module: str, rtl_dir: str | Path) -> list[str]:
        rtl_dir = Path(rtl_dir)
        if not rtl_dir.exists():
            return []
        io_set = set()
        for ext in ("*.v", "*.sv"):
            for f in rtl_dir.rglob(ext):
                text = f.read_text(encoding="utf-8", errors="replace")
                if top_module in text:
                    for m in re.finditer(
                        r"(input|output|inout)\s+(\[\d+:\d+\])?\s*(\w+)",
                        text, re.IGNORECASE
                    ):
                        io_set.add(m.group(3))
        self.io_signals.update(io_set)
        return list(io_set)

    def find_clock_reset_signals(self, rtl_dir: str | Path) -> list[str]:
        rtl_dir = Path(rtl_dir)
        cr_set = {"clk", "clock", "rst", "reset", "rst_n", "rstn", "clk_i", "rst_i"}
        for ext in ("*.v", "*.sv", "*.vhd"):
            for f in rtl_dir.rglob(ext):
                text = f.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(
                    r"(always|process)\s*[\(@].*?(\w+).*?(posedge|negedge)",
                    text, re.IGNORECASE
                ):
                    cr_set.add(m.group(2))
        self.critical_signals.update(cr_set)
        return list(cr_set)

    def generate_log_wave_tcl(
        self,
        top_module: str,
        rtl_dir: str | Path,
        tb_path: str | Path | None = None,
        has_error: bool = False,
        vivado_version: str = "2020.2",
    ) -> str:
        """Generate log_wave TCL compatible with Vivado 2020.2 (no -depth, no -signal)."""
        clk_rst = self.find_clock_reset_signals(rtl_dir)
        io = self.find_io_signals(top_module, rtl_dir)
        signals_to_log = set(clk_rst + io)

        if tb_path:
            assertion_sigs = self.parse_testbench_assertions(tb_path)
            signals_to_log.update(assertion_sigs)

        lines = ["# Waveform trimming — batch mode (Vivado 2020.2 compatible)"]

        # In batch mode, waveform capture is controlled via properties, not log_wave
        lines.append("set_property xsim.simulate.log_all_signals false [get_filesets sim_1]")
        lines.append("set_property xsim.simulate.waveform_storage compact [get_filesets sim_1]")

        return "\n".join(lines)

    def generate_error_expansion_tcl(self, error_signals: list[str], window: int = 50) -> str:
        lines = ["# Error-triggered waveform expansion (Vivado 2020.2 compatible)"]
        for sig in error_signals:
            lines.append(f"log_wave {{{sig}}}")
        return "\n".join(lines)