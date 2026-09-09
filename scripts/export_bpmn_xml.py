#!/usr/bin/env python3
"""Export process_model 2.1 as portable BPMN 2.0 XML with BPMN-DI."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drawio_layout import Bounds, absolute_bounds, edge_points, label_bounds, load_cells  # noqa: E402

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}
XSI = "http://www.w3.org/2001/XMLSchema-instance"
for prefix, uri in NS.items(): ET.register_namespace(prefix, uri)
ET.register_namespace("xsi", XSI)


def q(prefix, tag): return f"{{{NS[prefix]}}}{tag}"


TASK_TAGS = {
    "user_task": "userTask", "manual_task": "manualTask", "service_task": "serviceTask",
    "script_task": "scriptTask", "business_rule_task": "businessRuleTask", "send_task": "sendTask",
    "receive_task": "receiveTask", "call_activity": "callActivity", "subprocess": "subProcess",
    "start_event": "startEvent", "end_event": "endEvent",
    "intermediate_catch_event": "intermediateCatchEvent", "intermediate_throw_event": "intermediateThrowEvent",
}
GATEWAY_TAGS = {"xor": "exclusiveGateway", "and": "parallelGateway", "or": "inclusiveGateway",
                "event_based": "eventBasedGateway"}
EVENT_TYPES = {"start_event", "end_event", "intermediate_catch_event", "intermediate_throw_event"}


def safe_id(value):
    token = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "")).strip("_")
    return token if token and not token[0].isdigit() else f"ID_{token or 'item'}"


def documentation(step):
    obj = step.get("business_object") if isinstance(step.get("business_object"), dict) else {}
    fields = [
        ("Действие", step.get("action") or step.get("name")), ("Объект", obj.get("name")),
        ("Интерфейс/способ", " / ".join(x for x in (step.get("system"), step.get("execution_channel")) if x)),
        ("Основание", step.get("basis")), ("Наблюдаемый результат", step.get("observable_result")),
        ("Критерий проверки", step.get("verification_criterion")), ("Статус знания", step.get("knowledge_status")),
    ]
    return "; ".join(f"{label}: {value or 'OPEN'}" for label, value in fields)


def flows(model):
    result, index = [], 1
    for node in [*(model.get("steps") or []), *(model.get("gateways") or [])]:
        branches = {str(x.get("to")): x for x in node.get("branches") or [] if isinstance(x, dict)}
        for target in node.get("next") or []:
            branch = branches.get(str(target), {})
            result.append({"id": f"F{index}", "source": str(node["id"]), "target": str(target),
                           "condition": branch.get("condition"), "default": bool(branch.get("default"))})
            index += 1
    return result


def _event_definition(element, task_type, step):
    definition = str(step.get("event_definition") or "none").lower()
    tags = {"message": "messageEventDefinition", "timer": "timerEventDefinition",
            "conditional": "conditionalEventDefinition", "signal": "signalEventDefinition",
            "multiple": "messageEventDefinition", "parallel_multiple": "messageEventDefinition"}
    tag = tags.get(definition)
    if tag: ET.SubElement(element, q("bpmn", tag), {"id": f"{tag}_{safe_id(step.get('id'))}"})
    if task_type == "start_event" and definition == "parallel_multiple": element.set("parallelMultiple", "true")


def _num(value):
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")


def _add_bounds(parent, bounds):
    ET.SubElement(parent, q("dc", "Bounds"), {"x": _num(bounds.x), "y": _num(bounds.y),
                                              "width": _num(bounds.width), "height": _num(bounds.height)})


def _add_label(parent, bounds):
    if bounds is not None:
        label = ET.SubElement(parent, q("bpmndi", "BPMNLabel")); _add_bounds(label, bounds)


def _occurrences(model, render_meta=None):
    if render_meta and render_meta.get("artifact_occurrences"):
        return [dict(x) for x in render_meta["artifact_occurrences"] if isinstance(x, dict)]
    result = []
    for step in model.get("steps") or []:
        for direction in ("input", "output"):
            for index, artifact in enumerate(step.get(f"{direction}s") or [], 1):
                if isinstance(artifact, dict):
                    result.append({**artifact, "activity_id": str(step["id"]), "direction": direction,
                                   "artifact_instance_id": f"ART.{step['id']}.{direction.upper()}.{index}"})
    return result


def build(model, drawio_path=None, render_meta=None):
    process_id = f"Process_{safe_id(model.get('process_id'))}"
    definitions = ET.Element(q("bpmn", "definitions"), {
        "id": f"Definitions_{safe_id(model.get('process_id'))}",
        "targetNamespace": "https://arman.example/bpmn/generated",
        "exporter": "arman-bpmn-generator", "exporterVersion": "2.1",
    })
    collaboration = ET.SubElement(definitions, q("bpmn", "collaboration"), {"id": "Collaboration_main"})
    participants = list(model.get("participants") or []) or [{"id": "PART_MAIN", "name": model.get("process_name"),
                                                               "participant_kind": "current_process"}]
    current = next((x for x in participants if x.get("participant_kind") == "current_process"), participants[0])
    for participant in participants:
        attrs = {"id": safe_id(participant.get("id")), "name": str(participant.get("name") or participant.get("id"))}
        if participant is current: attrs["processRef"] = process_id
        element = ET.SubElement(collaboration, q("bpmn", "participant"), attrs)
        linked = participant.get("linked_process")
        if isinstance(linked, dict) and (linked.get("id") or linked.get("uri")):
            ET.SubElement(element, q("bpmn", "documentation")).text = (
                f"Связанный процесс: {linked.get('id') or 'OPEN'}; URI: {linked.get('uri') or 'OPEN'}")
    process = ET.SubElement(definitions, q("bpmn", "process"), {
        "id": process_id, "name": str(model.get("process_name") or model.get("process_id")), "isExecutable": "false"})
    graph_flows = flows(model)
    incoming, outgoing = {}, {}
    for flow in graph_flows:
        incoming.setdefault(flow["target"], []).append(flow["id"])
        outgoing.setdefault(flow["source"], []).append(flow["id"])

    occurrences = _occurrences(model, render_meta)
    logical_objects = {}
    for item in occurrences:
        logical = str(item.get("logical_object_id") or f"OPEN.{safe_id(item.get('name'))}")
        logical_objects.setdefault(logical, (str(item.get("bpmn_representation") or "data_object"),
                                             str(item.get("name") or logical)))
    for logical, (representation, name) in logical_objects.items():
        owner = definitions if representation == "data_store" else process
        ET.SubElement(owner, q("bpmn", "dataStore" if representation == "data_store" else "dataObject"),
                      {"id": f"Logical_{safe_id(logical)}", "name": name})
    occurrence_refs, direction_counts = {}, {}
    for item in occurrences:
        sid, direction = str(item.get("activity_id")), str(item.get("direction"))
        key = (sid, direction); direction_counts[key] = direction_counts.get(key, 0) + 1
        ref_id = f"DataRef_{safe_id(sid)}_{direction}_{direction_counts[key]}"
        occurrence_refs[str(item.get("artifact_instance_id"))] = ref_id
        representation = str(item.get("bpmn_representation") or "data_object")
        ref_tag = "dataStoreReference" if representation == "data_store" else "dataObjectReference"
        ref_attr = "dataStoreRef" if representation == "data_store" else "dataObjectRef"
        logical = str(item.get("logical_object_id") or f"OPEN.{safe_id(item.get('name'))}")
        reference = ET.SubElement(process, q("bpmn", ref_tag), {"id": ref_id, "name": str(item.get("name") or ""),
                                                                 ref_attr: f"Logical_{safe_id(logical)}"})
        if item.get("state"): ET.SubElement(reference, q("bpmn", "dataState"), {"name": str(item["state"])})

    by_activity = {}
    for item in occurrences: by_activity.setdefault(str(item.get("activity_id")), []).append(item)
    node_elements, data_assoc_ids = {}, {}
    for step in model.get("steps") or []:
        sid, task_type = str(step["id"]), str(step.get("task_type") or "user_task")
        attrs = {"id": safe_id(sid), "name": str(step.get("name") or "")}
        if task_type == "call_activity" and step.get("called_process_id"): attrs["calledElement"] = str(step["called_process_id"])
        element = ET.SubElement(process, q("bpmn", TASK_TAGS.get(task_type, "task")), attrs)
        node_elements[sid] = element
        ET.SubElement(element, q("bpmn", "documentation")).text = documentation(step)
        for flow_id in incoming.get(sid, []): ET.SubElement(element, q("bpmn", "incoming")).text = flow_id
        for flow_id in outgoing.get(sid, []): ET.SubElement(element, q("bpmn", "outgoing")).text = flow_id
        _event_definition(element, task_type, step)
        owned = by_activity.get(sid, [])
        inputs = [x for x in owned if x.get("direction") == "input"]
        outputs = [x for x in owned if x.get("direction") == "output"]
        if inputs or outputs:
            io_spec = ET.SubElement(element, q("bpmn", "ioSpecification"))
            for index, item in enumerate(inputs, 1):
                ET.SubElement(io_spec, q("bpmn", "dataInput"), {"id": f"DataInput_{safe_id(sid)}_{index}",
                                                                  "name": str(item.get("name") or "")})
            for index, item in enumerate(outputs, 1):
                ET.SubElement(io_spec, q("bpmn", "dataOutput"), {"id": f"DataOutput_{safe_id(sid)}_{index}",
                                                                   "name": str(item.get("name") or "")})
            input_set = ET.SubElement(io_spec, q("bpmn", "inputSet"), {"id": f"InputSet_{safe_id(sid)}"})
            for index in range(1, len(inputs) + 1): ET.SubElement(input_set, q("bpmn", "dataInputRefs")).text = f"DataInput_{safe_id(sid)}_{index}"
            output_set = ET.SubElement(io_spec, q("bpmn", "outputSet"), {"id": f"OutputSet_{safe_id(sid)}"})
            for index in range(1, len(outputs) + 1): ET.SubElement(output_set, q("bpmn", "dataOutputRefs")).text = f"DataOutput_{safe_id(sid)}_{index}"
        for direction, items in (("input", inputs), ("output", outputs)):
            for index, item in enumerate(items, 1):
                assoc_id = f"DataAssoc_{safe_id(sid)}_{direction}_{index}"
                data_assoc_ids[str(item.get("artifact_instance_id"))] = assoc_id
                association = ET.SubElement(element, q("bpmn", f"data{direction.title()}Association"), {"id": assoc_id})
                ref_id = occurrence_refs[str(item.get("artifact_instance_id"))]
                if direction == "input":
                    ET.SubElement(association, q("bpmn", "sourceRef")).text = ref_id
                    ET.SubElement(association, q("bpmn", "targetRef")).text = f"DataInput_{safe_id(sid)}_{index}"
                else:
                    ET.SubElement(association, q("bpmn", "sourceRef")).text = f"DataOutput_{safe_id(sid)}_{index}"
                    ET.SubElement(association, q("bpmn", "targetRef")).text = ref_id
    for gateway in model.get("gateways") or []:
        gid = str(gateway["id"]); attrs = {"id": safe_id(gid)}
        if gateway.get("gateway_role") != "merge" and gateway.get("name"): attrs["name"] = str(gateway["name"])
        element = ET.SubElement(process, q("bpmn", GATEWAY_TAGS.get(str(gateway.get("gateway_type")), "exclusiveGateway")), attrs)
        node_elements[gid] = element
        for flow_id in incoming.get(gid, []): ET.SubElement(element, q("bpmn", "incoming")).text = flow_id
        for flow_id in outgoing.get(gid, []): ET.SubElement(element, q("bpmn", "outgoing")).text = flow_id
    for flow in graph_flows:
        if flow["default"] and flow["source"] in node_elements: node_elements[flow["source"]].set("default", flow["id"])
    for flow in graph_flows:
        attrs = {"id": flow["id"], "sourceRef": safe_id(flow["source"]), "targetRef": safe_id(flow["target"])}
        if flow.get("condition"): attrs["name"] = str(flow["condition"])
        element = ET.SubElement(process, q("bpmn", "sequenceFlow"), attrs)
        if flow.get("condition") and not flow.get("default"):
            ET.SubElement(element, q("bpmn", "conditionExpression"), {f"{{{XSI}}}type": "bpmn:tFormalExpression"}).text = str(flow["condition"])
    message_ids = {}
    for index, flow in enumerate(model.get("message_flows") or [], 1):
        source_id = str(flow.get("id") or f"MessageFlow_{index}"); semantic_id = safe_id(source_id)
        message_ids[source_id] = semantic_id
        ET.SubElement(collaboration, q("bpmn", "messageFlow"), {"id": semantic_id, "name": str(flow.get("name") or ""),
                                                                 "sourceRef": safe_id(flow.get("source")),
                                                                 "targetRef": safe_id(flow.get("target"))})

    visual_annotations = []
    for step in model.get("steps") or []:
        sid, task_type = str(step.get("id")), str(step.get("task_type") or "")
        if task_type in EVENT_TYPES: continue
        if step.get("performer_kind") == "human" and str(step.get("role") or "").strip():
            visual_annotations.append({"id": f"Visual_ROLE_{safe_id(sid)}", "cell": f"ROLE.{sid}",
                                       "text": f"Роль: {step['role']}", "activity": sid, "kind": "role"})
        visual_annotations.append({"id": f"Visual_SYSTEM_{safe_id(sid)}", "cell": f"SYSTEM.{sid}",
                                   "text": f"Система: {step.get('system') or 'OPEN'}", "activity": sid, "kind": "system"})
    for item in visual_annotations:
        annotation = ET.SubElement(process, q("bpmn", "textAnnotation"), {"id": item["id"]})
        ET.SubElement(annotation, q("bpmn", "text")).text = item["text"]
        item["association_id"] = f"VisualAssoc_{item['kind'].upper()}_{safe_id(item['activity'])}"
        ET.SubElement(process, q("bpmn", "association"), {"id": item["association_id"],
                                                            "sourceRef": safe_id(item["activity"]),
                                                            "targetRef": item["id"], "associationDirection": "None"})
    for index, item in enumerate(model.get("annotations") or [], 1):
        annotation = ET.SubElement(process, q("bpmn", "textAnnotation"), {"id": safe_id(item.get("id") or f"Annotation_{index}")})
        ET.SubElement(annotation, q("bpmn", "text")).text = str(item.get("text") or "")

    if drawio_path is not None:
        _add_di(definitions, model, process_id, collaboration, current, participants, graph_flows, occurrences,
                occurrence_refs, data_assoc_ids, visual_annotations, message_ids, Path(drawio_path))
    return ET.ElementTree(definitions)


def _add_shape(plane, semantic_id, drawio_cell, cells, label=None, horizontal=False, bounds_override=None):
    bounds = bounds_override or absolute_bounds(cells.get(drawio_cell), cells)
    if bounds is None: return None
    attrs = {"id": f"Shape_{safe_id(semantic_id)}", "bpmnElement": semantic_id}
    if horizontal: attrs["isHorizontal"] = "true"
    shape = ET.SubElement(plane, q("bpmndi", "BPMNShape"), attrs); _add_bounds(shape, bounds); _add_label(shape, label)
    return shape


def _add_edge(plane, semantic_id, drawio_cell, cells, label=None, explicit_points=None):
    cell = cells.get(drawio_cell)
    points = explicit_points if explicit_points is not None else edge_points(cell, cells) if cell is not None else []
    if len(points) < 2: return None
    edge = ET.SubElement(plane, q("bpmndi", "BPMNEdge"), {"id": f"Edge_{safe_id(semantic_id)}", "bpmnElement": semantic_id})
    for x, y in points: ET.SubElement(edge, q("di", "waypoint"), {"x": _num(x), "y": _num(y)})
    _add_label(edge, label); return edge


def _annotation_points(task, annotation, kind):
    task_x = task.x + task.width * (.28 if kind == "role" else .72)
    ann_x = min(max(task_x, annotation.x), annotation.x + annotation.width)
    return ([(task_x, task.y), (ann_x, annotation.y + annotation.height)]
            if annotation.y + annotation.height <= task.y
            else [(task_x, task.y + task.height), (ann_x, annotation.y)])


def _portable_annotation_bounds(task, badge, kind):
    """Expand compact draw.io badges into readable standard TextAnnotations."""
    # Role captions benefit from a wider line above the task.  System captions
    # stay compact below it so the artifact ports on either side remain free.
    width = max(160, badge.width) if kind == "role" else min(task.width - 30, max(100, badge.width))
    x = task.x + (task.width - width) / 2
    if kind == "role":
        return Bounds(x, task.y - 54, width, 40)
    return Bounds(x, task.y + task.height + 6, width, 28)


def _add_di(definitions, model, process_id, collaboration, current, participants, graph_flows, occurrences,
            occurrence_refs, data_assoc_ids, visual_annotations, message_ids, drawio_path):
    cells = load_cells(drawio_path)
    diagram = ET.SubElement(definitions, q("bpmndi", "BPMNDiagram"), {"id": "BPMNDiagram_main"})
    plane = ET.SubElement(diagram, q("bpmndi", "BPMNPlane"), {"id": "BPMNPlane_main",
                                                                "bpmnElement": collaboration.get("id")})
    _add_shape(plane, safe_id(current.get("id")), "PART_MAIN", cells, horizontal=True)
    for participant in participants:
        if participant is current: continue
        pid = str(participant.get("id"))
        _add_shape(plane, safe_id(pid), pid, cells, label=label_bounds(cells, f"PARTICIPANT.{pid}"), horizontal=True)
    for step in model.get("steps") or []:
        sid, task_type = str(step.get("id")), str(step.get("task_type") or "user_task")
        _add_shape(plane, safe_id(sid), sid if task_type in EVENT_TYPES else f"TASK.{sid}", cells,
                   label=label_bounds(cells, sid))
    for gateway in model.get("gateways") or []:
        gid = str(gateway.get("id")); _add_shape(plane, safe_id(gid), gid, cells, label=label_bounds(cells, gid))
    for item in occurrences:
        cell_id = str(item.get("artifact_instance_id")); ref_id = occurrence_refs.get(cell_id)
        bounds = absolute_bounds(cells.get(cell_id), cells)
        # Keep each label inside the artifact's horizontal slot.  Adjacent
        # input/output objects are deliberately close in the corporate layout,
        # so a label wider than its own slot would overlap its neighbour.
        data_label = Bounds(bounds.x - 4, bounds.y + bounds.height + 3, bounds.width + 8, 40) if bounds else None
        if ref_id: _add_shape(plane, ref_id, cell_id, cells, label=data_label)
    annotation_bounds = {}
    for item in visual_annotations:
        task = absolute_bounds(cells.get(f"TASK.{item['activity']}"), cells)
        badge = absolute_bounds(cells.get(item["cell"]), cells)
        if task and badge:
            annotation_bounds[item["id"]] = _portable_annotation_bounds(task, badge, item["kind"])
            _add_shape(plane, item["id"], item["cell"], cells, bounds_override=annotation_bounds[item["id"]])
    for index, item in enumerate(model.get("annotations") or [], 1):
        aid = str(item.get("id") or f"ANNOTATION.{index}")
        _add_shape(plane, safe_id(item.get("id") or f"Annotation_{index}"), aid, cells)
    for flow in graph_flows: _add_edge(plane, flow["id"], flow["id"], cells, label=label_bounds(cells, flow["id"]))
    for index, flow in enumerate(model.get("message_flows") or [], 1):
        fid = str(flow.get("id") or f"MessageFlow_{index}")
        _add_edge(plane, message_ids[fid], fid, cells, label=label_bounds(cells, fid))
    for item in occurrences:
        artifact_id = str(item.get("artifact_instance_id")); assoc_id = data_assoc_ids.get(artifact_id)
        if assoc_id: _add_edge(plane, assoc_id, f"ASSOC.{artifact_id}", cells)
    for item in visual_annotations:
        task = absolute_bounds(cells.get(f"TASK.{item['activity']}"), cells)
        badge = annotation_bounds.get(item["id"])
        if task and badge:
            _add_edge(plane, item["association_id"], item["association_id"], cells,
                      explicit_points=_annotation_points(task, badge, item["kind"]))


def lint_bpmn_xml(path, require_di=False):
    errors, warnings = [], []
    try: root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc: return {"status": "FAIL", "errors": [f"XML_PARSE: {exc}"], "warnings": []}
    if root.findall(f".//{q('bpmn', 'laneSet')}"): errors.append("NO_INTERNAL_LANES: laneSet присутствует")
    ids = {x.get("id") for x in root.iter() if x.get("id")}
    for tag in ("sequenceFlow", "messageFlow", "association"):
        for flow in root.findall(f".//{q('bpmn', tag)}"):
            for attr in ("sourceRef", "targetRef"):
                if flow.get(attr) not in ids: errors.append(f"UNRESOLVED_REFERENCE: {flow.get('id')}.{attr}={flow.get(attr)}")
    sequence = {x.get("id"): x for x in root.findall(f".//{q('bpmn', 'sequenceFlow')}")}
    for element in root.iter():
        default_ref = element.get("default")
        if default_ref:
            flow = sequence.get(default_ref)
            if flow is None: errors.append(f"DEFAULT_FLOW_UNRESOLVED: {element.get('id')}.default={default_ref}")
            elif flow.get("sourceRef") != element.get("id"): errors.append(f"DEFAULT_FLOW_SOURCE_MISMATCH: {element.get('id')}.default={default_ref}")
    for call in root.findall(f".//{q('bpmn', 'callActivity')}"):
        if not str(call.get("calledElement") or "").strip(): errors.append(f"CALL_ACTIVITY_CALLED_ELEMENT_REQUIRED: {call.get('id')}")
    starts = {x.get("id"): x for x in root.findall(f".//{q('bpmn', 'startEvent')}")}
    for flow in root.findall(f".//{q('bpmn', 'messageFlow')}"):
        target = starts.get(flow.get("targetRef"))
        if target is not None and target.find(q("bpmn", "messageEventDefinition")) is None:
            errors.append(f"MESSAGE_FLOW_START_EVENT_DEFINITION_REQUIRED: {flow.get('id')} -> {flow.get('targetRef')}")
    diagrams = root.findall(f".//{q('bpmndi', 'BPMNDiagram')}")
    shapes = root.findall(f".//{q('bpmndi', 'BPMNShape')}")
    edges = root.findall(f".//{q('bpmndi', 'BPMNEdge')}")
    if require_di and (not diagrams or not shapes or not edges): errors.append("BPMN_DI_REQUIRED: Diagram/Shape/Edge отсутствует")
    if require_di:
        for edge in edges:
            if len(edge.findall(q("di", "waypoint"))) < 2: errors.append(f"BPMN_DI_WAYPOINTS_REQUIRED: {edge.get('id')}")
    return {"status": "FAIL" if errors else "PASS", "errors": errors, "warnings": warnings,
            "checks": {"no_lane_set": not bool(root.findall(f".//{q('bpmn', 'laneSet')}")), "bpmn_di": bool(diagrams)}}


def validate_xsd(path, xsd_dir):
    schema_path = xsd_dir / "BPMN20.xsd"
    if not schema_path.is_file(): return "NOT_CONFIGURED", [f"BPMN20.xsd not found in {xsd_dir}"]
    try:
        from lxml import etree
        schema = etree.XMLSchema(etree.parse(str(schema_path))); document = etree.parse(str(path))
        return ("PASS", []) if schema.validate(document) else ("FAIL", [str(x) for x in schema.error_log])
    except Exception as exc: return "FAIL", [f"XSD_VALIDATION_ERROR: {exc}"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry"); parser.add_argument("output"); parser.add_argument("--mode", default="to-be")
    parser.add_argument("--model"); parser.add_argument("--drawio", required=True)
    parser.add_argument("--render-meta"); parser.add_argument("--validation-report")
    parser.add_argument("--xsd-dir", default=os.environ.get("BPMN_XSD_DIR")); args = parser.parse_args()
    model_path = Path(args.model) if args.model else Path(str(args.registry).replace("-registry.md", "-model.json"))
    model = json.loads(model_path.read_text(encoding="utf-8"))
    meta_path = Path(args.render_meta) if args.render_meta else Path(args.registry).with_name(Path(args.registry).stem.replace("-registry", "") + "-render-meta.json")
    render_meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else None
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    tree = build(model, Path(args.drawio), render_meta); ET.indent(tree, space="  "); tree.write(output, encoding="utf-8", xml_declaration=True)
    report = lint_bpmn_xml(output, require_di=True)
    xsd_status, xsd_errors = validate_xsd(output, Path(args.xsd_dir)) if args.xsd_dir else ("NOT_RUN", [])
    report.update({"xsd_status": xsd_status, "xsd_errors": xsd_errors})
    if xsd_status == "FAIL": report["status"] = "FAIL"; report["errors"].extend(xsd_errors)
    report_path = Path(args.validation_report) if args.validation_report else Path("output/validation") / f"{model['process_id']}-bpmn-xml-validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True); report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"BPMN XML+DI: {output}"); print(f"BPMN XML validation: {report['status']} ({report_path})")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
