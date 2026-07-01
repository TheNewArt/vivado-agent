import re
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass, field
from src.utils.logger import setup_logger

logger = setup_logger("project_detector")


DEVICE_FAMILIES = {
    "7series":  ["xc7", "xa7", "xc7z"],
    "ultrascale": ["xcku", "xcku", "xcku"],
    "ultrascale_plus": ["xcku", "xcku", "xcku", "xcku", "xcku", "xcku", "xcku", "xcku", "xcku", "xcku"],
    "versal":   ["xvc", "xvm", "xve"],
    "aie":      ["aie"],
}


@dataclass
class ProjectFiles:
    rtl_files: list[Path]
    tb_files: list[Path]
    constraints: list[Path]
    top_module: str = ""
    project_name: str = ""
    source: str = ""  # xpr, filelist, scan
    device: str = ""  # xc7a35t, xcku040, etc.
    device_family: str = ""  # 7series, ultrascale, versal, aie
    vivado_version: str = ""
    has_petalinux: bool = False
    has_vitis_hls: bool = False
    has_hls_source: bool = False


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
        pf = self._from_scan(project_dir)
        # 4) Detect PetaLinux / Vitis HLS / HLS sources
        self._detect_extras(project_dir, pf)
        return pf

    @staticmethod
    def _detect_extras(project_dir: Path, pf: ProjectFiles):
        """Detect PetaLinux, Vitis HLS, and HLS sources."""
        # PetaLinux: look for petalinux project markers
        petalinux_markers = [
            project_dir / "petalinux" / "config.project",
            project_dir / "project-spec" / "config.project",
            project_dir / "meta-user",
        ]
        pf.has_petalinux = any(m.exists() for m in petalinux_markers)

        # Vitis HLS: look for .tcl with HLS directives
        hls_scripts = list(project_dir.rglob("*.tcl"))
        for tcl in hls_scripts:
            if tcl.exists():
                text = tcl.read_text(encoding="utf-8", errors="replace")
                if "open_solution" in text or "csynth_design" in text:
                    pf.has_vitis_hls = True
                    break

        # HLS source files (.cpp, .c with HLS pragmas)
        for ext in ("*.cpp", "*.c"):
            for f in project_dir.rglob(ext):
                if f.exists():
                    text = f.read_text(encoding="utf-8", errors="replace")
                    if "HLS" in text or "hls" in text or "ap_int" in text or "ap_fixed" in text:
                        pf.has_hls_source = True
                        break

        if pf.has_petalinux:
            logger.info(f"  PetaLinux project detected")
        if pf.has_vitis_hls:
            logger.info(f"  Vitis HLS project detected")
        if pf.has_hls_source:
            logger.info(f"  HLS source files detected")

    def _from_xpr(self, xpr_path: Path) -> ProjectFiles:
        """Parse Vivado .xpr XML to extract file lists and metadata."""
        logger.info(f"Parsing XPR: {xpr_path}")
        text = xpr_path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ET.parse(xpr_path)
            root = tree.getroot()
        except ET.ParseError as e:
            logger.warning(f"Failed to parse XPR: {e}")
            return self._from_scan(xpr_path.parent)

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
        for m in re.finditer(r'<Path>([^<]+\.(?:v|sv|vhd|vhdl))</Path>', text, re.IGNORECASE):
            p = Path(m.group(1))
            if p.suffix.lower() in self.HDL_EXTS:
                files.append(p)

        project_name = xpr_path.stem

        # Extract device part
        device = ""
        family = ""
        m = re.search(r'<Part>\s*([^<]+)\s*</Part>', text)
        if m:
            device = m.group(1).strip()
            # Determine family
            device_lower = device.lower()
            if any(device_lower.startswith(p) for p in ["xc7", "xa7", "xc7z"]):
                family = "7series"
            elif any(device_lower.startswith(p) for p in ["xcku", "xcku"]):
                family = "ultrascale"
            elif any(device_lower.startswith(p) for p in ["xcku", "xcku", "xcku",
                                                           "xcku", "xcku", "xcku",
                                                           "xcku", "xcku", "xcku"]):
                family = "ultrascale_plus"
            elif any(device_lower.startswith(p) for p in ["xvc", "xvm", "xve"]):
                family = "versal"
            elif "aie" in device_lower:
                family = "aie"

        # Extract Vivado version
        vivado_version = ""
        m = re.search(r'<ProductVersion>\s*([^<]+)\s*</ProductVersion>', text)
        if m:
            vivado_version = m.group(1).strip()
        # Also try RV design version
        if not vivado_version:
            m = re.search(r'<!--\s*Product version:\s*([^<]+?)\s*-->', text)
            if m:
                vivado_version = m.group(1).strip()

        pf = self._classify_files(files, project_dir=xpr_path.parent, source="xpr", project_name=project_name)
        pf.device = device
        pf.device_family = family
        pf.vivado_version = vivado_version
        self._detect_extras(xpr_path.parent, pf)
        return pf

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