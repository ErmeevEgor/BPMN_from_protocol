#!/usr/bin/env python3
"""Build the final validation report from independent validation gates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_contract import evaluate  # noqa: E402
from validate_model import validate_semantics  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def build(workspace: Path, model: dict, process_id: str, bpmn_requested: bool = True) -> tuple[Path, dict]:
    result = evaluate(workspace, model, process_id)
    steps = [step for step in (model.get("steps") or []) if isinstance(step, dict)]
    activities = [step for step in steps if step.get("task_type") not in {
        "start_event", "end_event", "intermediate_catch_event", "intermediate_throw_event"
    }]
    human_roles = [step for step in activities if step.get("performer_kind") == "human"]
    model_errors, model_warnings = validate_semantics(model)
    bpmn = workspace / "output" / "bpmn" / f"{process_id}.bpmn"
    png = workspace / "output" / "preview" / f"{process_id}.png"
    xml_validation_path = workspace / "output" / "validation" / f"{process_id}-bpmn-xml-validation.json"
    xml_validation = json.loads(xml_validation_path.read_text(encoding="utf-8")) if xml_validation_path.is_file() else {}
    di_validation_path = workspace / "output" / "validation" / f"{process_id}-bpmn-di-validation.json"
    di_validation = json.loads(di_validation_path.read_text(encoding="utf-8")) if di_validation_path.is_file() else {}
    drawio_report_path = workspace / "output" / "validation" / f"{process_id}-drawio-validation.json"
    drawio_report = json.loads(drawio_report_path.read_text(encoding="utf-8")) if drawio_report_path.is_file() else {}
    model_status = "PASS" if not model_errors else "FAIL"
    corporate_status = drawio_report.get("status", "NOT_RUN")
    geometry_status = "PASS" if corporate_status == "PASS" else corporate_status
    xml_status = xml_validation.get("status", "FAIL" if bpmn_requested else "NOT_REQUESTED")
    di_status = di_validation.get("status", "FAIL" if bpmn_requested else "NOT_REQUESTED")
    if model_status == "FAIL" or corporate_status == "FAIL" \
            or (bpmn_requested and (xml_status != "PASS" or di_status != "PASS")):
        overall = "FAIL"
    elif corporate_status != "PASS" or result["critical_knowledge_blockers"] \
            or not result["post_render_review_valid"]:
        overall = "NEEDS_REVIEW"
    else:
        overall = "PASS"
    result.update({
        "model_source_status": model_status, "bpmn_semantics_status": model_status,
        "corporate_notation_status": corporate_status, "geometry_status": geometry_status,
        "bpmn_xml_status": xml_status, "bpmn_di_status": di_status, "overall_status": overall,
    })
    lines = [
        "# Validation", "", f"Процесс: {model.get('process_name', process_id)}", "",
        f"Источник: {model.get('source_file', '')}", "",
        f"Шагов: {len(steps)}", f"Participants: {len(model.get('participants') or [])}",
        "Internal lanes: 0", f"Human role overlays: {len(human_roles)}",
        f"System overlays: {len(activities)}",
        f"Artifact occurrences: {sum(len(step.get('inputs') or []) + len(step.get('outputs') or []) for step in activities)}",
        f"Gateways: {len(model.get('gateways') or [])}",
        f"Information gaps: {len(model.get('information_gaps') or [])}", "",
        f"Model/source gate: {model_status}",
        f"BPMN semantics gate: {model_status}",
        f"Corporate notation gate: {corporate_status}",
        f"Geometry gate: {geometry_status}",
        f"PNG: {'PASS' if png.is_file() else 'SKIPPED'}",
        f"BPMN 2.0 XML gate: {xml_status}",
        f"BPMN-DI visual gate: {di_status}",
        f"Post-render review: {result['post_render_review_status']}",
        f"Post-render review validity: {'PASS' if result['post_render_review_valid'] else 'NEEDS_REVIEW'}",
        f"Post-render review note: {result['post_render_review_reason']}",
        "Model errors:",
    ]
    lines.extend(f"- {item}" for item in model_errors or ["none"])
    lines.extend(["", "Model warnings:"])
    lines.extend(f"- {item}" for item in model_warnings or ["none"])
    lines.extend(["", "Critical OPEN/ASSUMPTION blockers:"])
    lines.extend(f"- {item}" for item in result["critical_knowledge_blockers"])
    if not result["critical_knowledge_blockers"]:
        lines.append("- none")
    lines += ["", f"Общее состояние: {overall}", ""]
    report = workspace / "output" / "validation" / f"{process_id}-validation.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    return report, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_path")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--no-bpmn", action="store_true")
    args = parser.parse_args()
    model = json.loads(Path(args.model_path).read_text(encoding="utf-8"))
    report, result = build(Path(args.workspace).resolve(), model, model["process_id"], not args.no_bpmn)
    print(json.dumps({"report": str(report), **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
