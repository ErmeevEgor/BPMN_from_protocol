#!/usr/bin/env python3
"""Render the generated BPMN through bpmn-js, then rasterize that SVG."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_support import bpmn_node_dir  # noqa: E402


def _node_renderer() -> Path:
    explicit = os.environ.get("BPMN_JS_RENDERER")
    candidates = [
        Path(explicit) if explicit else None,
        bpmn_node_dir() / "render_bpmn.js",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file(): return candidate
    raise FileNotFoundError("render_bpmn.js not found; set BPMN_JS_RENDERER")


def render(bpmn: Path, svg: Path, png: Path) -> None:
    renderer = _node_renderer()
    if not (renderer.parent / "node_modules/bpmn-js/package.json").is_file():
        raise RuntimeError(f"bpmn-js dependencies missing in {renderer.parent}; run npm install there")
    node = shutil.which("node")
    if not node: raise RuntimeError("Node.js >=18 is required")
    svg.parent.mkdir(parents=True, exist_ok=True); png.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([node, str(renderer), str(bpmn), str(svg), str(png)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode or not svg.is_file() or svg.stat().st_size < 100 \
            or not png.is_file() or png.stat().st_size < 100:
        raise RuntimeError("bpmn-js render failed: " + result.stderr[-1000:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bpmn"); parser.add_argument("svg"); parser.add_argument("png"); args = parser.parse_args()
    try: render(Path(args.bpmn), Path(args.svg), Path(args.png))
    except (OSError, RuntimeError) as exc: print(f"BPMN_JS_RENDER_FAIL: {exc}", file=sys.stderr); return 1
    print(f"bpmn-js SVG: {args.svg}\nbpmn-js PNG: {args.png}"); return 0


if __name__ == "__main__": raise SystemExit(main())
