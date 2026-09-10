#!/usr/bin/env python3
"""Evaluate the separate technical, knowledge, and post-render review gates."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path


UNRESOLVED = {"OPEN", "ASSUMPTION"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def critical_unresolved(model: dict) -> list[str]:
    blockers: list[str] = []
    for gap in model.get("information_gaps") or []:
        if not isinstance(gap, dict):
            continue
        if str(gap.get("severity") or "").lower() == "critical" \
                and str(gap.get("status") or "").upper() in UNRESOLVED:
            blockers.append(
                f"information gap {gap.get('step_id', '?')}: {gap.get('missing_information', '')}"
            )
    for step in model.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("criticality") or "").lower() == "critical" \
                and str(step.get("knowledge_status") or "").upper() in UNRESOLVED:
            blockers.append(
                f"critical Activity {step.get('id', '?')}: {step.get('knowledge_status')}"
            )
    return blockers


def _valid_post_render_review(review_path: Path, drawio: Path, svg: Path,
                              png: Path | None = None,
                              bpmn: Path | None = None, bpmn_svg: Path | None = None,
                              bpmn_png: Path | None = None) -> tuple[bool, str, dict]:
    if not review_path.is_file():
        return False, "post-render review отсутствует", {}
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"post-render review не читается: {exc}", {}
    if review.get("status") != "PASS":
        return False, f"post-render review status={review.get('status', 'UNKNOWN')}", review
    if not drawio.is_file() or not svg.is_file():
        return False, "артефакты для post-render review отсутствуют", review
    hashes = review.get("artifact_sha256") or {}
    artifacts = {"drawio": drawio, "svg": svg}
    if "png" in hashes:
        artifacts["png"] = png
    if bpmn is not None and bpmn.is_file():
        artifacts.update({"bpmn": bpmn, "bpmn_svg": bpmn_svg})
        if "bpmn_png" in hashes:
            artifacts["bpmn_png"] = bpmn_png
    if any(path is None or not path.is_file() or hashes.get(name) != sha256(path)
           for name, path in artifacts.items()):
        return False, "post-render review относится к другой версии артефактов", review
    try:
        reviewed_at = datetime.fromisoformat(str(review.get("reviewed_at") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False, "reviewed_at отсутствует или имеет неверный формат", review
    if reviewed_at + 1 < max(path.stat().st_mtime for path in artifacts.values() if path is not None):
        return False, "post-render review создан раньше текущего рендера", review
    return True, "post-render review подтверждён", review


def evaluate(workspace: Path, model: dict, process_id: str) -> dict:
    validation_dir = workspace / "output" / "validation"
    drawio_validation = validation_dir / f"{process_id}-drawio-validation.json"
    technical = {}
    if drawio_validation.is_file():
        technical = json.loads(drawio_validation.read_text(encoding="utf-8"))
    technical_status = technical.get("status", "NOT_RUN")
    drawio = workspace / "output" / "drawio" / f"{process_id}.drawio"
    svg = workspace / "output" / "preview" / f"{process_id}.svg"
    png = workspace / "output" / "preview" / f"{process_id}.png"
    bpmn = workspace / "output" / "bpmn" / f"{process_id}.bpmn"
    bpmn_svg = workspace / "output" / "bpmn-preview" / f"{process_id}.svg"
    bpmn_png = workspace / "output" / "bpmn-preview" / f"{process_id}.png"
    review_path = validation_dir / f"{process_id}-post-render-review.json"
    review_valid, review_reason, review = _valid_post_render_review(
        review_path, drawio, svg, png, bpmn, bpmn_svg, bpmn_png)
    critical = critical_unresolved(model)
    if technical_status != "PASS":
        overall = "FAIL"
    elif critical or not review_valid:
        overall = "NEEDS_REVIEW"
    else:
        overall = "PASS"
    return {
        "overall_status": overall,
        "technical_status": technical_status,
        "post_render_review_status": review.get("status", "NOT_RECORDED"),
        "post_render_review_valid": review_valid,
        "post_render_review_reason": review_reason,
        "critical_knowledge_blockers": critical,
        "post_render_review_path": str(review_path),
    }
