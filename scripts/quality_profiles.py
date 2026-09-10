#!/usr/bin/env python3
"""Machine-readable validation profiles for BPMN quality levels.

The profiles deliberately contain no modeling limits. All levels use the same
semantic model and requested deliverables; only validation and correction
effort differs.
"""
from __future__ import annotations

import json
from pathlib import Path


SHARED_CONTENT_CONTRACT = [
    "all source-backed actions, roles, systems, objects, gateways, exceptions, transfers, inputs, and outputs",
    "schema 2.1, source traceability, ROLE != SYSTEM, and valid graph semantics",
    "explicit information gaps instead of invented facts",
    "all user-requested output formats",
]

PROFILES = {
    "L1": {
        "name": "Basic validation",
        "target_minutes": [3, 7],
        "correction_rounds": 0,
        "actual_svg_geometry": False,
        "visual_inspections": 1,
        "cross_format_audit": False,
        "publish_visual_candidate_on_findings": True,
        "checks": [
            "model schema and semantic graph",
            "source traceability and ROLE != SYSTEM",
            "draw.io XML structure and basic endpoint references",
            "requested BPMN XML parse and BPMN-DI completeness",
            "quick full-SVG catastrophic-layout inspection",
        ],
    },
    "L2": {
        "name": "Standard validation",
        "target_minutes": [7, 15],
        "correction_rounds": 1,
        "actual_svg_geometry": True,
        "visual_inspections": 1,
        "cross_format_audit": True,
        "publish_visual_candidate_on_findings": False,
        "checks": [
            "all L1 checks",
            "actual-SVG connector and endpoint geometry",
            "edge, label, role, system, and artifact placement",
            "requested BPMN XML and BPMN-DI visual validation",
            "one complete draw.io and bpmn-js preview inspection",
        ],
    },
    "L3": {
        "name": "Extended validation",
        "target_minutes": [20, 40],
        "correction_rounds": 3,
        "actual_svg_geometry": True,
        "visual_inspections": 2,
        "cross_format_audit": True,
        "publish_visual_candidate_on_findings": False,
        "checks": [
            "all L2 checks",
            "element-by-element model, registry, draw.io, and BPMN comparison",
            "overview-scale and detail-scale inspection of every preview",
            "exhaustive connector, typography, whitespace, and artifact audit",
            "regression comparison when a reference case exists",
        ],
    },
}


def normalize_quality_level(value: str | None) -> str:
    level = str(value or "L2").strip().upper()
    if level not in PROFILES:
        raise ValueError(f"Unsupported quality level {value!r}; expected L1, L2, or L3")
    return level


def get_quality_profile(value: str | None) -> dict:
    level = normalize_quality_level(value)
    return {
        "quality_level": level,
        "content_contract": list(SHARED_CONTENT_CONTRACT),
        **PROFILES[level],
    }


def write_quality_profile(workspace: Path, process_id: str, value: str | None) -> Path:
    profile = get_quality_profile(value)
    output = workspace / "output" / "validation" / f"{process_id}-quality-profile.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output
