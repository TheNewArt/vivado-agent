import re
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("project_detector")


@dataclass
class ProjectFiles:
    rtl_files: list[Path]
    tb_files: list[Path]
    constraints: list[Path]
    top_module: str = ""
    project_name: str = ""
    source: str = ""  # xpr, filelist, scan


class ProjectDetector:
    """Auto-detect project files from .xpr, filelist, or directory scan."""

    HDL_EXTS = {".v", ".sv", ".vhd", ".vhdl"}
    TB_MARKERS = ("tb_", "_tb", "testbench", "test_bench")

    def detect(self, project_dir: str | Path) -> ProjectFiles:
        project_dir = Path(project_dir).resolve()
        logger.info(f"Detecting project files in: {project_dir}")

        # 1) Try .xpr
        xpr_files = list(project_dir.glob("*.xpr"))
        if xpr_files:
            return self._from_xpr(xpr_files[0])

        # 2) Try filelist
        for name in ("filelist.f", "files.f", "filelist.txt", "rtl.files"):
            fl = project_dir / name
            if fl.exists():
                return self._from_filelist(fl)

        # 3) Fallback: directory scan
        return self._from_scan(project_dir)

    def _from_xpr(self, xpr_path: Path) -> ProjectFiles:
        """Parse Vivado .xpr XML to extract file lists."""
        logger.info(f"Parsing XPR: {xpr_path}")
        try:
            tree = ET.parse(xpr_path)
            root = tree.getroot()
        except ET.ParseError as e:
            logger.warning(f"Failed to parse XPR: {e}")
            return self._from_scan(xpr_path.parent)

        ns = {"ns": "http://www.xilinx.com/XMLSchema"}
        # Try without namespace
        files = []
        for file_elem in root.iter("File"):
            path_attr = file_elem.get("Path") or file_elem.get("path")
            if path_attr:
                p = Path(path_attr)
                if p.suffix.lower() in self.HDL_EXTS:
                    files.append(p)
            for child in file_elem:
                if child.tag in ("Path", "path") and child.text:
                    p = Path(child.text)
                    if p.suffix.lower() in self.HDL_EXTS:
                        files.append(p)

        # Also search for file paths in text
        text = xpr_path.read_text(encoding="utf-8")
        for m in re.finditer(r'<Path>([^<]+\.(?:v|sv|vhd|vhdl))</Path>', text, re.IGNORECASE):
            p = Path(m.group(1))
            if p.suffix.lower() in self.HDL_EXTS:
                files.append(p)

        project_name = xpr_path.stem
        return self._classify_files(files, project_dir=xpr_path.parent, source="xpr", project_name=project_name)

    def _from_filelist(self, filelist_path: Path) -> ProjectFiles:
        """Parse filelist (one file per line, supports comments)."""
        files = []
        text = filelist_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "//", "--")):
                continue
            p = Path(line)
            if p.suffix.lower() in self.HDL_EXTS:
                files.append(p)
        return self._classify_files(files, project_dir=filelist_path.parent, source="filelist")

    def _from_scan(self, project_dir: Path) -> ProjectFiles:
        """Directory scan for HDL files."""
        files = []
        for ext in self.HDL_EXTS:
            for f in sorted(project_dir.rglob(f"*{ext}")):
                files.append(f)
        logger.info(f"Directory scan found {len(files)} HDL files")
        return self._classify_files(files, project_dir=project_dir, source="scan")

    def _classify_files(
        self, files: list[Path], project_dir: Path, source: str, project_name: str = ""
    ) -> ProjectFiles:
        rtl, tb, constraints = [], [], []
        # Resolve relative paths
        resolved = []
        for f in files:
            if not f.is_absolute():
                p = (project_dir / f).resolve()
            else:
                p = f.resolve()
            resolved.append(p)

        for p in resolved:
            name_lower = p.stem.lower()
            if any(m in name_lower for m in self.TB_MARKERS):
                tb.append(p)
            else:
                rtl.append(p)

        if not project_name:
            project_name = project_dir.name

        pf = ProjectFiles(
            rtl_files=rtl,
            tb_files=tb,
            constraints=constraints,
            source=source,
            project_name=project_name,
        )

        # Try to guess top module from design sources
        from src.tools.module_parser import ModuleParser
        parser = ModuleParser()
        for f in rtl:
            mods = parser.parse_file(f)
            for name in mods:
                if name.lower() == project_name.lower():
                    pf.top_module = name
                    break
            if pf.top_module:
                break

        logger.info(f"Project '{project_name}': {len(rtl)} RTL, {len(tb)} TB (from {source})")
        return pf