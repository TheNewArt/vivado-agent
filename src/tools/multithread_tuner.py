import os
import re
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger("multithread_tuner")


class MultithreadTuner:
    """Auto-detect CPU resources and tune simulation parallelism."""

    def __init__(self, max_threads: int = 0, vivado_version: str = "2020.2"):
        self.max_threads = max_threads if max_threads > 0 else self._detect_cpu_count()
        self.vivado_version = vivado_version

    @staticmethod
    def _detect_cpu_count() -> int:
        try:
            return os.cpu_count() or 4
        except Exception:
            return 4

    def recommend_threads(self, design_scale: str = "medium") -> int:
        cpu = self.max_threads
        scale_map = {"small": 2, "medium": 4, "large": cpu, "huge": cpu}
        return min(scale_map.get(design_scale, 4), cpu)

    def estimate_design_scale(self, rtl_dir: str | Path) -> str:
        rtl_dir = Path(rtl_dir)
        if not rtl_dir.exists():
            return "medium"
        total_lines = 0
        for ext in ("*.v", "*.sv", "*.vhd"):
            total_lines += sum(
                len(f.read_text(encoding="utf-8", errors="replace").splitlines())
                for f in rtl_dir.rglob(ext) if f.is_file()
            )
        if total_lines < 5000:
            return "small"
        elif total_lines < 50000:
            return "medium"
        elif total_lines < 200000:
            return "large"
        return "huge"

    def generate_optimization_tcl(self, rtl_dir: str | Path, filesets: list[str] | None = None) -> str:
        scale = self.estimate_design_scale(rtl_dir)
        threads = self.recommend_threads(scale)
        logger.info(f"Design scale: {scale}, threads: {threads}")

        lines = [
            "# Performance tuning",
            f"# Vivado {self.vivado_version}, {threads} threads, design scale: {scale}",
            f"set_param general.maxThreads {threads}",
        ]
        return "\n".join(lines)