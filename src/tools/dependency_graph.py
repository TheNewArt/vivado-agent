import re
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("dependency_graph")


@dataclass
class ModuleNode:
    name: str
    files: list[Path]
    instantiated_modules: set[str] = field(default_factory=set)
    instantiated_by: set[str] = field(default_factory=set)


class DependencyGraph:
    """
    RTL module dependency graph (DAG).
    Parses module instantiations to build parent->child and child->parent relationships.
    Used for dependency-aware incremental compilation: if module B changes,
    all modules that instantiate B must also be recompiled.
    """

    # Match: module_name #(params) instance_name ( ports );
    # Must NOT match: if (cond), for (i=0), always @(posedge)
    INST_RE = re.compile(
        r'(?:^|\s)([a-zA-Z_]\w*)\s+'           # module name (capture)
        r'(?:#\s*\([^)]*\)\s*)?'               # optional parameter override
        r'([a-zA-Z_]\w*)\s*'                    # instance name (capture)
        r'\(',                                  # start of port list
    )
    SKIP_KEYWORDS = {
        'if', 'else', 'for', 'while', 'case', 'always', 'initial',
        'assign', 'begin', 'end', 'module', 'input', 'output',
        'wire', 'reg', 'logic', 'integer', 'real', 'time',
        'posedge', 'negedge', 'or', 'and', 'not', 'wait', 'repeat',
        'forever', 'fork', 'join', 'disable',
    }

    def __init__(self):
        self.nodes: dict[str, ModuleNode] = {}

    def build(self, module_to_files: dict[str, list[Path]]) -> dict[str, ModuleNode]:
        """Build dependency graph from module->files mapping."""
        self.nodes = {}
        for name, files in module_to_files.items():
            self.nodes[name] = ModuleNode(name=name, files=files)

        # Parse each module's files for instantiations
        for name, node in self.nodes.items():
            for f in node.files:
                insts = self._parse_instantiations(f)
                for inst_name in insts:
                    if inst_name in self.nodes and inst_name != name:
                        node.instantiated_modules.add(inst_name)
                        self.nodes[inst_name].instantiated_by.add(name)

        # Log summary
        total_edges = sum(len(n.instantiated_modules) for n in self.nodes.values())
        logger.info(f"Dependency graph: {len(self.nodes)} nodes, {total_edges} edges")
        for name, node in self.nodes.items():
            if node.instantiated_modules:
                logger.debug(f"  {name} -> {{{', '.join(node.instantiated_modules)}}}")

        return self.nodes

    @classmethod
    def _parse_instantiations(cls, file_path: Path) -> set[str]:
        """Parse a single file and return set of instantiated module names."""
        if not file_path.is_file():
            return set()
        text = file_path.read_text(encoding="utf-8", errors="replace")

        # Remove comments first to avoid false positives
        text = cls._remove_comments(text)

        insts = set()
        for m in cls.INST_RE.finditer(text):
            mod_name = m.group(1)
            # Skip Verilog keywords and common false positives
            if mod_name.lower() in cls.SKIP_KEYWORDS:
                continue
            # Skip if it looks like a type declaration (wire/reg/logic)
            prev_char = text[max(0, m.start() - 1)]
            if prev_char.isalpha() or prev_char == '_':
                continue
            insts.add(mod_name)

        return insts

    @staticmethod
    def _remove_comments(text: str) -> str:
        """Remove Verilog/SystemVerilog comments."""
        # Remove block comments /* ... */
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        # Remove line comments // ...
        text = re.sub(r'//.*$', '', text, flags=re.MULTILINE)
        return text

    def get_transitive_closure(self, changed_modules: set[str]) -> set[str]:
        """
        Given a set of changed modules, compute the transitive closure:
        all modules that directly or indirectly depend on any changed module.
        """
        affected = set(changed_modules)
        queue = list(changed_modules)

        while queue:
            mod = queue.pop(0)
            # Find all modules that instantiate this module
            if mod in self.nodes:
                for parent in self.nodes[mod].instantiated_by:
                    if parent not in affected:
                        affected.add(parent)
                        queue.append(parent)

        return affected

    def get_affected_modules(self, changed_files: list[Path]) -> tuple[set[str], set[str]]:
        """
        Return (directly_changed, transitively_affected).
        transitively_affected includes directly_changed plus all upstream dependents.
        """
        # Find which modules are in the changed files
        direct = set()
        for f in changed_files:
            for name, node in self.nodes.items():
                if f in node.files:
                    direct.add(name)

        transitive = self.get_transitive_closure(direct)
        return direct, transitive

    def get_compile_order(self) -> list[str]:
        """Return modules in bottom-up compile order (dependencies first)."""
        visited = set()
        order = []

        def dfs(node_name):
            if node_name in visited:
                return
            visited.add(node_name)
            node = self.nodes.get(node_name)
            if node:
                for dep in node.instantiated_modules:
                    dfs(dep)
                order.append(node_name)

        for name in self.nodes:
            dfs(name)

        return order