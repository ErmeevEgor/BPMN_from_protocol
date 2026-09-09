#!/usr/bin/env python3
"""Normalize legacy/process_model 2.0 input into canonical schema 2.1."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from model_contract import normalize_to_v2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_path")
    parser.add_argument("-o", "--out", help="Output path; defaults to in-place replacement")
    parser.add_argument("--report", help="Optional JSON report with deterministic changes")
    args = parser.parse_args()

    source = Path(args.model_path)
    target = Path(args.out) if args.out else source
    model = json.loads(source.read_text(encoding="utf-8"))
    changes: list[str] = []
    normalized = normalize_to_v2(model, source_model_path=source, changes=changes)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"model": str(target), "changes": changes}, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(f"Canonical schema 2.1 saved: {target}")
    for change in changes:
        print(f"  - {change}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
