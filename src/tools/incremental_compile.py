import hashlib
import json
import time
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger("incremental_compile")


class IncrementalCompileManager:
    """Module-level incremental compilation with time prediction."""

    def __init__(self, cache_dir: str | Path = "./xsim_cache"):
        self.cache_dir = Path(cache_dir)
        self.manifest_path = self.cache_dir / ".incremental_manifest.json"
        self.history_path = self.cache_dir / ".compile_history.json"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()

    def compute_module_hashes(self, module_to_files: dict[str, list[Path]]) -> dict[str, str]:
        """Compute hash per module by concatenating all its source files."""
        result = {}
        for mod, files in module_to_files.items():
            h = hashlib.sha256()
            for f in sorted(files):
                try:
                    h.update(f.read_bytes())
                except FileNotFoundError:
                    h.update(b"missing")
            result[mod] = h.hexdigest()
        return result

    def get_changed_modules(
        self, module_to_files: dict[str, list[Path]]
    ) -> tuple[list[str], list[str], bool]:
        """Return (changed_module_names, unchanged_module_names, is_first_run)."""
        manifest = self._load_manifest()
        current = self.compute_module_hashes(module_to_files)

        if not manifest:
            logger.info("No cache — first run (all modules need compile)")
            self._save_manifest(current)
            return list(current.keys()), [], True

        changed, unchanged = [], []
        for mod, chash in current.items():
            cached = manifest.get(mod)
            if cached != chash:
                changed.append(mod)
            else:
                unchanged.append(mod)

        if changed:
            new_manifest = manifest.copy()
            new_manifest.update(current)
            self._save_manifest(new_manifest)

        logger.info(f"Incremental: {len(changed)} changed, {len(unchanged)} cached modules")
        return changed, unchanged, False

    def get_changed_files(self, files: list[Path]) -> tuple[list[Path], list[Path], bool]:
        """Legacy flat-file check. Still works."""
        manifest = self._load_manifest()
        if not manifest:
            self._save_manifest({str(f): self._file_hash(f) for f in files})
            return files, [], True

        changed, unchanged = [], []
        for f in files:
            current = self._file_hash(f)
            cached = manifest.get(str(f))
            if cached != current:
                changed.append(f)
            else:
                unchanged.append(f)

        if changed:
            new_mf = manifest.copy()
            for f in files:
                new_mf[str(f)] = self._file_hash(f)
            self._save_manifest(new_mf)

        return changed, unchanged, False

    def record_compile(self, modules: list[str], duration_s: float, file_count: int, total_lines: int):
        """Record a compile event for time prediction."""
        history = self._load_history()
        entry = {
            "timestamp": time.time(),
            "modules": modules,
            "duration_s": duration_s,
            "file_count": file_count,
            "total_lines": total_lines,
        }
        history.append(entry)
        # Keep last 20 entries
        history = history[-20:]
        with open(self.history_path, "w") as f:
            json.dump(history, f, indent=2)

    def predict_compile_time(self, changed_modules: list[str]) -> dict:
        """Predict compile time based on history and module count."""
        history = self._load_history()
        if not history:
            return {"predicted_s": None, "confidence": "none", "detail": "No history available"}

        # Average duration per module from recent runs
        total_dur = sum(e["duration_s"] for e in history)
        total_mods = sum(len(e["modules"]) for e in history)
        avg_per_module = total_dur / max(total_mods, 1)

        predicted = avg_per_module * len(changed_modules)
        return {
            "predicted_s": round(predicted, 1),
            "predicted_min": round(predicted / 60, 1),
            "confidence": "low" if len(history) < 3 else "medium",
            "history_entries": len(history),
        }

    def get_compile_deps_tcl(self, filesets: list[str], enable_incr: bool = False) -> str:
        """Generate TCL for incremental compilation.
        enable_incr: set True to enable --incr (Vivado 2019+). Safe to leave off on first run.
        """
        tcl = ["# Incremental compilation setup"]
        tcl.append(f"set_property xsim.simulate.cache_path {{{self.cache_dir}}} [get_filesets sim_1]")
        if enable_incr:
            tcl.append("set_property xsim.simulate.xsim.more_options {--incr} [get_filesets sim_1]")
        else:
            tcl.append("# set --incr after first successful compile for faster subsequent runs")
        return "\n".join(tcl)

    def _load_manifest(self) -> dict:
        if self.manifest_path.exists():
            with open(self.manifest_path) as f:
                return json.load(f)
        return {}

    def _save_manifest(self, manifest: dict):
        with open(self.manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

    def _load_history(self) -> list:
        if self.history_path.exists():
            with open(self.history_path) as f:
                return json.load(f)
        return []

    def clear(self):
        if self.manifest_path.exists():
            self.manifest_path.unlink()
        if self.history_path.exists():
            self.history_path.unlink()
        logger.info("Cache cleared")