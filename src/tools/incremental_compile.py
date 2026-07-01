import hashlib
import json
import time
from pathlib import Path
from src.utils.logger import setup_logger

logger = setup_logger("incremental_compile")


class IncrementalCompileManager:
    """Dependency-aware incremental compilation cache.

    Instead of flat file hashing, this tracks module-level hashes AND
    their dependency relationships.  If module B changes, both B and any
    module that instantiates B (transitive) will be marked as needing
    recompilation.
    """

    def __init__(self, cache_dir: str | Path = "./xsim_cache"):
        self.cache_dir = Path(cache_dir)
        self.manifest_path = self.cache_dir / ".incremental_manifest.json"
        self.history_path = self.cache_dir / ".compile_history.json"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()

    def compute_module_hashes(
        self, module_to_files: dict[str, list[Path]]
    ) -> dict[str, str]:
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
        self,
        module_to_files: dict[str, list[Path]],
        dependency_graph: dict | None = None,
    ) -> tuple[list[str], list[str], bool]:
        """Return (changed_module_names, unchanged_module_names, is_first_run).

        When dependency_graph is provided, transitively affected modules
        (those that depend on a changed module) are also marked as changed.
        """
        manifest = self._load_manifest()
        current = self.compute_module_hashes(module_to_files)

        if not manifest:
            logger.info("No cache — first run (all modules need compile)")
            self._save_manifest(current)
            return list(current.keys()), [], True

        direct_changed = []
        unchanged = []
        for mod, chash in current.items():
            cached = manifest.get(mod)
            if cached != chash:
                direct_changed.append(mod)
            else:
                unchanged.append(mod)

        changed = set(direct_changed)

        # Dependency-aware: if a module changed, anything that depends on it also changes
        if dependency_graph and direct_changed:
            from src.tools.dependency_graph import DependencyGraph
            dg = dependency_graph if isinstance(dependency_graph, dict) else {}
            transitive = set(direct_changed)
            queue = list(direct_changed)
            while queue:
                mod = queue.pop(0)
                node_data = dg.get(mod) if isinstance(dg, dict) else None
                if node_data:
                    for parent in getattr(node_data, 'instantiated_by', set()):
                        if parent not in transitive:
                            transitive.add(parent)
                            queue.append(parent)
            changed = transitive

        changed_list = list(changed)
        unchanged_list = [m for m in unchanged if m not in changed]

        if changed_list:
            new_manifest = manifest.copy()
            new_manifest.update(current)
            self._save_manifest(new_manifest)

        logger.info(
            f"Incremental: {len(direct_changed)} direct + "
            f"{len(changed_list) - len(direct_changed)} transitive = "
            f"{len(changed_list)} total changed, {len(unchanged_list)} cached"
        )
        return changed_list, unchanged_list, False

    def get_changed_files(self, files: list[Path]) -> tuple[list[Path], list[Path], bool]:
        """Legacy flat-file check (no dependency awareness)."""
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

    def record_compile(self, modules: list[str], duration_s: float,
                       file_count: int, total_lines: int):
        history = self._load_history()
        entry = {
            "timestamp": time.time(),
            "modules": modules,
            "duration_s": round(duration_s, 1),
            "file_count": file_count,
            "total_lines": total_lines,
        }
        history.append(entry)
        history = history[-20:]
        with open(self.history_path, "w") as f:
            json.dump(history, f, indent=2)

    def predict_compile_time(self, changed_modules: list[str]) -> dict:
        history = self._load_history()
        if not history:
            return {"predicted_s": None, "confidence": "none",
                    "detail": "No history available"}
        total_dur = sum(e["duration_s"] for e in history)
        total_mods = sum(len(e["modules"]) for e in history)
        avg = total_dur / max(total_mods, 1)
        predicted = avg * len(changed_modules)
        return {
            "predicted_s": round(predicted, 1),
            "predicted_min": round(predicted / 60, 1),
            "confidence": "low" if len(history) < 3 else "medium",
            "history_entries": len(history),
        }

    def get_compile_deps_tcl(self, filesets: list[str], enable_incr: bool = False) -> str:
        tcl = ["# Incremental compilation setup"]
        tcl.append(f"set_property xsim.simulate.cache_path {{{self.cache_dir}}} [get_filesets sim_1]")
        if enable_incr:
            tcl.append("set_property xsim.simulate.xsim.more_options {--incr} [get_filesets sim_1]")
        else:
            tcl.append("# set --incr after first successful compile")
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