"""Online LLM API client (OpenAI-compatible). Supports any provider: OpenAI, DeepSeek, etc."""

import json
import os
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("llm_client")


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = 0.1
    max_tokens: int = 4096


class LLMClient:
    """Lightweight OpenAI-compatible API client. No SDK dependency needed."""

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or self._from_env()
        if not self.config.api_key:
            logger.warning("No API key configured — LLM calls will fail")

    @staticmethod
    def _from_env() -> LLMConfig:
        return LLMConfig(
            api_key=os.environ.get("LLM_API_KEY", ""),
            base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("LLM_MODEL", "gpt-4o"),
        )

    def generate(self, prompt: str, system: str = "") -> str:
        """Send a chat completion request and return the response text."""
        import urllib.request
        import urllib.error

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }).encode()

        req = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
                content = result["choices"][0]["message"]["content"]
                logger.info(f"LLM response: {len(content)} chars, model={result['model']}")
                return content
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            logger.error(f"LLM API error {e.code}: {err_body[:300]}")
            return f"# LLM API error: {e.code}\n# {err_body[:200]}"
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            return f"# LLM request failed: {e}"