import re
import json
import subprocess
import tempfile
from pathlib import Path
from src.utils.logger import setup_logger
from src.core.llm_client import LLMClient, LLMConfig

logger = setup_logger("auto_fix_agent")


class AutoFixAgent:
    """
    Phase 3 agent: generates RTL patches via LLM with validation gating.

    Pipeline:
      LLM output -> extract patch -> syntax check (xvlog) -> apply -> verify

    Multiple fix strategies (diff / JSON / full replace) with fallback.
    """

    def __init__(self, llm_config: LLMConfig | None = None, vivado_path: str = "vivado"):
        self.client = LLMClient(llm_config)
        self.vivado_path = vivado_path
        self._strategy_success: dict[str, dict] = {}

    # ── Main entry ──
    def propose_fix(self, rtl_path: str | Path, error_context: str,
                    snapshot_data: str = "", spec: str = "") -> str:
        rtl_path = Path(rtl_path)
        rtl_code = rtl_path.read_text(encoding="utf-8", errors="replace") if rtl_path.is_file() else "// file not found"
        strategy = self._choose_strategy(error_context)
        prompt = self._build_prompt(str(rtl_path), rtl_code, error_context, snapshot_data, spec, strategy)
        system = "You are an expert Verilog/SystemVerilog RTL debug engineer. Output ONLY the fix."
        return self.client.generate(prompt, system=system)

    # ── Strategy selection ──
    def _choose_strategy(self, error_context: str) -> str:
        ctx = error_context.lower()
        if "latch" in ctx:
            err_type = "latch_inference"
        elif "timing" in ctx or "setup" in ctx:
            err_type = "timing"
        elif "multiple driver" in ctx:
            err_type = "multiple_driver"
        elif "combinational" in ctx:
            err_type = "combinational_loop"
        elif "unresolved" in ctx or "undefined" in ctx:
            err_type = "reference"
        else:
            err_type = "general"

        track = self._strategy_success.get(err_type, {})
        diff_s = track.get("diff", 0.5)
        json_s = track.get("json", 0.5)
        return "diff" if diff_s >= json_s else "json"

    # ── Validation ──
    def has_valid_diff(self, fix_text: str) -> bool:
        if not fix_text or fix_text.startswith("# LLM"):
            return False
        if "```diff" in fix_text or "---" in fix_text:
            return True
        try:
            json.loads(fix_text)
            return True
        except json.JSONDecodeError:
            pass
        return False

    def syntax_check(self, rtl_path: Path) -> dict:
        """Run syntax check: Verilator (ms) → Vivado fallback (s).

        Returns {passed, errors, checker}.
        """
        from src.tools.synth_checker import SynthChecker
        checker = SynthChecker(vivado_path=self.vivado_path)
        top_module = rtl_path.stem  # guess top from filename
        result = checker.quick_check(rtl_path, top_module=top_module)
        return {
            "passed": result.passed,
            "errors": "; ".join(result.errors[:5]) if result.errors else "",
            "checker": result.checker,
        }

    # ── Apply patch with validation ──
    def apply_patch(self, rtl_path: Path, fix_text: str) -> bool:
        """Extract, syntax-check, then apply patch."""
        # Extract the fixed code
        new_code = self._extract_fixed_code(fix_text, rtl_path)
        if not new_code:
            logger.warning("No valid patch format found")
            return False

        # Write to temp file for syntax check
        with tempfile.NamedTemporaryFile(
            suffix=rtl_path.suffix, mode="w", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(new_code)
            tmp_path = tmp.name

        # Syntax check
        check = self.syntax_check(Path(tmp_path))
        if not check["passed"]:
            logger.warning(f"Syntax check FAILED: {check['errors'][:200]}")
            Path(tmp_path).unlink(missing_ok=True)
            return False

        # Apply
        rtl_path.write_text(new_code)
        Path(tmp_path).unlink(missing_ok=True)
        logger.info(f"Patch applied to {rtl_path} (syntax check passed)")
        return True

    def _extract_fixed_code(self, fix_text: str, original_path: Path) -> str | None:
        """Extract fixed code from LLM output using multiple strategies."""
        # Strategy 1: diff
        m = re.search(r'```diff\n(.*?)```', fix_text, re.DOTALL)
        if m:
            result = self._apply_diff_to_text(original_path.read_text(encoding="utf-8"), m.group(1))
            if result:
                return result

        # Strategy 2: JSON
        try:
            data = json.loads(fix_text)
            if isinstance(data, dict) and "code" in data:
                return data["code"]
        except (json.JSONDecodeError, TypeError):
            pass

        # Strategy 3: code block
        for lang in ("verilog", "systemverilog", "sv", "v", ""):
            pattern = rf'```{lang}\n(.*?)```' if lang else r'```\n(.*?)```'
            m = re.search(pattern, fix_text, re.DOTALL)
            if m:
                return m.group(1).strip()

        return None

    @staticmethod
    def _apply_diff_to_text(original: str, diff_content: str) -> str | None:
        """Apply unified diff to original text and return result."""
        lines = original.splitlines()
        new_lines = list(lines)
        applied = False

        for h in re.finditer(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*?)(?=@@|\Z)', diff_content, re.DOTALL):
            old_start = int(h.group(1))
            hunk = h.group(5).strip().splitlines()

            # Remove lines marked with '-'
            deletions = [(i, l[1:]) for i, l in enumerate(hunk) if l.startswith('-')]
            for j, (hunk_idx, _) in reversed(list(enumerate(deletions))):
                idx = old_start - 1 + hunk_idx
                if 0 <= idx < len(new_lines):
                    new_lines.pop(idx)
                    applied = True

            # Add lines marked with '+'
            additions = [(i, l[1:]) for i, l in enumerate(hunk) if l.startswith('+')]
            offset = 0
            for j, (hunk_idx, new_line) in enumerate(additions):
                insert_pos = old_start - 1 + hunk_idx + offset
                if 0 <= insert_pos <= len(new_lines):
                    new_lines.insert(insert_pos, new_line)
                    offset += 1
                    applied = True

        return "\n".join(new_lines) + "\n" if applied else None

    def _record_success(self, strategy: str, success: bool):
        if strategy not in self._strategy_success:
            self._strategy_success[strategy] = {"success": 0, "total": 0}
        self._strategy_success[strategy]["total"] += 1
        if success:
            self._strategy_success[strategy]["success"] += 1

    @staticmethod
    def _build_prompt(rtl_path: str, rtl_code: str, error_context: str,
                      snapshot: str, spec: str, strategy: str) -> str:
        fmt = 'Output JSON: {"category": "...", "root_cause": "...", "code": "..."}' \
            if strategy == "json" else \
            'Output diff:\n```diff\n--- a/path\n+++ b/path\n@@ -... +... @@\n<diff>\n```'
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
{fmt}

ONLY the fix. No explanation."""