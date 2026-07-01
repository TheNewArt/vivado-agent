import os
import yaml
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from src.core.llm_client import LLMConfig

# Load .env from project root (search upward from this file)
_env_loaded = False


def _ensure_env():
    global _env_loaded
    if not _env_loaded:
        load_dotenv()
        _env_loaded = True


class Config:
    def __init__(self, config_dir: str | Path = "config"):
        self.config_dir = Path(config_dir)
        self.data: dict = {}

    def load(self, path: Optional[str | Path] = None) -> dict:
        if path is None:
            path = self.config_dir / "default.yaml"
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path) as f:
            self.data = yaml.safe_load(f) or {}
        return self.data

    def merge(self, other: dict):
        self._deep_merge(self.data, other)

    def get(self, key: str, default=None):
        keys = key.split(".")
        val = self.data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
        return val if val is not None else default

    @staticmethod
    def _deep_merge(base: dict, overlay: dict):
        for k, v in overlay.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                Config._deep_merge(base[k], v)
            else:
                base[k] = v

    @property
    def vivado_path(self) -> str:
        return self.get("vivado.path", "vivado")

    @property
    def xsim_cache(self) -> Path:
        return Path(self.get("optimization.sim_cache_dir", "./xsim_cache")).resolve()

    @property
    def incremental_enabled(self) -> bool:
        return self.get("optimization.incremental_compile", True)

    @property
    def max_threads(self) -> int:
        return self.get("optimization.max_threads", 0)

    @property
    def waveform_crop_enabled(self) -> bool:
        return self.get("optimization.waveform_crop", True)

    @property
    def terminate_on_deadlock(self) -> bool:
        return self.get("optimization.terminate_on_deadlock", True)

    def build_llm_config(self) -> LLMConfig:
        return LLMConfig(
            api_key=os.environ.get("LLM_API_KEY", ""),
            base_url=self.get("llm.base_url", "https://api.openai.com/v1"),
            model=self.get("llm.model", "gpt-4o"),
            temperature=self.get("llm.temperature", 0.1),
            max_tokens=self.get("llm.max_tokens", 4096),
        )