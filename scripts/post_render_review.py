#!/usr/bin/env python3
"""Record a human/agent visual decision only after draw.io and PNG exist."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_contract import sha256  # noqa: E402


DEFAULT_CHECKS = [
    "Task labels are readable and use only short operational names",
    "no clipped labels or label overlaps",
    "no unbridged edge-edge intersections",
    "every Sequence/Message/Data connector visibly touches both endpoint contours",
    "backward arrows use readable external corridors",
    "diagram scale and whitespace are suitable for reading",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("process_id")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--status", required=True, choices=("PASS", "FAIL"))
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    drawio = workspace / "output" / "drawio" / f"{args.process_id}.drawio"
    png = workspace / "output" / "preview" / f"{args.process_id}.png"
    technical_path = workspace / "output" / "validation" / f"{args.process_id}-drawio-validation.json"
    bpmn = workspace / "output" / "bpmn" / f"{args.process_id}.bpmn"
    bpmn_svg = workspace / "output" / "bpmn-preview" / f"{args.process_id}.svg"
    bpmn_png = workspace / "output" / "bpmn-preview" / f"{args.process_id}.png"
    bpmn_validation_path = workspace / "output" / "validation" / f"{args.process_id}-bpmn-di-validation.json"
    required = [drawio, png, technical_path]
    if bpmn.is_file():
        required.extend([bpmn_svg, bpmn_png, bpmn_validation_path])
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        print("POST_RENDER_REVIEW_BEFORE_RENDER_FORBIDDEN: " + ", ".join(missing), file=sys.stderr)
        return 2
    technical = json.loads(technical_path.read_text(encoding="utf-8"))
    if technical.get("status") != "PASS":
        print("POST_RENDER_REVIEW_REQUIRES_TECHNICAL_PASS", file=sys.stderr)
        return 2
    if bpmn.is_file():
        bpmn_technical = json.loads(bpmn_validation_path.read_text(encoding="utf-8"))
        if bpmn_technical.get("status") != "PASS":
            print("POST_RENDER_REVIEW_REQUIRES_BPMN_DI_PASS", file=sys.stderr)
            return 2
    hashes = {"drawio": sha256(drawio), "png": sha256(png)}
    if bpmn.is_file():
        hashes.update({"bpmn": sha256(bpmn), "bpmn_svg": sha256(bpmn_svg), "bpmn_png": sha256(bpmn_png)})
    payload = {
        "process_id": args.process_id,
        "status": args.status,
        "reviewer": args.reviewer,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "artifact_sha256": hashes,
        "checks": DEFAULT_CHECKS,
        "notes": args.notes,
    }
    output = workspace / "output" / "validation" / f"{args.process_id}-post-render-review.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Post-render review recorded: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
