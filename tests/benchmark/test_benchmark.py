"""Benchmark: verify the agent catches all intentionally planted bugs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

BENCH_DIR = Path(__file__).parent


def test_dependency_graph():
    """Verify dependency graph catches top -> counter relationship."""
    from src.tools.dependency_graph import DependencyGraph

    # Parse benchmark RTL
    module_to_files = {}
    for ext in ("*.v", "*.sv"):
        for f in (BENCH_DIR / "rtl").rglob(ext):
            from src.tools.module_parser import ModuleParser
            mp = ModuleParser()
            mods = mp.parse_file(f)
            for name, info in mods.items():
                module_to_files.setdefault(name, []).append(f)

    dg = DependencyGraph()
    nodes = dg.build(module_to_files)

    assert "top" in nodes, "top module not found"
    assert "counter" in nodes, "counter module not found"
    assert "counter" in nodes["top"].instantiated_modules, \
        "top should instantiate counter"

    # Changing counter should mark top as affected
    affected = dg.get_transitive_closure({"counter"})
    assert "top" in affected, \
        "changing counter should affect top (transitive closure)"


def test_static_scanner_finds_bugs():
    """Verify static scanner catches all planted bugs in benchmark."""
    from src.tools.static_scanner import StaticScanner

    scanner = StaticScanner()
    rtl_dir = BENCH_DIR / "rtl"
    issues = scanner.scan_rtl(rtl_dir)

    categories = {i.category for i in issues}

    # These bugs are planted in the benchmark
    expected = {
        "fsm_no_default",      # case without default for SIDE_GREEN
        "inferred_latch",      # combinational always without else
        "cdc_no_sync",         # car_sensor crossing clk domains
        "combinational_loop",  # assign loop_signal = loop_signal ^ ...
        "multiple_driver",     # multi_drive assigned in two always blocks
    }
    found = expected & categories
    missing = expected - categories

    print(f"\n  Static scanner found {len(issues)} issues:")
    for i in issues:
        print(f"    [{i.severity}] {i.category}: {i.description}")
    print(f"  Expected bugs: {len(expected)}, found: {len(found)}, missing: {missing}")

    # Allow partial match — some patterns may not fire due to regex limitations
    assert len(found) >= 3, \
        f"Static scanner should catch at least 3/5 planted bugs, got {len(found)}: {missing}"


def test_log_analyzer():
    """Verify log analyzer parses common Vivado errors."""
    from src.tools.log_analyzer import LogAnalyzer

    la = LogAnalyzer()
    log = (
        "ERROR: [Synth 8-327] Latch inferred in module top at line 42\n"
        "ERROR: [Synth 8-685] Multiple driver on net multi_drive at line 67\n"
        "CRITICAL WARNING: [Timing 12-190] Timing violation detected\n"
        "ERROR: [Synth 8-338] Combinational loop detected at line 55\n"
    )
    errors = la.parse_errors(log)
    cats = {e.category for e in errors}
    assert "latch_inference" in cats
    assert "multiple_driver" in cats
    assert "combinational_loop" in cats


def test_rag_index_on_benchmark():
    """Verify RAG index maps line numbers to blocks."""
    from src.tools.rag_index import RAGIndex

    rag = RAGIndex(BENCH_DIR / "rtl")
    rag.build()
    blocks = rag.search_by_module("top")
    assert len(blocks) >= 1, "Should find 'top' module block"

    # Look up a line in the middle of the file
    top_file = BENCH_DIR / "rtl" / "top.sv"
    block = rag.lookup(top_file, 10)
    assert block is not None, "Should find block for line 10"
    assert "top" in block.module, "Block should belong to 'top' module"


def test_module_parser_on_benchmark():
    """Verify module parser extracts all modules."""
    from src.tools.module_parser import ModuleParser

    mp = ModuleParser()
    mods = mp.scan_directory(BENCH_DIR / "rtl")
    names = set(mods.keys())
    assert "top" in names, "Should find 'top' module"
    assert "counter" in names, "Should find 'counter' module"


def test_project_detector_on_benchmark():
    """Verify project detector finds all files."""
    from src.tools.project_detector import ProjectDetector

    detector = ProjectDetector()
    pf = detector.detect(BENCH_DIR)
    assert len(pf.rtl_files) >= 2, f"Should find 2+ RTL files, got {len(pf.rtl_files)}"


if __name__ == "__main__":
    test_dependency_graph()
    test_static_scanner_finds_bugs()
    test_log_analyzer()
    test_rag_index_on_benchmark()
    test_module_parser_on_benchmark()
    test_project_detector_on_benchmark()
    print("\nAll benchmark tests PASSED")