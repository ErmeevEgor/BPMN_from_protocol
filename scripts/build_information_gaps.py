#!/usr/bin/env python3
"""Build the customer-facing information-gaps register from process_model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


COLUMNS = ("step_id", "Чего не хватает", "Влияние на схему", "Вопрос заказчику", "Статус")


def esc(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def build(model: dict) -> str:
    lines = [f"# Information gaps — {model.get('process_name', model.get('process_id', ''))}", ""]
    lines += ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for gap in model.get("information_gaps") or []:
        if not isinstance(gap, dict):
            continue
        lines.append("| " + " | ".join(esc(gap.get(key)) for key in (
            "step_id", "missing_information", "diagram_impact", "customer_question", "status"
        )) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_path")
    parser.add_argument("-o", "--out")
    args = parser.parse_args()
    model = json.loads(Path(args.model_path).read_text(encoding="utf-8"))
    process_id = model.get("process_id", "process")
    out = Path(args.out) if args.out else Path("output/registries") / f"{process_id}-information-gaps.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(model), encoding="utf-8")
    print(f"Information gaps saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
