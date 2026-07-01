import re
import json
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("rag_index")


@dataclass
class SourceBlock:
    file_path: str
    line_start: int
    line_end: int
    content: str
    module: str = ""
    block_type: str = ""  # module, always_block, assign, instance


class RAGIndex:
    """Index RTL source files for line-number -> code-block lookup."""

    def __init__(self, rtl_dir: str | Path | None = None):
        self.rtl_dir = Path(rtl_dir) if rtl_dir else None
        self.blocks: list[SourceBlock] = []
        self.line_map: dict[str, dict[int, SourceBlock]] = {}  # file -> line -> block
        self._built = False

    def build(self, rtl_dir: str | Path | None = None):
        if rtl_dir:
            self.rtl_dir = Path(rtl_dir)
        if not self.rtl_dir or not self.rtl_dir.exists():
            logger.warning(f"Cannot build index: {self.rtl_dir} not found")
            return

        self.blocks = []
        self.line_map = {}
        for ext in ("*.v", "*.sv", "*.vhd"):
            for f in sorted(self.rtl_dir.rglob(ext)):
                self._index_file(f)

        self._built = True
        logger.info(f"RAG index built: {len(self.blocks)} blocks from {len(self.line_map)} files")

    def _index_file(self, path: Path):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        file_key = str(path.resolve())

        self.line_map[file_key] = {}

        # Index module blocks
        mod_re = re.compile(r'(?:^|\n)\s*module\s+(\w+)', re.MULTILINE)
        for m in mod_re.finditer(text):
            start_line = text[:m.start()].count('\n') + 1
            end_line = self._find_block_end(text, m.end(), lines)
            block = SourceBlock(
                file_path=file_key,
                line_start=start_line,
                line_end=end_line,
                content="\n".join(lines[start_line-1:end_line]),
                module=m.group(1),
                block_type="module",
            )
            self.blocks.append(block)
            for ln in range(start_line, end_line + 1):
                self.line_map[file_key][ln] = block

        # Index always blocks
        always_re = re.compile(r'(always|always_comb|always_ff|always_latch)\s*@?\s*\(', re.MULTILINE)
        for m in always_re.finditer(text):
            start_line = text[:m.start()].count('\n') + 1
            end_line = self._find_block_end(text, m.end(), lines)
            block = SourceBlock(
                file_path=file_key,
                line_start=start_line,
                line_end=end_line,
                content="\n".join(lines[start_line-1:end_line]),
                module=self._find_enclosing_module(text, m.start()),
                block_type="always_block",
            )
            self.blocks.append(block)
            for ln in range(start_line, end_line + 1):
                self.line_map[file_key][ln] = block

    def lookup(self, file_path: str | Path, line_no: int) -> SourceBlock | None:
        """Find the code block containing a given line number."""
        file_key = str(Path(file_path).resolve())
        return self.line_map.get(file_key, {}).get(line_no)

    def lookup_by_error(self, file_path: str | Path, line_no: int, context_lines: int = 5) -> str:
        """Retrieve source code around an error line with context."""
        block = self.lookup(file_path, line_no)
        if block:
            return block.content

        # Fallback: just read around the line
        file_key = str(Path(file_path).resolve())
        try:
            with open(file_key) as f:
                all_lines = f.readlines()
            start = max(0, line_no - context_lines - 1)
            end = min(len(all_lines), line_no + context_lines)
            return "".join(all_lines[start:end])
        except Exception:
            return ""

    def search_by_module(self, module_name: str) -> list[SourceBlock]:
        """Find blocks belonging to a module."""
        return [b for b in self.blocks if b.module == module_name]

    def search_by_text(self, query: str) -> list[SourceBlock]:
        """Search block content for text."""
        return [b for b in self.blocks if query.lower() in b.content.lower()]

    def get_all_file_keys(self) -> list[str]:
        return list(self.line_map.keys())

    @staticmethod
    def _find_block_end(text: str, start_pos: int, lines: list[str]) -> int:
        """Find end of a begin..end block starting from position."""
        pos = start_pos
        brace_depth = 0
        in_block = False
        end_line = len(lines)

        while pos < len(text):
            next_begin = text.find("begin", pos, pos + 20)
            next_end = text.find("end", pos, pos + 20)

            if next_begin == -1 and next_end == -1:
                break

            if next_end != -1 and (next_begin == -1 or next_end < next_begin):
                brace_depth -= 1
                if brace_depth < 0 and in_block:
                    end_line = text[:pos].count('\n') + 1
                    break
                pos = next_end + 3
            else:
                brace_depth += 1
                in_block = True
                pos = next_begin + 5

        return end_line

    @staticmethod
    def _find_enclosing_module(text: str, pos: int) -> str:
        """Find the module name that encloses a position."""
        mod_re = re.compile(r'(?:^|\n)\s*module\s+(\w+)', re.MULTILINE)
        last_mod = ""
        for m in mod_re.finditer(text):
            if m.start() < pos:
                last_mod = m.group(1)
            else:
                break
        return last_mod


class BugDatabase:
    """Simple pattern-matching bug database for common FPGA issues."""

    def __init__(self):
        self.patterns: list[dict] = [
            {
                "pattern": r"Latch\s+inferred",
                "category": "latch_inference",
                "suggestion": "Missing default assignment in combinational always block; add 'else' or default assignment",
                "severity": "warning",
            },
            {
                "pattern": r"Multiple\s+driver",
                "category": "multiple_driver",
                "suggestion": "Signal driven from multiple always blocks; merge all assignments into one block",
                "severity": "error",
            },
            {
                "pattern": r"Combinational\s+loop",
                "category": "combinational_loop",
                "suggestion": "Combinational feedback detected; add register stage to break the loop",
                "severity": "error",
            },
            {
                "pattern": r"Timing.*?violation",
                "category": "timing_violation",
                "suggestion": "Reduce logic levels, increase clock period, or add pipeline registers",
                "severity": "error",
            },
            {
                "pattern": r"CDC|clock\s+crossing",
                "category": "cdc",
                "suggestion": "Add 2-FF synchronizer for cross-clock domain signals",
                "severity": "warning",
            },
            {
                "pattern": r"unresolved|Undefined",
                "category": "reference_error",
                "suggestion": "Check module/instance names and file include paths",
                "severity": "error",
            },
            {
                "pattern": r"width|mismatch",
                "category": "width_mismatch",
                "suggestion": "Check port widths and signal declarations",
                "severity": "error",
            },
            {
                "pattern": r"inferred\s+latch",
                "category": "inferred_latch",
                "suggestion": "Incomplete case/if statement; add 'default' or 'else' clause",
                "severity": "warning",
            },
            {
                "pattern": r"X\s+propagation",
                "category": "x_propagation",
                "suggestion": "Check for uninitialized registers or unresolved inputs",
                "severity": "error",
            },
        ]

    def match(self, error_message: str) -> list[dict]:
        matches = []
        for entry in self.patterns:
            if re.search(entry["pattern"], error_message, re.IGNORECASE):
                matches.append(entry.copy())
        return matches