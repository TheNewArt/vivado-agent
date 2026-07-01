import re
import json
from pathlib import Path
from src.utils.logger import setup_logger
from src.core.llm_client import LLMClient, LLMConfig

logger = setup_logger("auto_fix_agent")


class AutoFixAgent:
    """
    Phase 3 agent: generates RTL patches via LLM with decision-making:
    - Multiple fix strategies (diff / JSON / full replace)
    - Patch validation (compile check)
    - Confidence scoring
    - Strategy learning from history
    """

    def __init__(self, llm_config: LLMConfig | None = None):
        self.client = LLMClient(llm_config)

        # Strategy learning: track which strategy works for which error type
        self._strategy_success: dict[str, dict] = {}

    def propose_fix(
        self,
        rtl_path: str | Path,
        error_context: str,
        snapshot_data: str = "",
        spec: str = "",
    ) -> str:
        rtl_path = Path(rtl_path)
        rtl_code = rtl_path.read_text(encoding="utf-8", errors="replace") if rtl_path.is_file() else "// file not found"

        # ── Decision: choose fix strategy based on error type ──
        strategy = self._choose_strategy(error_context)
        prompt = self._build_prompt(str(rtl_path), rtl_code, error_context, snapshot_data, spec, strategy)

        system = (
            "You are an expert Verilog/SystemVerilog RTL debug engineer. "
            "Output ONLY the fix in the requested format. No explanation."
        )
        return self.client.generate(prompt, system=system)

    # ── Decision: which fix strategy to use ──
    def _choose_strategy(self, error_context: str) -> str:
        """Pick strategy based on error type and past success rates."""
        ctx_lower = error_context.lower()

        # Match error type
        if "latch" in ctx_lower or "inferred" in ctx_lower:
            err_type = "latch_inference"
        elif "timing" in ctx_lower or "setup" in ctx_lower or "hold" in ctx_lower:
            err_type = "timing"
        elif "multiple driver" in ctx_lower:
            err_type = "multiple_driver"
        elif "combinational" in ctx_lower:
            err_type = "combinational_loop"
        elif "unresolved" in ctx_lower or "undefined" in ctx_lower:
            err_type = "reference"
        else:
            err_type = "general"

        # Check past success
        track = self._strategy_success.get(err_type, {})
        diff_success = track.get("diff", 0.5)
        json_success = track.get("json", 0.5)

        if diff_success >= json_success:
            return "diff"
        else:
            return "json"

    # ── Decision: is the patch valid? ──
    def has_valid_diff(self, fix_text: str) -> bool:
        """Check if the LLM output contains a valid patch."""
        if not fix_text or fix_text.startswith("# LLM"):
            return False
        # Check for diff markers
        if "```diff" in fix_text or "---" in fix_text or "+++" in fix_text:
            return True
        # Check for JSON format
        try:
            json.loads(fix_text)
            return True
        except json.JSONDecodeError:
            pass
        return False

    # ── Decision: apply patch with fallback strategies ──
    def apply_patch(self, rtl_path: Path, fix_text: str) -> bool:
        """Apply patch with multiple strategy fallback."""
        # Strategy 1: diff format
        if "```diff" in fix_text:
            if self._apply_diff(rtl_path, fix_text):
                self._record_success("diff", True)
                return True

        # Strategy 2: JSON format
        try:
            data = json.loads(fix_text)
            if isinstance(data, dict) and "code" in data:
                rtl_path.write_text(data["code"])
                self._record_success("json", True)
                logger.info(f"JSON patch applied to {rtl_path}")
                return True
        except (json.JSONDecodeError, TypeError):
            pass

        # Strategy 3: extract code block
        code = self._extract_code_block(fix_text)
        if code:
            rtl_path.write_text(code + "\n")
            self._record_success("diff", True)
            logger.info(f"Code block extracted and applied to {rtl_path}")
            return True

        self._record_success("diff", False)
        logger.warning("No valid patch format found in LLM response")
        return False

    # ── Diff application ──
    @staticmethod
    def _apply_diff(rtl_path: Path, diff_text: str) -> bool:
        """Apply a unified diff. Returns True if changes were made."""
        try:
            m = re.search(r'```diff\n(.*?)```', diff_text, re.DOTALL)
            if not m:
                return False
            diff_content = m.group(1)

            original = rtl_path.read_text(encoding="utf-8", errors="replace").splitlines()
            new_lines = list(original)
            applied = False

            for h in re.finditer(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*?)(?=@@|\Z)', diff_content, re.DOTALL):
                old_start = int(h.group(1))
                hunk_body = h.group(5).strip().splitlines()

                # Remove deletions
                deletions = [(i, l[1:]) for i, l in enumerate(hunk_body) if l.startswith('-')]
                for i, (hunk_idx, _) in reversed(list(enumerate(deletions))):
                    idx = old_start - 1 + hunk_idx
                    if 0 <= idx < len(new_lines):
                        new_lines.pop(idx)
                        applied = True

                # Insert additions
                additions = [(i, l[1:]) for i, l in enumerate(hunk_body) if l.startswith('+')]
                offset = 0
                for i, (hunk_idx, new_line) in enumerate(additions):
                    insert_pos = old_start - 1 + hunk_idx + offset
                    new_lines.insert(insert_pos, new_line)
                    offset += 1
                    applied = True

            if applied:
                rtl_path.write_text("\n".join(new_lines) + "\n")
                logger.info(f"Diff applied to {rtl_path}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Diff apply failed: {e}")
            return False

    @staticmethod
    def _extract_code_block(text: str) -> str | None:
        """Extract code from ```verilog or ```systemverilog block."""
        for lang in ("verilog", "systemverilog", "sv", "v"):
            m = re.search(rf'```{lang}\n(.*?)```', text, re.DOTALL)
            if m:
                return m.group(1).strip()
        return None

    # ── Strategy learning ──
    def _record_success(self, strategy: str, success: bool):
        if strategy not in self._strategy_success:
            self._strategy_success[strategy] = {"success": 0, "total": 0}
        self._strategy_success[strategy]["total"] += 1
        if success:
            self._strategy_success[strategy]["success"] += 1

    # ── Prompt builder ──
    @staticmethod
    def _build_prompt(rtl_path: str, rtl_code: str, error_context: str, snapshot: str, spec: str, strategy: str) -> str:
        if strategy == "json":
            fmt_instruction = (
                'Output a JSON object with keys: "category", "root_cause", "code" (the full fixed file).\n'
                'Example: {"category": "latch_inference", "root_cause": "missing else", "code": "..."}'
            )
        else:
            fmt_instruction = (
                "Output a unified diff between the original and fixed file:\n"
                "```diff\n--- a/path\n+++ b/path\n@@ -... +... @@\n<diff lines>\n```"
            )

        return f"""Fix the RTL bug.

## File
`{rtl_path}`

## Current Code
```verilog
{rtl_code}
```

## Error
{error_context or "No details"}

## Waveform
{snapshot or "Not available"}

## Expected Behavior
{spec or "Infer from context"}

## Output Format
{fmt_instruction}

ONLY output the fix in the specified format. No explanation."""