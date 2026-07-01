import re
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("module_parser")


@dataclass
class ModuleInfo:
    name: str
    files: list[Path]
    lines: int
    is_top_candidate: bool = False


class ModuleParser:
    """Parse Verilog/SystemVerilog files to extract module declarations."""

    MODULE_RE = re.compile(
        r'(?:^|\n)\s*module\s+(\w+)\s*(?:#\s*\(|\(|;)',
        re.MULTILINE,
    )
    ENDMODULE_RE = re.compile(r'^endmodule\s*$', re.MULTILINE)
    PORT_RE = re.compile(
        r'(input|output|inout)\s+(?:wire|reg|logic|wand|wor)?\s*(?:\[\d+:\d+\]\s*)?(\w+)',
        re.IGNORECASE,
    )

    def __init__(self):
        self.module_map: dict[str, ModuleInfo] = {}

    def parse_file(self, path: Path) -> dict[str, ModuleInfo]:
        """Extract module names from a single HDL file."""
        if not path.is_file():
            return {}
        text = path.read_text(encoding="utf-8", errors="replace")
        modules = {}
        for m in self.MODULE_RE.finditer(text):
            name = m.group(1)
            # Skip `module` keyword inside comments or string literals
            prev_newline = text.rfind('\n', 0, m.start())
            line_start = prev_newline + 1
            line_prefix = text[line_start:m.start()].strip()
            if "//" in line_prefix or "/*" in line_prefix or "//" in text[line_start:line_start+2]:
                continue
            if name.lower() in ('auto', 'generate', 'for', 'if', 'case'):
                continue
            modules[name] = ModuleInfo(name=name, files=[path], lines=text.count('\n') + 1)
        return modules

    def scan_directory(self, rtl_dir: str | Path) -> dict[str, ModuleInfo]:
        """Scan directory and build module-name -> files map."""
        rtl_dir = Path(rtl_dir)
        if not rtl_dir.exists():
            logger.warning(f"RTL directory not found: {rtl_dir}")
            return {}

        self.module_map = {}
        for ext in ("*.v", "*.sv", "*.vhd"):
            for f in sorted(rtl_dir.rglob(ext)):
                modules = self.parse_file(f)
                for mod_name, info in modules.items():
                    if mod_name in self.module_map:
                        self.module_map[mod_name].files.append(f)
                    else:
                        self.module_map[mod_name] = info

        logger.info(f"Scanned {len(self.module_map)} modules from {rtl_dir}")
        for name, info in list(self.module_map.items())[:5]:
            logger.debug(f"  {name}: {info.files[0].name} ({info.lines} lines)")
        return self.module_map

    def find_top_module(self, mod_name: str | None = None) -> str | None:
        """Auto-detect top module: first module with no instantiations of other modules."""
        if not self.module_map:
            return None
        if mod_name and mod_name in self.module_map:
            return mod_name
        # Heuristic: the module with the most ports is likely top
        best, best_ports = None, 0
        for name, info in self.module_map.items():
            text = info.files[0].read_text(encoding="utf-8", errors="replace")
            ports = len(self.PORT_RE.findall(text))
            if ports > best_ports:
                best, best_ports = name, ports
        return best or list(self.module_map.keys())[0]

    def get_module_hierarchy(self) -> dict[str, list[str]]:
        """Detect instantiations and build parent->children map."""
        hierarchy: dict[str, list[str]] = {}
        inst_re = re.compile(r'(\w+)\s+(?:#\s*\(.*?\)\s*)?(\w+)\s*\(', re.DOTALL)
        parent_module = None
        for name, info in self.module_map.items():
            text = info.files[0].read_text(encoding="utf-8", errors="replace")
            # Find module boundaries
            for m in self.MODULE_RE.finditer(text):
                parent_module = m.group(1)
                hierarchy.setdefault(parent_module, [])
            for m in inst_re.finditer(text):
                inst_module, inst_name = m.group(1), m.group(2)
                if inst_module != parent_module and inst_module in self.module_map:
                    hierarchy.setdefault(parent_module, []).append(inst_module)
        return hierarchy