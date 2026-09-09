#!/usr/bin/env python3
"""Host-neutral runtime discovery shared by the portable BPMN skill."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil


SKILL_ROOT = Path(__file__).resolve().parent.parent


def _usable_file(value: str | os.PathLike[str] | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path.resolve()) if path.is_file() else None


def _config_candidates(workspace: Path | None = None) -> list[Path]:
    roots = []
    if workspace is not None:
        roots.append(Path(workspace))
    roots.extend((Path.cwd(), SKILL_ROOT))
    result: list[Path] = []
    for root in roots:
        candidate = root / "config" / "tooling.json"
        if candidate not in result:
            result.append(candidate)
    return result


def configured_tool(name: str, workspace: Path | None = None) -> str | None:
    for config in _config_candidates(workspace):
        if not config.is_file():
            continue
        try:
            value = json.loads(config.read_text(encoding="utf-8")).get(name)
        except (OSError, json.JSONDecodeError):
            continue
        hit = _usable_file(value)
        if hit:
            return hit
    return None


def find_drawio_cli(workspace: Path | None = None) -> str | None:
    """Resolve draw.io without assuming one agent, OS, or install directory."""
    if os.environ.get("BPMN_DISABLE_DRAWIO", "").strip().lower() in {"1", "true", "yes"}:
        return None
    hit = _usable_file(os.environ.get("DRAWIO_CLI"))
    if hit:
        return hit
    hit = configured_tool("drawio_cli", workspace)
    if hit:
        return hit
    for command in ("drawio", "draw.io", "drawio.exe", "draw.io.exe"):
        hit = shutil.which(command)
        if hit:
            return hit
    candidates = (
        Path("C:/Program Files/draw.io/draw.io.exe"),
        Path("C:/Program Files (x86)/draw.io/draw.io.exe"),
        Path("/Applications/draw.io.app/Contents/MacOS/draw.io"),
        Path("/usr/bin/drawio"),
        Path("/usr/local/bin/drawio"),
        Path("/snap/bin/drawio"),
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def find_libreoffice() -> str | None:
    explicit = _usable_file(os.environ.get("LIBREOFFICE_CLI"))
    if explicit:
        return explicit
    for command in ("soffice", "soffice.exe", "libreoffice"):
        hit = shutil.which(command)
        if hit:
            return hit
    candidates = (
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def vendor_scripts_dir() -> Path:
    packaged = SKILL_ROOT / "vendor" / "bpmn-diagrams" / "scripts"
    if packaged.is_dir():
        return packaged
    repository_copy = SKILL_ROOT / "portable-skills" / "bpmn-from-protocol" / "vendor" / "bpmn-diagrams" / "scripts"
    return repository_copy


def bpmn_node_dir() -> Path:
    return vendor_scripts_dir() / "node"
