import subprocess
import tempfile
import os
import re
import time
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger("tcl_engine")


class TCLEngine:
    """Base layer: thin TCL execution wrapper. No intelligence."""

    def __init__(self, vivado_path: str = "vivado", mode: str = "batch", timeout: int = 3600):
        self.vivado_path = vivado_path
        self.mode = mode
        self.timeout = timeout

    def run_script(self, tcl_commands: str | list[str], workdir: str | Path | None = None) -> dict:
        """Execute TCL commands in Vivado batch mode. Returns {stdout, stderr, returncode}."""
        if isinstance(tcl_commands, list):
            tcl_commands = "\n".join(tcl_commands)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tcl", delete=False, encoding="utf-8"
        ) as f:
            f.write(tcl_commands)
            script_path = f.name

        try:
            cmd = [
                self.vivado_path,
                "-mode", self.mode,
                "-source", script_path,
                "-nojournal",
                "-log", self._log_path("vivado"),
            ]
            logger.info(f"Executing: {' '.join(cmd[:4])} ... -source {script_path}")
            start = time.time()

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(workdir) if workdir else None,
            )

            elapsed = time.time() - start
            logger.info(f"Vivado finished in {elapsed:.1f}s (rc={proc.returncode})")

            if proc.returncode != 0:
                logger.warning(f"TCL script finished with non-zero: {proc.returncode}")

            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
                "elapsed": elapsed,
            }
        except subprocess.TimeoutExpired:
            logger.error(f"Vivado timed out after {self.timeout}s")
            return {"stdout": "", "stderr": "Timeout", "returncode": -1, "elapsed": self.timeout}
        finally:
            os.unlink(script_path)

    def run_tcl_safe(self, tcl_command: str, **kwargs) -> dict:
        """Safely run a single TCL command; returns formatted result."""
        return self.run_script(tcl_command.strip(), **kwargs)

    @staticmethod
    def _log_path(base: str) -> str:
        ts = time.strftime("%Y%m%d_%H%M%S")
        return f"{base}_{ts}.log"

    @staticmethod
    def extract_errors(log: str) -> list[dict]:
        errors = []
        patterns = [
            (r"ERROR:\s*\[[^\]]+\]\s*(.*)", "vivado_error"),
            (r"CRITICAL WARNING:\s*\[[^\]]+\]\s*(.*)", "critical_warning"),
            (r"Time violation:\s*(.*)", "timing_violation"),
            (r"Latch inferred.*?(line\s+\d+)", "latch_inference"),
        ]
        for pattern, etype in patterns:
            for m in re.finditer(pattern, log, re.IGNORECASE):
                errors.append({"type": etype, "message": m.group(0).strip(), "detail": m.group(1).strip()})
        return errors

    @staticmethod
    def extract_elapsed(log: str) -> float | None:
        m = re.search(r"Finished (.+?) in (\d+\.?\d*)s", log)
        if m:
            return float(m.group(2))
        return None