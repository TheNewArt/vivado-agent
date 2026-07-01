import subprocess
import tempfile
import os
import re
import time
import threading
import shutil
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger("tcl_engine")


class TCLSession:
    """Represents a single Vivado TCL session with keep-alive and health monitoring."""

    def __init__(self, vivado_path: str, session_id: str, timeout: int = 3600):
        self.vivado_path = vivado_path
        self.session_id = session_id
        self.timeout = timeout
        self.process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_activity = time.time()
        self._healthy = True
        self._monitor_thread: threading.Thread | None = None

    def start(self) -> bool:
        """Start a persistent Vivado TCL shell session."""
        try:
            self.process = subprocess.Popen(
                [self.vivado_path, "-mode", "tcl", "-nojournal"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._last_activity = time.time()
            self._healthy = True
            self._monitor_thread = threading.Thread(
                target=self._health_monitor, daemon=True
            )
            self._monitor_thread.start()
            logger.info(f"TCL session {self.session_id} started (PID: {self.process.pid})")
            return True
        except FileNotFoundError:
            logger.error(f"Vivado not found: {self.vivado_path}")
            return False

    def send(self, command: str, timeout: int = 300) -> str:
        """Send a TCL command and get the response."""
        if not self.process or not self._healthy:
            raise RuntimeError("Session not started or unhealthy")

        with self._lock:
            try:
                self.process.stdin.write(command + "\n")
                self.process.stdin.flush()
                self._last_activity = time.time()

                # Read until prompt or timeout
                output = []
                deadline = time.time() + timeout
                while time.time() < deadline:
                    line = self.process.stdout.readline()
                    if not line:
                        break
                    output.append(line)
                    # Vivado TCL prompt
                    if line.strip().endswith("% "):
                        break

                return "".join(output)
            except Exception as e:
                self._healthy = False
                logger.error(f"Session {self.session_id} error: {e}")
                raise

    def _health_monitor(self):
        """Monitor session health: restart if stuck or timed out."""
        while self._healthy and self.process:
            idle = time.time() - self._last_activity
            if idle > self.timeout:
                logger.warning(f"Session {self.session_id} idle for {idle:.0f}s — restarting")
                self._healthy = False
                self.restart()
                break
            time.sleep(30)

    def restart(self) -> bool:
        """Restart the session."""
        self.stop()
        return self.start()

    def stop(self):
        """Stop the session gracefully."""
        self._healthy = False
        if self.process:
            try:
                self.process.stdin.write("exit\n")
                self.process.stdin.flush()
                self.process.wait(timeout=10)
            except Exception:
                self.process.kill()
            self.process = None
            logger.info(f"Session {self.session_id} stopped")


class TCLEngine:
    """TCL execution engine with session management.

    Supports:
    - One-shot batch execution (legacy)
    - Persistent sessions with keep-alive
    - Auto-restart on timeout or memory pressure
    - Memory leak detection (heuristic: cumulative output size)
    """

    def __init__(self, vivado_path: str = "vivado", mode: str = "batch",
                 timeout: int = 3600, max_memory_mb: int = 4096):
        self.vivado_path = vivado_path
        self.mode = mode
        self.timeout = timeout
        self.max_memory_mb = max_memory_mb
        self._sessions: dict[str, TCLSession] = {}
        self._cumulative_output = 0

    # ── One-shot batch execution ──

    def run_script(self, tcl_commands: str | list[str],
                   workdir: str | Path | None = None) -> dict:
        """Execute TCL commands in Vivado batch mode. Returns {stdout, stderr, returncode, elapsed}."""
        if isinstance(tcl_commands, list):
            tcl_commands = "\n".join(tcl_commands)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tcl", delete=False, encoding="utf-8"
        ) as f:
            f.write(tcl_commands)
            script_path = f.name

        try:
            cmd = [
                self.vivado_path, "-mode", self.mode,
                "-source", script_path, "-nojournal",
                "-log", self._log_path("vivado"),
            ]
            logger.info(f"Executing: {self.vivado_path} -mode {self.mode} -source {script_path}")
            start = time.time()

            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self.timeout, cwd=str(workdir) if workdir else None,
            )

            elapsed = time.time() - start
            self._cumulative_output += len(proc.stdout) + len(proc.stderr)

            # Memory leak heuristic: if output is growing too fast, warn
            if self._cumulative_output > self.max_memory_mb * 1024 * 1024:
                logger.warning(f"Cumulative output > {self.max_memory_mb}MB — possible memory leak")
                self._cumulative_output = 0

            if proc.returncode != 0:
                logger.warning(f"TCL finished rc={proc.returncode} in {elapsed:.1f}s")
            else:
                logger.info(f"TCL finished in {elapsed:.1f}s")

            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
                "elapsed": elapsed,
            }
        except subprocess.TimeoutExpired:
            logger.error(f"TCL timed out after {self.timeout}s")
            return {"stdout": "", "stderr": "Timeout", "returncode": -1, "elapsed": self.timeout}
        finally:
            os.unlink(script_path)

    # ── Persistent session management ──

    def create_session(self, session_id: str = "default") -> TCLSession:
        """Create a persistent TCL session with keep-alive."""
        if session_id in self._sessions:
            logger.warning(f"Session {session_id} already exists — restarting")
            self._sessions[session_id].stop()

        session = TCLSession(self.vivado_path, session_id, self.timeout)
        if session.start():
            self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str = "default") -> TCLSession | None:
        return self._sessions.get(session_id)

    def close_session(self, session_id: str = "default"):
        if session_id in self._sessions:
            self._sessions[session_id].stop()
            del self._sessions[session_id]

    def close_all(self):
        for sid in list(self._sessions.keys()):
            self.close_session(sid)

    # ── Utilities ──

    def run_tcl_safe(self, tcl_command: str, **kwargs) -> dict:
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
            (r"Placement failed.*?(\d+)", "placement_failed"),
            (r"FATAL\s+ERROR", "fatal_error"),
        ]
        for pattern, etype in patterns:
            for m in re.finditer(pattern, log, re.IGNORECASE):
                errors.append({
                    "type": etype,
                    "message": m.group(0).strip(),
                    "detail": m.group(1).strip() if m.lastindex and m.lastindex >= 1 else "",
                })
        return errors

    @staticmethod
    def extract_elapsed(log: str) -> float | None:
        m = re.search(r"Finished (.+?) in (\d+\.?\d*)s", log)
        if m:
            return float(m.group(2))
        return None