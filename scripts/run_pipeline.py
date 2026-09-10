#!/usr/bin/env python3
"""Validate process_model 2.0/2.1 and generate lane-less corporate artifacts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from build_validation_report import build as build_validation_report
from quality_profiles import normalize_quality_level, write_quality_profile


SCRIPTS = Path(__file__).resolve().parent


def run(command: list[str], cwd: Path) -> int:
    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--no-png", action="store_true")
    parser.add_argument("--bpmn", action="store_true")
    parser.add_argument("--quality-level", default="L2", choices=("L1", "L2", "L3"),
                        help="Validation depth only; model completeness is identical at every level")
    args = parser.parse_args()
    quality_level = normalize_quality_level(args.quality_level)
    source = Path(args.model).resolve()
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    model = json.loads(source.read_text(encoding="utf-8"))
    process_id = str(model.get("process_id") or "").strip()
    if not re.fullmatch(r"[0-9A-Za-zА-Яа-яЁё_.-]+", process_id):
        raise SystemExit("Invalid process_id")
    quality_profile_path = write_quality_profile(workspace, process_id, quality_level)
    canonical = workspace / "output/models" / f"{process_id}-model.json"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if source != canonical.resolve():
        shutil.copy2(source, canonical)
    stages = [
        [sys.executable, str(SCRIPTS / "normalize_model.py"), str(canonical)],
        [sys.executable, str(SCRIPTS / "validate_model.py"), str(canonical)],
        [sys.executable, str(SCRIPTS / "build_semantic_registry.py"), str(canonical), "-o",
         str(workspace / "output/registries" / f"{process_id}-semantic-registry.md")],
        [sys.executable, str(SCRIPTS / "build_information_gaps.py"), str(canonical), "-o",
         str(workspace / "output/registries" / f"{process_id}-information-gaps.md")],
        [sys.executable, str(SCRIPTS / "build_registry.py"), str(canonical)],
    ]
    for command in stages:
        code = run(command, workspace)
        if code:
            return code
    registry = workspace / "output/registries" / f"{process_id}-registry.md"
    render = [
        sys.executable, str(SCRIPTS / "render_diagram.py"), str(registry), process_id,
        "--model", str(canonical), "--quality-level", quality_level,
    ]
    if args.no_png:
        render.append("--no-png")
    code = run(render, workspace)
    if code:
        return code
    bpmn = None
    bpmn_svg = None
    bpmn_png = None
    if args.bpmn:
        bpmn = workspace / "output/bpmn" / f"{process_id}.bpmn"
        drawio = workspace / "output/drawio" / f"{process_id}.drawio"
        render_meta = workspace / "output/registries" / f"{process_id}-render-meta.json"
        code = run([
            sys.executable, str(SCRIPTS / "export_bpmn_xml.py"), str(registry), str(bpmn),
            "--model", str(canonical), "--drawio", str(drawio), "--render-meta", str(render_meta),
            "--validation-report",
            str(workspace / "output/validation" / f"{process_id}-bpmn-xml-validation.json"),
        ], workspace)
        if code:
            return code
        if quality_level != "L1":
            bpmn_svg = workspace / "output/bpmn-preview" / f"{process_id}.svg"
            bpmn_png = workspace / "output/bpmn-preview" / f"{process_id}.png"
            code = run([sys.executable, str(SCRIPTS / "render_bpmn.py"), str(bpmn), str(bpmn_svg), str(bpmn_png)], workspace)
            if code:
                return code
            code = run([
                sys.executable, str(SCRIPTS / "validate_bpmn_di.py"), str(bpmn), "--model", str(canonical),
                "--svg", str(bpmn_svg), "--png", str(bpmn_png), "--report",
                str(workspace / "output/validation" / f"{process_id}-bpmn-di-validation.json"),
            ], workspace)
            if code:
                return code
    model = json.loads(canonical.read_text(encoding="utf-8"))
    report, result = build_validation_report(
        workspace, model, process_id, bpmn_requested=args.bpmn, quality_level=quality_level,
    )
    print(json.dumps({
        "model": str(canonical),
        "semantic_registry": str(workspace / "output/registries" / f"{process_id}-semantic-registry.md"),
        "information_gaps": str(workspace / "output/registries" / f"{process_id}-information-gaps.md"),
        "render_meta": str(workspace / "output/registries" / f"{process_id}-render-meta.json"),
        "drawio": str(workspace / "output/drawio" / f"{process_id}.drawio"),
        "png": str(workspace / "output/preview" / f"{process_id}.png"),
        "bpmn": str(bpmn) if bpmn else None,
        "bpmn_svg": str(bpmn_svg) if bpmn_svg else None,
        "bpmn_png": str(bpmn_png) if bpmn_png else None,
        "quality_level": quality_level,
        "quality_profile": str(quality_profile_path),
        "validation": str(report), "status": result["overall_status"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
