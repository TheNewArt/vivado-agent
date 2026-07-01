from pathlib import Path
from src.utils.logger import setup_logger
from src.core.llm_client import LLMClient, LLMConfig

logger = setup_logger("auto_fix_agent")


class AutoFixAgent:
    """
    Phase 3 agent (LLM-based via online API): generates RTL patches
    from errors/waveforms and drives the fix-verify loop.
    """

    def __init__(self, llm_config: LLMConfig | None = None):
        self.client = LLMClient(llm_config)

    def propose_fix(
        self,
        rtl_path: str | Path,
        error_context: str,
        snapshot_data: str = "",
        spec: str = "",
    ) -> str:
        rtl_code = Path(rtl_path).read_text(encoding="utf-8", errors="replace") if Path(rtl_path).is_file() else \
                   "// Multiple files — see directory structure"
        prompt = self._build_prompt(str(rtl_path), rtl_code, error_context, snapshot_data, spec)
        system = "You are an expert Verilog/SystemVerilog RTL debug engineer. Output ONLY the diff."
        return self.client.generate(prompt, system=system)

    @staticmethod
    def _build_prompt(rtl_path: str, rtl_code: str, error_context: str, snapshot: str, spec: str) -> str:
        return f"""Analyze the RTL failure and output a minimal patch.

## Source File
`{rtl_path}`

## RTL Code
```verilog
{rtl_code}
```

## Error / Timing Context
{error_context or "No errors parsed"}

## Waveform Snapshot
{snapshot or "Not available"}

## Specification / Expected Behavior
{spec or "Not provided (infer from context)"}

## Instructions
1. Identify the root cause (testbench issue, RTL logic bug, or synthesis/timing issue).
2. Output a minimal unified diff patch.
3. If the root cause is outside the shown file, state it clearly and suggest the file to fix.

Output format (ONLY this, no extra commentary):
```diff
--- a/{rtl_path}
+++ b/{rtl_path}
@@ -... +... @@
<diff lines>
```"""

    def verify_fix(self) -> bool:
        logger.warning("Fix re-verification requires simulation re-run — not yet automated")
        return False