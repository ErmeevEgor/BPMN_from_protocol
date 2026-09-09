#!/usr/bin/env python3
"""Build a human-readable semantic registry from process_model 2.1."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


COLUMNS = [
    "ID", "Codes", "Parent/Subprocess", "Performer", "Role", "System", "Action", "BPMN Type",
    "Business Object", "Interface/Method", "Basis", "Observable Result",
    "Verification Criterion", "Input", "Output", "Control", "Condition",
    "Exceptions", "Knowledge Status", "Source",
]


def esc(value) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", "<br>").strip()


def names(items) -> str:
    values = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or "")
        if item.get("state"):
            label += f" [{item['state']}]"
        if item.get("logical_object_id"):
            label += f" <{item['logical_object_id']}>"
        if item.get("kind"):
            label += f" {{{item['kind']}}}"
        values.append(label)
    return "; ".join(values)


def source_text(refs) -> str:
    result = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        location = ", ".join(
            part for part in (str(ref.get("section") or "").strip(), str(ref.get("locator") or "").strip()) if part
        )
        label = str(ref.get("source_file") or "").strip()
        if location:
            label += f" ({location})"
        if ref.get("excerpt"):
            label += f": {ref['excerpt']}"
        if label:
            result.append(label)
    return "; ".join(result)


def build(model: dict) -> str:
    lines = [f"# Semantic registry — {model.get('process_name', model.get('process_id', ''))}", ""]
    lines.append("| " + " | ".join(COLUMNS) + " |")
    lines.append("|" + "|".join("---" for _ in COLUMNS) + "|")
    for step in model.get("steps") or []:
        if not isinstance(step, dict):
            continue
        business_object = step.get("business_object") if isinstance(step.get("business_object"), dict) else {}
        object_text = business_object.get("name", "")
        if business_object.get("kind"):
            object_text = f"{object_text} [{business_object['kind']}]"
        row = [
            step.get("id", ""), ", ".join(str(x) for x in step.get("display_codes") or []),
            step.get("parent_id", ""), step.get("performer_kind", ""), step.get("role", ""),
            step.get("system", ""), step.get("action", ""), step.get("task_type", ""),
            object_text,
            " / ".join(value for value in (
                str(step.get("system") or "").strip(),
                str(step.get("execution_channel") or "").strip(),
            ) if value),
            step.get("basis", ""), step.get("observable_result", ""),
            step.get("verification_criterion", ""),
            names(step.get("inputs")), names(step.get("outputs")),
            "; ".join(str(value) for value in (step.get("control") or [])),
            step.get("condition", ""), "; ".join(str(value) for value in (step.get("exception_refs") or [])),
            step.get("knowledge_status", ""), source_text(step.get("source_refs")),
        ]
        lines.append("| " + " | ".join(esc(value) for value in row) + " |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_path")
    parser.add_argument("-o", "--out")
    args = parser.parse_args()
    model_path = Path(args.model_path)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    process_id = model.get("process_id", "process")
    out = Path(args.out) if args.out else Path("output/registries") / f"{process_id}-semantic-registry.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(model), encoding="utf-8")
    print(f"Semantic registry saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
