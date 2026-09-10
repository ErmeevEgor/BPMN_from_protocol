#!/usr/bin/env python3
"""Record one checkpoint in an L1 -> L2 -> L3 validation ladder."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from quality_profiles import get_quality_profile, normalize_quality_level


LEVELS = ("L1", "L2", "L3")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_paths(workspace: Path, process_id: str) -> dict[str, Path]:
    candidates = {
        "drawio": workspace / "output/drawio" / f"{process_id}.drawio",
        "svg": workspace / "output/preview" / f"{process_id}.svg",
        "bpmn": workspace / "output/bpmn" / f"{process_id}.bpmn",
        "bpmn_svg": workspace / "output/bpmn-preview" / f"{process_id}.svg",
    }
    return {name: path for name, path in candidates.items() if path.is_file()}


def report_status(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^Общее состояние:\s*(\S+)", text)
    return match.group(1) if match else "UNKNOWN"


def copy_revision(workspace: Path, process_id: str, revision: str,
                  artifacts: dict[str, Path]) -> Path:
    target = workspace / "output/revisions" / process_id / revision
    for name, source in artifacts.items():
        destination = target / name / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return target


def write_summary(path: Path, process_id: str, checkpoints: list[dict]) -> None:
    lines = [
        "# Validation ladder", "", f"Процесс: {process_id}", "",
        "| Уровень | Ревизия | Схема изменена | Исправлений | Статус |",
        "|---|---:|---|---:|---|",
    ]
    for item in checkpoints:
        lines.append(
            f"| {item['quality_level']} | {item['revision']} | "
            f"{'да' if item['diagram_changed'] else 'нет'} | "
            f"{item['corrections']} | {item['status']} |"
        )
    lines += [
        "", "Отдельная ревизия создается только после фактического изменения "
        "диаграммы. Одинаковые хэши означают, что следующий уровень проверял "
        "ту же визуальную версию глубже.", "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def record(workspace: Path, process_id: str, quality_level: str,
           corrections: int, notes: str = "") -> dict:
    level = normalize_quality_level(quality_level)
    profile = get_quality_profile(level)
    if corrections < 0 or corrections > profile["correction_rounds"]:
        raise ValueError(
            f"{level} allows 0..{profile['correction_rounds']} correction rounds"
        )

    validation_dir = workspace / "output/validation"
    report = validation_dir / f"{process_id}-validation.md"
    model = workspace / "output/models" / f"{process_id}-model.json"
    profile_path = validation_dir / f"{process_id}-quality-profile.json"
    if not report.is_file() or not model.is_file() or not profile_path.is_file():
        raise FileNotFoundError("Current model, quality profile, and validation report are required")
    current_profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if current_profile.get("quality_level") != level:
        raise ValueError("Current quality profile does not match the recorded level")

    artifacts = artifact_paths(workspace, process_id)
    if not {"drawio", "svg"}.issubset(artifacts):
        raise FileNotFoundError("Current draw.io and SVG artifacts are required")
    artifact_hashes = {name: sha256(path) for name, path in artifacts.items()}
    deliverable_hashes = {
        name: digest for name, digest in artifact_hashes.items()
        if name in {"drawio", "svg", "bpmn"}
    }
    diagram_fingerprint = hashlib.sha256(
        json.dumps(deliverable_hashes, sort_keys=True).encode("utf-8")
    ).hexdigest()
    model_hash = sha256(model)

    manifest_path = validation_dir / f"{process_id}-validation-ladder.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"process_id": process_id, "model_sha256": model_hash,
                    "revisions": [], "checkpoints": []}
    checkpoints = manifest["checkpoints"]
    expected = LEVELS[len(checkpoints)] if len(checkpoints) < len(LEVELS) else None
    if level != expected:
        raise ValueError(f"Expected next level {expected or 'none'}, got {level}")
    if manifest["model_sha256"] != model_hash:
        raise ValueError("MODEL_CONTENT_DRIFT: all validation levels must use one canonical model")

    previous = checkpoints[-1] if checkpoints else None
    changed = previous is not None and previous["diagram_sha256"] != diagram_fingerprint
    new_revision = previous is None or changed
    if changed and corrections == 0:
        raise ValueError("DIAGRAM_CHANGED_WITHOUT_DECLARED_CORRECTION")
    if previous is not None and not changed and corrections != 0:
        raise ValueError("CORRECTION_DECLARED_BUT_DIAGRAM_UNCHANGED")

    if new_revision:
        revision = f"r{len(manifest['revisions'])}"
        revision_path = copy_revision(workspace, process_id, revision, artifacts)
        manifest["revisions"].append({
            "revision": revision,
            "diagram_sha256": diagram_fingerprint,
            "artifact_sha256": artifact_hashes,
            "path": str(revision_path.relative_to(workspace)),
        })
    else:
        revision = previous["revision"]

    level_dir = validation_dir / "levels"
    level_dir.mkdir(parents=True, exist_ok=True)
    saved_report = level_dir / f"{process_id}-{level}-validation.md"
    saved_profile = level_dir / f"{process_id}-{level}-quality-profile.json"
    shutil.copy2(report, saved_report)
    shutil.copy2(profile_path, saved_profile)
    review = validation_dir / f"{process_id}-post-render-review.json"
    saved_review = None
    if review.is_file():
        saved_review = level_dir / f"{process_id}-{level}-post-render-review.json"
        shutil.copy2(review, saved_review)

    checkpoint = {
        "quality_level": level,
        "revision": revision,
        "diagram_changed": changed,
        "diagram_sha256": diagram_fingerprint,
        "artifact_sha256": artifact_hashes,
        "corrections": corrections,
        "status": report_status(report),
        "report": str(saved_report.relative_to(workspace)),
        "quality_profile": str(saved_profile.relative_to(workspace)),
        "post_render_review": str(saved_review.relative_to(workspace)) if saved_review else None,
        "notes": notes,
    }
    checkpoints.append(checkpoint)
    validation_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_path = validation_dir / f"{process_id}-validation-ladder.md"
    write_summary(summary_path, process_id, checkpoints)
    return {"manifest": str(manifest_path), "summary": str(summary_path), **checkpoint}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("process_id")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--quality-level", required=True, choices=LEVELS)
    parser.add_argument("--corrections", type=int, default=0)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    try:
        result = record(Path(args.workspace).resolve(), args.process_id,
                        args.quality_level, args.corrections, args.notes)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"VALIDATION_LADDER_REJECTED: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
