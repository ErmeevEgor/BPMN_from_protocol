#!/usr/bin/env python3
"""Report portable BPMN skill runtime capabilities without changing the host."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from runtime_support import bpmn_node_dir, find_drawio_cli, find_libreoffice


PYTHON_IMPORTS = {
    "python-docx": "docx",
    "beautifulsoup4": "bs4",
    "pdfplumber": "pdfplumber",
    "openpyxl": "openpyxl",
}
SUPPORTED_EXTENSIONS = [
    ".txt", ".md", ".html", ".docx", ".doc", ".pdf",
    ".xlsx", ".xlsm", ".csv", ".tsv",
]


def _version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0] if result.returncode == 0 and text else None


def inspect(workspace: Path | None = None) -> dict:
    packages = {name: importlib.util.find_spec(module) is not None
                for name, module in PYTHON_IMPORTS.items()}
    node = shutil.which("node")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    node_dir = bpmn_node_dir()
    bpmn_modules = node_dir / "node_modules" / "bpmn-js" / "package.json"
    drawio = find_drawio_cli(workspace)
    libreoffice = find_libreoffice()
    python_ok = sys.version_info >= (3, 11)
    base_ready = python_ok
    all_extractors = all(packages.values())
    bpmn_ready = base_ready and bool(node) and bpmn_modules.is_file()
    svg_ready = base_ready and bool(drawio)
    status = "READY_FULL" if all_extractors and bpmn_ready and svg_ready else "READY_BASE" if base_ready else "NOT_READY"
    return {
        "status": status,
        "python": {"executable": sys.executable, "version": sys.version.split()[0], "ok": python_ok},
        "python_packages": packages,
        "node": {"path": node, "version": _version([node, "--version"]) if node else None},
        "npm": {"path": npm, "version": _version([npm, "--version"]) if npm else None},
        "bpmn_js_dependencies": {"path": str(node_dir), "installed": bpmn_modules.is_file()},
        "drawio_cli": {"path": drawio, "available": bool(drawio)},
        "libreoffice": {"path": libreoffice, "available": bool(libreoffice), "required_for": [".doc"]},
        "capabilities": {
            "source_extraction": base_ready,
            "all_document_extractors": all_extractors,
            "drawio_generation": base_ready,
            "drawio_svg_and_validation": svg_ready,
            "bpmn_xml_and_preview": bpmn_ready,
        },
        "supported_extensions": SUPPORTED_EXTENSIONS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require", choices=("base", "svg", "png", "bpmn", "full"))
    args = parser.parse_args()
    result = inspect(args.workspace)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Runtime: {result['status']}")
        print(f"Python {result['python']['version']}: {'PASS' if result['python']['ok'] else 'FAIL'}")
        for package, available in result["python_packages"].items():
            print(f"Python package {package}: {'PASS' if available else 'MISSING'}")
        print(f"Node.js: {result['node']['version'] or 'MISSING'}")
        print(f"bpmn-js dependencies: {'PASS' if result['bpmn_js_dependencies']['installed'] else 'MISSING'}")
        print(f"draw.io CLI: {result['drawio_cli']['path'] or 'MISSING (draw.io remains deliverable; SVG preview is unavailable)'}")
        print(f"LibreOffice: {result['libreoffice']['path'] or 'MISSING (only legacy .doc is unavailable)'}")
    required = {
        "base": result["capabilities"]["source_extraction"],
        "svg": result["capabilities"]["drawio_svg_and_validation"],
        "png": result["capabilities"]["drawio_svg_and_validation"],
        "bpmn": result["capabilities"]["bpmn_xml_and_preview"],
        "full": result["status"] == "READY_FULL",
    }
    return 0 if not args.require or required[args.require] else 1


if __name__ == "__main__":
    raise SystemExit(main())
