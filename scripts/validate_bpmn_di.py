#!/usr/bin/env python3
"""Validate portable BPMN-DI geometry and bpmn-js render evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shape_geometry import DIAMOND, ELLIPSE, RECTANGLE, point_on_perimeter  # noqa: E402

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}


def q(prefix, tag): return f"{{{NS[prefix]}}}{tag}"


def local(tag): return tag.rsplit("}", 1)[-1]


def _bounds(element):
    bounds = element.find(q("dc", "Bounds"))
    if bounds is None: return None
    x, y = float(bounds.get("x", 0)), float(bounds.get("y", 0))
    return x, y, x + float(bounds.get("width", 0)), y + float(bounds.get("height", 0))


def _points(edge):
    return [(float(x.get("x", 0)), float(x.get("y", 0))) for x in edge.findall(q("di", "waypoint"))]


def _shape_kind(element):
    tag = local(element.tag) if element is not None else ""
    if tag.endswith("Gateway"): return DIAMOND
    if tag.endswith("Event"): return ELLIPSE
    return RECTANGLE


def _segments(points):
    return [(a, b) for a, b in zip(points, points[1:]) if abs(a[0] - b[0]) + abs(a[1] - b[1]) > .01]


def _orientation(a, b, c):
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    return 0 if abs(value) < .01 else (1 if value > 0 else 2)


def _segment_relation(a, b, c, d):
    # Long collinear shared sections are always blocking.
    if _orientation(a, b, c) == _orientation(a, b, d) == 0:
        if abs(a[0] - b[0]) >= abs(a[1] - b[1]):
            overlap = min(max(a[0], b[0]), max(c[0], d[0])) - max(min(a[0], b[0]), min(c[0], d[0]))
        else:
            overlap = min(max(a[1], b[1]), max(c[1], d[1])) - max(min(a[1], b[1]), min(c[1], d[1]))
        return "overlap" if overlap > 1 else None
    if _orientation(a, b, c) != _orientation(a, b, d) and _orientation(c, d, a) != _orientation(c, d, b):
        return "cross"
    return None


def _segment_intersects_rect(a, b, rect, margin=1.0):
    x0, y0, x1, y1 = rect
    x0 += margin; y0 += margin; x1 -= margin; y1 -= margin
    if x0 >= x1 or y0 >= y1: return False
    if x0 < a[0] < x1 and y0 < a[1] < y1: return True
    if x0 < b[0] < x1 and y0 < b[1] < y1: return True
    sides = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
             ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    return any(_segment_relation(a, b, c, d) == "cross" for c, d in sides)


def _rect_overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _data_endpoints(root):
    result = {}
    for activity in root.iter():
        activity_id = activity.get("id")
        if not activity_id: continue
        for association in list(activity):
            kind = local(association.tag)
            if kind not in {"dataInputAssociation", "dataOutputAssociation"}: continue
            source = association.find(q("bpmn", "sourceRef")); target = association.find(q("bpmn", "targetRef"))
            if source is None or target is None: continue
            if kind == "dataInputAssociation": result[association.get("id")] = (source.text, activity_id)
            else: result[association.get("id")] = (activity_id, target.text)
    return result


def _svg_geometry(svg_path):
    """Read bpmn-js visible hit geometry in render coordinates."""
    document = ET.parse(svg_path).getroot()
    rendered_shapes, rendered_edges = {}, {}
    for group in document.iter():
        if local(group.tag) != "g" or not group.get("data-element-id"): continue
        element_id, classes = group.get("data-element-id"), group.get("class") or ""
        if "djs-shape" in classes:
            transform = re.search(r"matrix\([^ ]+ [^ ]+ [^ ]+ [^ ]+ ([-0-9.]+) ([-0-9.]+)\)", group.get("transform") or "")
            tx, ty = (float(transform.group(1)), float(transform.group(2))) if transform else (0.0, 0.0)
            hit = next((x for x in group.iter() if local(x.tag) == "rect" and "djs-hit-all" in (x.get("class") or "")), None)
            if hit is not None:
                x, y = tx + float(hit.get("x", 0)), ty + float(hit.get("y", 0))
                rendered_shapes[element_id] = (x, y, x + float(hit.get("width", 0)), y + float(hit.get("height", 0)))
        elif "djs-connection" in classes:
            hit = next((x for x in group.iter() if local(x.tag) == "path" and "djs-hit-stroke" in (x.get("class") or "")), None)
            if hit is not None:
                points = [(float(x), float(y)) for x, y in re.findall(
                    r"[ML]\s*(-?[0-9]+(?:\.[0-9]+)?),?\s*(-?[0-9]+(?:\.[0-9]+)?)", hit.get("d") or "")]
                if len(points) >= 2: rendered_edges[element_id] = points
    return rendered_shapes, rendered_edges


def validate(path: Path, model=None, svg_path=None, png_path=None):
    errors, warnings = [], []
    try: root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc: return {"status": "FAIL", "errors": [{"rule": "XML_PARSE", "detail": str(exc)}], "warnings": []}
    semantic = {x.get("id"): x for x in root.iter() if x.get("id")}
    shapes = {x.get("bpmnElement"): _bounds(x) for x in root.findall(f".//{q('bpmndi', 'BPMNShape')}")}
    edges = {x.get("bpmnElement"): _points(x) for x in root.findall(f".//{q('bpmndi', 'BPMNEdge')}")}
    labels = []
    for owner in [*root.findall(f".//{q('bpmndi', 'BPMNShape')}"), *root.findall(f".//{q('bpmndi', 'BPMNEdge')}")]:
        label = owner.find(q("bpmndi", "BPMNLabel"))
        if label is not None and _bounds(label): labels.append((owner.get("bpmnElement"), _bounds(label)))
    endpoints = _data_endpoints(root)
    for tag in ("sequenceFlow", "messageFlow", "association"):
        for edge in root.findall(f".//{q('bpmn', tag)}"):
            endpoints[edge.get("id")] = (edge.get("sourceRef"), edge.get("targetRef"))
    for edge_id, points in edges.items():
        refs = endpoints.get(edge_id)
        if not refs or len(points) < 2:
            errors.append({"rule": "BPMN_DI_EDGE_INCOMPLETE", "element": edge_id, "detail": "semantic endpoints/waypoints missing"})
            continue
        for side, ref, point in (("SOURCE", refs[0], points[0]), ("TARGET", refs[1], points[-1])):
            rect = shapes.get(ref); element = semantic.get(ref)
            if rect is None:
                errors.append({"rule": f"EDGE_{side}_SHAPE_MISSING", "element": edge_id, "detail": str(ref)})
            elif not point_on_perimeter(_shape_kind(element), point, rect, tolerance=1.5):
                errors.append({"rule": f"EDGE_{side}_NOT_ON_SHAPE_PERIMETER", "element": edge_id,
                               "detail": f"point={point}, shape={ref}, kind={_shape_kind(element)}"})

    flow_node_tags = {"task", "userTask", "manualTask", "serviceTask", "scriptTask", "businessRuleTask",
                      "sendTask", "receiveTask", "callActivity", "subProcess", "startEvent", "endEvent",
                      "intermediateCatchEvent", "intermediateThrowEvent", "exclusiveGateway", "parallelGateway",
                      "inclusiveGateway", "eventBasedGateway"}
    flow_nodes = {sid: rect for sid, rect in shapes.items() if rect and semantic.get(sid) is not None
                  and local(semantic[sid].tag) in flow_node_tags}
    for edge_id, points in edges.items():
        source, target = endpoints.get(edge_id, (None, None))
        for node_id, rect in flow_nodes.items():
            if node_id in {source, target}: continue
            if any(_segment_intersects_rect(a, b, rect) for a, b in _segments(points)):
                errors.append({"rule": "BPMN_DI_EDGE_THROUGH_FLOW_NODE", "element": edge_id, "detail": node_id}); break
    # Data associations are routed independently from sequence/message flows.
    # Their visible path must not cut through any unrelated BPMN shape,
    # including artifacts and the portable role/system TextAnnotations.
    for edge_id, points in edges.items():
        if not edge_id.startswith("DataAssoc_"): continue
        source, target = endpoints.get(edge_id, (None, None))
        for shape_id, rect in shapes.items():
            if shape_id in {source, target} or rect is None: continue
            element = semantic.get(shape_id)
            if element is not None and local(element.tag) == "participant": continue
            if any(_segment_intersects_rect(a, b, rect) for a, b in _segments(points)):
                errors.append({"rule": "BPMN_DI_DATA_ASSOCIATION_THROUGH_SHAPE",
                               "element": edge_id, "detail": shape_id})
                break
    edge_ids = list(edges)
    for index, left in enumerate(edge_ids):
        for right in edge_ids[index + 1:]:
            relation = next((_segment_relation(a, b, c, d) for a, b in _segments(edges[left])
                             for c, d in _segments(edges[right]) if _segment_relation(a, b, c, d)), None)
            if relation == "overlap":
                errors.append({"rule": "BPMN_DI_EDGE_SEGMENT_OVERLAP", "element": left, "detail": right})
            elif relation == "cross" and (left.startswith("DataAssoc_") or right.startswith("DataAssoc_")):
                errors.append({"rule": "BPMN_DI_DATA_ASSOCIATION_CROSS", "element": left, "detail": right})
    for owner, label in labels:
        for shape_id, rect in shapes.items():
            shape_element = semantic.get(shape_id)
            if shape_element is not None and local(shape_element.tag) == "participant":
                continue
            if shape_id != owner and rect and _rect_overlap(label, rect):
                errors.append({"rule": "BPMN_DI_LABEL_OVERLAP", "element": owner, "detail": shape_id}); break
    for index, (left_owner, left_label) in enumerate(labels):
        for right_owner, right_label in labels[index + 1:]:
            if left_owner != right_owner and _rect_overlap(left_label, right_label):
                errors.append({"rule": "BPMN_DI_LABEL_OVERLAP", "element": left_owner,
                               "detail": f"label:{right_owner}"})
    if model is not None:
        annotation_texts = {"".join(x.itertext()).strip() for x in root.findall(f".//{q('bpmn', 'textAnnotation')}")}
        for step in model.get("steps") or []:
            if str(step.get("task_type")) in {"start_event", "end_event", "intermediate_catch_event", "intermediate_throw_event"}: continue
            sid = str(step.get("id"))
            if step.get("performer_kind") == "human" and str(step.get("role") or "").strip() \
                    and f"Роль: {step['role']}" not in annotation_texts:
                errors.append({"rule": "BPMN_DI_ROLE_NOT_VISIBLE", "element": sid, "detail": str(step.get("role"))})
            if f"Система: {step.get('system') or 'OPEN'}" not in annotation_texts:
                errors.append({"rule": "BPMN_DI_SYSTEM_NOT_VISIBLE", "element": sid, "detail": str(step.get("system"))})
    if svg_path is not None:
        try:
            svg = Path(svg_path).read_text(encoding="utf-8")
            if "<svg" not in svg or "djs-element" not in svg: raise ValueError("bpmn-js SVG markers missing")
            svg_shapes, svg_edges = _svg_geometry(Path(svg_path))
            for edge_id, refs in endpoints.items():
                route = svg_edges.get(edge_id)
                if edge_id not in edges: continue
                if not route:
                    errors.append({"rule": "BPMN_JS_EDGE_NOT_RENDERED", "element": edge_id})
                    continue
                for side, ref, point in (("SOURCE", refs[0], route[0]), ("TARGET", refs[1], route[-1])):
                    rect = svg_shapes.get(ref); element = semantic.get(ref)
                    if rect is None or not point_on_perimeter(_shape_kind(element), point, rect, tolerance=2.0):
                        errors.append({"rule": f"EDGE_{side}_NOT_ON_SHAPE_PERIMETER", "element": edge_id,
                                       "detail": f"bpmn-js point={point}, shape={ref}"})
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append({"rule": "BPMN_JS_SVG_UNREADABLE", "detail": str(exc)})
    if png_path is not None:
        try:
            data = Path(png_path).read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 100: raise ValueError("invalid/empty PNG")
        except (OSError, ValueError) as exc:
            errors.append({"rule": "BPMN_JS_PNG_UNREADABLE", "detail": str(exc)})
    return {"status": "FAIL" if errors else "PASS", "errors": errors, "warnings": warnings,
            "checks": {"shapes": len(shapes), "edges": len(edges), "labels": len(labels),
                       "bpmn_js_svg": str(svg_path) if svg_path else "NOT_REQUESTED",
                       "bpmn_js_png": str(png_path) if png_path else "NOT_REQUESTED"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bpmn"); parser.add_argument("--model"); parser.add_argument("--svg"); parser.add_argument("--png")
    parser.add_argument("--report"); args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8")) if args.model else None
    result = validate(Path(args.bpmn), model, Path(args.svg) if args.svg else None, Path(args.png) if args.png else None)
    report = Path(args.report) if args.report else Path(args.bpmn).with_name(Path(args.bpmn).stem + "-bpmn-di-validation.json")
    report.parent.mkdir(parents=True, exist_ok=True); report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report.with_suffix(".md").write_text("# BPMN-DI validation\n\n" + f"Status: {result['status']}\n\n" +
                                         "\n".join(f"- {x['rule']}: {x.get('element', '')} {x.get('detail', '')}" for x in result["errors"]) + "\n", encoding="utf-8")
    print(f"BPMN-DI validation: {result['status']} ({report})")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
