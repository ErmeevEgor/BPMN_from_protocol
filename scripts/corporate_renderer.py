#!/usr/bin/env python3
"""Render corporate_role_overlays_v2 as deterministic lane-less draw.io XML.

Every flow has explicit ports. Coordinates come from actual BPMN Flow Node
bounds; Activity Group bounds never participate in routing.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import textwrap
import xml.etree.ElementTree as ET

from shape_geometry import DIAMOND, ELLIPSE, RECTANGLE, perimeter_port


DEFAULT_THEME = {
    "activity_fill": "#FFFFFF", "activity_stroke": "#1F2937",
    "role_fill": "#6366F1", "role_stroke": "#4F46E5", "role_font": "#FFFFFF",
    "system_fill": "#F3F4F6", "system_stroke": "#9CA3AF", "system_font": "#374151",
    "system_report_fill": "#FFDCC2", "print_form_fill": "#E7FCCB",
    "system_object_fill": "#FFFFFF", "other_artifact_fill": "#F3F4F6",
}

POOL_X, POOL_Y = 20, 110
FLOW_LEFT, COLUMN_STEP, ROW_GAP = 80, 220, 125
GROUP_WIDTH = 210
TASK_X, TASK_Y, TASK_WIDTH, TASK_HEIGHT = 30, 32, 150, 80
ROLE_WIDTH, ROLE_HEIGHT = 120, 22
SYSTEM_WIDTH, SYSTEM_HEIGHT = 112, 20
ARTIFACT_TOP, ARTIFACT_WIDTH, ARTIFACT_HEIGHT, ARTIFACT_GAP = 154, 84, 44, 12


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def left(self): return self.x
    @property
    def right(self): return self.x + self.width
    @property
    def top(self): return self.y
    @property
    def bottom(self): return self.y + self.height
    @property
    def cx(self): return self.x + self.width / 2
    @property
    def cy(self): return self.y + self.height / 2


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")


def _cell(root, cid, value="", style="", *, parent="1", vertex=False, edge=False,
          source=None, target=None, extra=None):
    attrs = {"id": cid, "value": value, "style": style, "parent": parent}
    if vertex: attrs["vertex"] = "1"
    if edge: attrs["edge"] = "1"
    if source: attrs["source"] = source
    if target: attrs["target"] = target
    if extra: attrs.update(extra)
    return ET.SubElement(root, "mxCell", attrs)


def _geometry(cell, x, y, width, height):
    return ET.SubElement(cell, "mxGeometry", {
        "x": _number(x), "y": _number(y), "width": _number(width),
        "height": _number(height), "as": "geometry",
    })


def _edge_geometry(cell, points=None):
    geom = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    if points:
        array = ET.SubElement(geom, "Array", {"as": "points"})
        for x, y in points:
            ET.SubElement(array, "mxPoint", {"x": _number(x), "y": _number(y)})


def _wrap(value, width, max_lines=None):
    text = " ".join(str(value or "").split())
    if not text: return ""
    lines = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False) or [text]
    if max_lines is not None and len(lines) > max_lines:
        # Do not silently truncate source wording: the validator must expose overflow.
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return "\n".join(lines)


def _task_style(task_type, theme):
    marker = {
        "user_task": "shape=mxgraph.bpmn.task;taskMarker=user;",
        "manual_task": "shape=mxgraph.bpmn.task;taskMarker=manual;",
        "service_task": "shape=mxgraph.bpmn.task;taskMarker=service;",
        "script_task": "shape=mxgraph.bpmn.task;taskMarker=script;",
        "business_rule_task": "shape=mxgraph.bpmn.task;taskMarker=businessRule;",
        "send_task": "shape=mxgraph.bpmn.task;taskMarker=send;",
        "receive_task": "shape=mxgraph.bpmn.task;taskMarker=receive;",
        "call_activity": "rounded=1;strokeWidth=3;callActivity=1;",
        "subprocess": "shape=process;rounded=1;",
    }.get(task_type, "rounded=1;")
    return (marker + "markerSize=15;whiteSpace=wrap;overflow=hidden;html=0;align=center;"
            "verticalAlign=middle;spacing=5;"
            f"fillColor={theme['activity_fill']};strokeColor={theme['activity_stroke']};fontSize=11;")


def _primary_path(meta, start_id):
    """Select a stable happy path without requiring a DAG."""
    nodes = meta.get("nodes") or {}
    outgoing = {}
    for flow in meta.get("sequence_flows") or []:
        outgoing.setdefault(str(flow.get("source")), []).append(flow)
    current = str(start_id) if start_id in nodes else next(iter(nodes), "")
    result, seen = [], set()
    while current and current in nodes and current not in seen:
        result.append(current); seen.add(current)
        candidates = [f for f in outgoing.get(current, []) if str(f.get("target")) not in seen]
        if not candidates: break
        candidates.sort(key=lambda f: (
            bool(f.get("default")), str(f.get("id")),
        ))
        current = str(candidates[0].get("target"))
    return result


def _layout_columns(meta, primary):
    nodes, flows = meta.get("nodes") or {}, meta.get("sequence_flows") or []
    columns = {nid: i for i, nid in enumerate(primary)}
    incoming, outgoing = {}, {}
    for flow in flows:
        source, target = str(flow.get("source")), str(flow.get("target"))
        incoming.setdefault(target, []).append(source); outgoing.setdefault(source, []).append(target)
    for _ in range(max(1, len(nodes) * 2)):
        changed = False
        for nid in nodes:
            if nid in columns: continue
            before = [columns[p] for p in incoming.get(nid, []) if p in columns]
            after = [columns[t] for t in outgoing.get(nid, []) if t in columns]
            if before and after:
                columns[nid] = max(before) + 1
            elif before: columns[nid] = max(before) + 1
            elif after: columns[nid] = max(0, min(after) - 1)
            else: continue
            changed = True
        if not changed: break
    fallback = max(columns.values(), default=-1) + 1
    for nid in nodes:
        if nid not in columns: columns[nid], fallback = fallback, fallback + 1
    return columns


def _row_assignments(meta, primary):
    """Infer branch rows from graph components, never source layout hints."""
    nodes, primary_set = meta.get("nodes") or {}, set(primary)
    result = {nid: 0 for nid in nodes if nid in primary_set}
    alternatives = [nid for nid in nodes if nid not in primary_set]
    alternative_set = set(alternatives)
    adjacent = {nid: set() for nid in alternatives}
    for flow in meta.get("sequence_flows") or []:
        source, target = str(flow.get("source")), str(flow.get("target"))
        if source in alternative_set and target in alternative_set:
            adjacent[source].add(target); adjacent[target].add(source)
    row = 0
    remaining = set(alternatives)
    for seed in alternatives:
        if seed not in remaining:
            continue
        row += 1
        pending = [seed]
        while pending:
            nid = pending.pop()
            if nid not in remaining:
                continue
            remaining.remove(nid); result[nid] = row
            pending.extend(sorted(adjacent[nid], reverse=True))
    return result


def _activity_group_height(occurrences):
    inputs = sum(item.get("direction") == "input" for item in occurrences)
    outputs = sum(item.get("direction") == "output" for item in occurrences)
    rows = max(inputs, outputs)
    return max(146, ARTIFACT_TOP + rows * (ARTIFACT_HEIGHT + ARTIFACT_GAP) - ARTIFACT_GAP + 10)


def _port_style(exit_xy, entry_xy):
    return (f"exitX={_number(exit_xy[0])};exitY={_number(exit_xy[1])};exitDx=0;exitDy=0;"
            "exitPerimeter=0;"
            f"entryX={_number(entry_xy[0])};entryY={_number(entry_xy[1])};entryDx=0;entryDy=0;"
            "entryPerimeter=0;")


def _sequence_slot(slot, *, gateway=None):
    """Return a deterministic non-central port for a non-happy flow."""
    if gateway == "out":
        # Fan-out ports progress right-to-left so a deeper branch starts
        # outside the preceding branch's horizontal segment.
        values = (.92, .8, .68, .56, .44, .32, .2, .08)
    elif gateway == "in":
        # Fan-in uses the same nesting order: later return corridors terminate
        # before, rather than across, earlier returns.
        values = (.92, .8, .68, .56, .44, .32, .2, .08)
    elif gateway == "return_in":
        # Keep cyclic return entries below the central happy-path port. The
        # increasing order makes nested left-side approaches planar.
        values = (.56, .62, .68, .74, .8, .86, .9, .94, .96)
    else:
        values = (.12, .22, .32, .42, .58, .68, .78, .88)
    return values[slot % len(values)]


def _sequence_route(flow, source, target, source_shape, target_shape,
                    source_row, target_row, source_col, target_col,
                    primary_pairs, route_index, return_base, return_right,
                    source_slot=0, target_slot=0, target_fan_in=1):
    source_activity, target_activity = source_shape == RECTANGLE, target_shape == RECTANGLE
    source_side_y = source.top + source.height * _sequence_slot(source_slot)
    target_side_y = target.top + target.height * _sequence_slot(target_slot)
    pair = (str(flow.get("source")), str(flow.get("target")))
    if pair in primary_pairs and source_row == target_row == 0 and target_col == source_col + 1:
        return _port_style(perimeter_port(source_shape, "right", .5),
                           perimeter_port(target_shape, "left", .5)), [], "happy"
    if target_row > source_row:
        # Fan-out is planar: branches farther from the happy-path row receive
        # both a lower horizontal lane and a farther-left target corridor.
        # This prevents a later horizontal leg from crossing an earlier
        # branch's vertical leg when several alternatives share a gateway.
        corridor = source.bottom + 58 + route_index * 28
        source_position = _sequence_slot(source_slot) if source_activity else _sequence_slot(source_slot, gateway="out")
        target_position = _sequence_slot(target_slot) if target_activity else _sequence_slot(target_slot, gateway="in")
        source_port = perimeter_port(source_shape, "right" if source_activity else "bottom", source_position)
        target_port = perimeter_port(target_shape, "left" if target_activity else "top", target_position)
        source_x = source.right + 42 + source_slot * 6 if source_activity else source.left + source.width * source_port[0]
        source_y = source_side_y if source_activity else corridor
        target_x = (target.left - 18 - source_slot * 8 - target_slot * 4
                    if target_activity else target.left + target.width * target_port[0])
        target_y = target_side_y if target_activity else corridor
        points = []
        if source_activity: points.append((source_x, source_y))
        points.extend([(source_x, corridor), (target_x, corridor)])
        if target_activity: points.append((target_x, target_y))
        return _port_style(source_port, target_port), points, "branch"
    if source_row > target_row:
        if target_col <= source_col:
            corridor = return_base + route_index * 34
            source_position = _sequence_slot(source_slot) if source_activity else _sequence_slot(source_slot, gateway="out")
            source_port = perimeter_port(source_shape, "right" if source_activity else "bottom", source_position)
            source_y = source_side_y if source_activity else source.bottom
            if not target_activity:
                # Cyclic alternatives use nested outer-bottom corridors. The
                # source corridor moves right, the bottom corridor moves down,
                # and the gateway approach moves left as the branch gets
                # deeper. This preserves planar ordering for repeated cycles.
                target_port = perimeter_port(target_shape, "left",
                                             _sequence_slot(target_slot, gateway="return_in"))
                nesting = max(0, target_fan_in - target_slot)
                source_x = return_right + nesting * 12
                staging_y = return_base + nesting * 34
                target_x = target.left - 60 + target_slot * 8
                target_y = target.top + target.height * target_port[1]
                return (_port_style(source_port, target_port),
                        [(source_x, source_y), (source_x, staging_y),
                         (target_x, staging_y), (target_x, target_y)], "return")
            target_port = perimeter_port(target_shape, "left",
                                         _sequence_slot(target_slot, gateway="return_in"))
            source_x = (source.right + 42 + route_index * 6 + source_slot * 4
                        if source_activity else source.left + source.width * source_port[0])
            target_x = target.left - 42 - target_slot * 6
            target_y = target_side_y
            return (_port_style(source_port, target_port),
                    [(source_x, source_y), (source_x, corridor), (target_x, corridor), (target_x, target_y)], "return")
        elbow_x = source.right + 22 + route_index * 10
        source_port = perimeter_port(source_shape, "right", _sequence_slot(
            source_slot, gateway="out" if not source_activity else None))
        target_port = perimeter_port(target_shape, "left" if target_activity else "bottom",
                                     _sequence_slot(target_slot, gateway="in" if not target_activity else None))
        source_y = source_side_y if source_activity else source.top + source.height * source_port[1]
        target_y = (target_side_y if target_activity
                    else target.top + target.height * target_port[1])
        target_x = (target.left - 42 - target_slot * 6 if target_activity
                    else target.left + target.width * target_port[0])
        return (_port_style(source_port, target_port),
                [(elbow_x, source_y), (elbow_x, target_y), (target_x, target_y)], "branch-return")
    if target_col <= source_col:
        corridor = return_base + route_index * 34
        source_position = _sequence_slot(source_slot) if source_activity else _sequence_slot(source_slot, gateway="out")
        source_port = perimeter_port(source_shape, "right" if source_activity else "bottom", source_position)
        source_y = source_side_y if source_activity else source.bottom
        if not target_activity:
            target_port = perimeter_port(target_shape, "left",
                                         _sequence_slot(target_slot, gateway="return_in"))
            nesting = max(0, target_fan_in - target_slot)
            source_x = return_right + nesting * 12
            staging_y = return_base + nesting * 34
            target_x = target.left - 60 + target_slot * 8
            target_y = target.top + target.height * target_port[1]
            return (_port_style(source_port, target_port),
                    [(source_x, source_y), (source_x, staging_y),
                     (target_x, staging_y), (target_x, target_y)], "return")
        target_port = perimeter_port(target_shape, "left", _sequence_slot(target_slot))
        source_x = (source.right + 42 + route_index * 6 + source_slot * 4
                    if source_activity else source.left + source.width * source_port[0])
        target_x = target.left - 42 - target_slot * 6
        target_y = target_side_y
        return (_port_style(source_port, target_port),
                [(source_x, source_y), (source_x, corridor), (target_x, corridor), (target_x, target_y)], "return")
    corridor = min(source.top, target.top) - 48 - route_index * 20
    source_position = _sequence_slot(source_slot) if source_activity else _sequence_slot(source_slot, gateway="out")
    target_position = _sequence_slot(target_slot) if target_activity else _sequence_slot(target_slot, gateway="in")
    source_port = perimeter_port(source_shape, "right" if source_activity else "top", source_position)
    target_port = perimeter_port(target_shape, "left" if target_activity else "top", target_position)
    source_x = source.right + 42 + source_slot * 6 if source_activity else source.left + source.width * source_port[0]
    source_y = source_side_y if source_activity else source.top
    target_x = target.left - 42 - target_slot * 6 if target_activity else target.left + target.width * target_port[0]
    target_y = target_side_y if target_activity else target.top
    return (_port_style(source_port, target_port),
            [(source_x, source_y), (source_x, corridor), (target_x, corridor), (target_x, target_y)], "alternative")


def _label_cell(root, cid, value, box, *, kind, parent="PART_MAIN", extra_style=""):
    cell = _cell(root, cid, value,
                 "text;html=0;whiteSpace=wrap;overflow=hidden;rounded=0;strokeColor=none;fillColor=none;"
                 f"align=center;verticalAlign=middle;fontSize=10;labelKind={kind};{extra_style}",
                 parent=parent, vertex=True)
    _geometry(cell, box.x, box.y, box.width, box.height)


def render(model, meta, output, theme_config=None):
    raw_theme = {**(theme_config or {}), **(meta.get("theme") or {})}
    theme = {**DEFAULT_THEME}
    for section, prefix in (("activity", "activity"), ("role_overlay", "role"),
                            ("system_overlay", "system"), ("system_report", "system_report"),
                            ("print_form", "print_form"), ("system_object", "system_object"),
                            ("other", "other_artifact")):
        values = raw_theme.get(section)
        if isinstance(values, dict):
            for key, value in values.items(): theme[f"{prefix}_{key}"] = value

    graph = ET.Element("mxGraphModel", {
        "dx": "1422", "dy": "794", "grid": "1", "gridSize": "10", "guides": "1",
        "tooltips": "1", "connect": "1", "arrows": "1", "fold": "1", "page": "1",
        "pageScale": "1", "pageWidth": "1654", "pageHeight": "1169", "math": "0", "shadow": "0",
    })
    root = ET.SubElement(graph, "root")
    ET.SubElement(root, "mxCell", {"id": "0"}); ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    nodes = meta.get("nodes") or {}
    primary = _primary_path(meta, model.get("start")); columns = _layout_columns(meta, primary)
    rows = _row_assignments(meta, primary); primary_pairs = set(zip(primary, primary[1:]))
    occurrence_by_activity = {}
    for occurrence in meta.get("artifact_occurrences") or []:
        occurrence_by_activity.setdefault(str(occurrence.get("activity_id")), []).append(occurrence)

    row_heights, group_heights = {}, {}
    for nid, info in nodes.items():
        height = _activity_group_height(occurrence_by_activity.get(nid, [])) if info.get("kind") == "activity" else 120
        if info.get("kind") == "activity": group_heights[nid] = height
        row_heights[rows[nid]] = max(row_heights.get(rows[nid], 0), height)
    row_tops, cursor = {}, 66.0
    for row in sorted(set(rows.values())):
        row_tops[row], cursor = cursor, cursor + row_heights.get(row, 146) + ROW_GAP

    flow_boxes, group_boxes, flow_shapes = {}, {}, {}
    for nid, info in nodes.items():
        x, top = FLOW_LEFT + columns[nid] * COLUMN_STEP, row_tops[rows[nid]]
        if info.get("kind") == "activity":
            group_boxes[nid] = Box(x - TASK_X, top, GROUP_WIDTH, group_heights[nid])
            flow_boxes[nid] = Box(x, top + TASK_Y, TASK_WIDTH, TASK_HEIGHT)
            flow_shapes[nid] = RECTANGLE
        else:
            flow_boxes[nid] = Box(x + 50, top + TASK_Y + 15, 50, 50)
            flow_shapes[nid] = DIAMOND if info.get("kind") == "gateway" else ELLIPSE
    all_boxes = [*flow_boxes.values(), *group_boxes.values()]
    max_content_x = max((b.right for b in all_boxes), default=900) + 90
    max_content_y = max((b.bottom for b in all_boxes), default=400)
    return_count = sum(columns.get(str(f.get("target")), 0) <= columns.get(str(f.get("source")), 0)
                       for f in meta.get("sequence_flows") or [])
    # Leave a visible gutter around every external return corridor. Without
    # this envelope the outermost return can visually merge with the Pool
    # border even though their semantic endpoints differ.
    if return_count:
        max_content_x = max(max_content_x,
                            max((box.right for box in flow_boxes.values()), default=900)
                            + 120 + return_count * 14)
    pool_height = max_content_y + max(100, return_count * 34 + 75) + len(meta.get("annotations") or []) * 55
    # Corporate export uses ``--size page`` because draw.io Desktop can clip
    # child cells when ``--crop`` is combined with a swimlane container on a
    # scaled Windows display. Make the page an explicit envelope around the
    # pool instead of relying on the static A4-ish defaults above.
    graph.set("pageWidth", _number(POOL_X + max_content_x + 20))
    graph.set("pageHeight", _number(POOL_Y + pool_height + 20))
    pool = _cell(root, "PART_MAIN", str(model.get("process_name") or model.get("process_id") or "Процесс"),
                 "swimlane;horizontal=0;startSize=30;html=0;whiteSpace=wrap;fillColor=#FFFFFF;strokeColor=#1F2937;",
                 vertex=True)
    _geometry(pool, POOL_X, POOL_Y, max_content_x, pool_height)

    roles = {str(i.get("activity_id")): i for i in meta.get("role_overlays") or []}
    systems = {str(i.get("activity_id")): i for i in meta.get("system_overlays") or []}
    task_cells, artifact_boxes, system_boxes = {}, {}, {}
    for nid, info in nodes.items():
        box = flow_boxes[nid]
        if info.get("kind") == "activity":
            group_box, group_id = group_boxes[nid], f"GROUP.{nid}"
            group = _cell(root, group_id, "", "group;html=0;activityGroup=1;", parent="PART_MAIN", vertex=True)
            _geometry(group, group_box.x, group_box.y, group_box.width, group_box.height)
            role = roles.get(nid)
            if role:
                rc = _cell(root, str(role["id"]), _wrap(role.get("text"), 22, 2),
                           f"rounded=1;arcSize=45;html=0;whiteSpace=wrap;overflow=hidden;roleBadge=1;"
                           f"fillColor={theme['role_fill']};strokeColor={theme['role_stroke']};"
                           f"fontColor={theme['role_font']};fontSize=9;fontStyle=1;",
                           parent=group_id, vertex=True)
                _geometry(rc, TASK_X, 4, ROLE_WIDTH, ROLE_HEIGHT)
            label = _wrap(" / ".join([*(str(c) for c in info.get("display_codes") or []),
                                      str(info.get("name") or "")]), 24, 3)
            task = _cell(root, f"TASK.{nid}", label, _task_style(str(info.get("task_type")), theme),
                         parent=group_id, vertex=True)
            _geometry(task, TASK_X, TASK_Y, TASK_WIDTH, TASK_HEIGHT); task_cells[nid] = f"TASK.{nid}"
            system = systems.get(nid, {"id": f"SYSTEM.{nid}", "text": "OPEN"})
            sx, sy = TASK_X + (TASK_WIDTH - SYSTEM_WIDTH) / 2, TASK_Y + TASK_HEIGHT + 7
            sc = _cell(root, str(system["id"]), _wrap(system.get("text") or "OPEN", 20, 2),
                       f"rounded=1;arcSize=50;html=0;whiteSpace=wrap;overflow=hidden;systemBadge=1;"
                       f"align=center;verticalAlign=middle;fontSize=9;fillColor={theme['system_fill']};"
                       f"strokeColor={theme['system_stroke']};fontColor={theme['system_font']};",
                       parent=group_id, vertex=True)
            _geometry(sc, sx, sy, SYSTEM_WIDTH, SYSTEM_HEIGHT)
            system_boxes[nid] = Box(group_box.x + sx, group_box.y + sy, SYSTEM_WIDTH, SYSTEM_HEIGHT)
            ins = [o for o in occurrence_by_activity.get(nid, []) if o.get("direction") == "input"]
            outs = [o for o in occurrence_by_activity.get(nid, []) if o.get("direction") == "output"]
            for direction, items, ax in (("input", ins, 14), ("output", outs, 112)):
                for i, occurrence in enumerate(items):
                    kind = str(occurrence.get("kind") or "other")
                    value = str(occurrence.get("name") or "")
                    if occurrence.get("state"): value += f"\n[{occurrence['state']}]"
                    ay, aid = ARTIFACT_TOP + i * (ARTIFACT_HEIGHT + ARTIFACT_GAP), str(occurrence["artifact_instance_id"])
                    art = _cell(root, aid, _wrap(value, 17, 4),
                                "shape=note;size=12;html=0;whiteSpace=wrap;overflow=hidden;align=center;"
                                f"verticalAlign=middle;fillColor={theme.get(f'{kind}_fill', theme['other_artifact_fill'])};"
                                "strokeColor=#6B7280;fontSize=8;artifact=1;",
                                parent=group_id, vertex=True)
                    _geometry(art, ax, ay, ARTIFACT_WIDTH, ARTIFACT_HEIGHT)
                    artifact_boxes[aid] = Box(group_box.x + ax, group_box.y + ay, ARTIFACT_WIDTH, ARTIFACT_HEIGHT)
            continue
        task_cells[nid] = nid
        if info.get("kind") == "gateway":
            symbol = {"xor": "exclusiveGw", "and": "parallelGw", "or": "inclusiveGw",
                      "event_based": "eventGw"}.get(info.get("gateway_type"), "exclusiveGw")
            cell = _cell(root, nid, "", f"shape=mxgraph.bpmn.gateway;gatewayType={symbol};html=0;"
                         "whiteSpace=wrap;fillColor=#FFFFFF;strokeColor=#404040;",
                         parent="PART_MAIN", vertex=True)
            _geometry(cell, box.x, box.y, box.width, box.height)
            if info.get("gateway_role") != "merge" and str(info.get("name") or "").strip():
                label = _wrap(info.get("name"), 20, 4); height = max(28, len(label.splitlines()) * 13 + 4)
                _label_cell(root, f"LABEL.{nid}", label, Box(box.cx - 68, box.top - height - 8, 136, height), kind="gateway")
        else:
            kind = info.get("task_type")
            event_definition = str(info.get("event_definition") or "none")
            definition_style = f"eventDefinition={event_definition};" if event_definition != "none" else ""
            style = ("ellipse;html=0;aspect=fixed;fillColor=#FFFFFF;strokeWidth=2;eventKind=start;" + definition_style if kind == "start_event"
                     else "ellipse;html=0;aspect=fixed;fillColor=#FFFFFF;strokeWidth=4;eventKind=end;" if kind == "end_event"
                     else "ellipse;html=0;aspect=fixed;fillColor=#FFFFFF;strokeWidth=2;double=1;eventKind=intermediate;")
            cell = _cell(root, nid, "", style, parent="PART_MAIN", vertex=True); _geometry(cell, box.x, box.y, box.width, box.height)
            if str(info.get("name") or "").strip():
                label = _wrap(info.get("name"), 21, 5); height = max(28, len(label.splitlines()) * 13 + 4)
                _label_cell(root, f"LABEL.{nid}", label, Box(box.cx - 72, box.bottom + 8, 144, height), kind="event")

    return_base = max_content_y + 48
    return_right = max((box.right for box in flow_boxes.values()), default=max_content_x) + 80
    sequence_flows = meta.get("sequence_flows") or []
    outgoing_slots, incoming_slots = {}, {}
    for flow in sequence_flows:
        outgoing_slots.setdefault(str(flow.get("source")), []).append(str(flow.get("id")))
        incoming_slots.setdefault(str(flow.get("target")), []).append(str(flow.get("id")))
    for index, flow in enumerate(sequence_flows):
        source_id, target_id = str(flow.get("source")), str(flow.get("target"))
        if source_id not in flow_boxes or target_id not in flow_boxes: continue
        flow_id = str(flow.get("id") or f"F{index + 1}")
        source_slot = outgoing_slots.get(source_id, [flow_id]).index(flow_id)
        target_slot = incoming_slots.get(target_id, [flow_id]).index(flow_id)
        ports, points, route_kind = _sequence_route(flow, flow_boxes[source_id], flow_boxes[target_id],
                                                    flow_shapes[source_id], flow_shapes[target_id],
                                                    rows[source_id], rows[target_id], columns[source_id], columns[target_id],
                                                    primary_pairs, index, return_base, return_right,
                                                    source_slot, target_slot,
                                                    len(incoming_slots.get(target_id, [flow_id])))
        fid = flow_id
        edge = _cell(root, fid, "", "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=0;html=0;"
                     "endArrow=block;endFill=1;strokeColor=#374151;" + ports,
                     parent="PART_MAIN", edge=True, source=task_cells[source_id], target=task_cells[target_id],
                     extra={"default": "1" if flow.get("default") else "0", "routeKind": route_kind})
        _edge_geometry(edge, points)
        if flow.get("default"):
            source_cell = next((c for c in root.findall("mxCell") if c.get("id") == task_cells[source_id]), None)
            if source_cell is not None: source_cell.set("defaultFlow", fid)
        condition = str(flow.get("condition") or "").strip()
        if condition:
            source = flow_boxes[source_id]
            label_box = (Box(source.right + 8, source.cy - 25, 72, 20) if route_kind == "happy"
                         else Box(source.cx + 9, source.bottom + 10 + source_slot * 28, 76, 22) if route_kind in {"branch", "return"}
                         else Box(source.right + 8, source.cy + 8, 76, 22))
            _label_cell(root, f"LABEL.{fid}", _wrap(condition, 12, 2), label_box,
                        kind="branch", extra_style=f"flowRef={fid};")

    assoc_by_artifact = {str(i.get("artifact_instance_id")): i for i in meta.get("data_associations") or []}
    for occurrence in meta.get("artifact_occurrences") or []:
        aid, sid = str(occurrence.get("artifact_instance_id")), str(occurrence.get("activity_id"))
        assoc = assoc_by_artifact.get(aid)
        if not assoc or aid not in artifact_boxes or sid not in group_boxes: continue
        direction, group, task, artifact = str(occurrence.get("direction")), group_boxes[sid], flow_boxes[sid], artifact_boxes[aid]
        siblings = [i for i in occurrence_by_activity.get(sid, []) if i.get("direction") == direction]
        sibling_index = next((i for i, item in enumerate(siblings) if item.get("artifact_instance_id") == aid), 0)
        # Nested fan-out without crossings: deeper artifact rows use a launch
        # level closer to the Task and a corridor farther outside the artifact
        # column. Port order follows the same planar ordering.
        launch_y = task.bottom - group.y + 24 - sibling_index * 4
        if direction == "input":
            source, target = aid, task_cells[sid]
            corridor_x = 12 - sibling_index * 2
            port_x = TASK_X + 14 - sibling_index * 3
            port_fraction = (port_x - TASK_X) / TASK_WIDTH
            points = [(corridor_x, artifact.cy - group.y), (corridor_x, launch_y),
                      (port_x, launch_y)]
            ports = _port_style((0, .5), (port_fraction, 1))
        else:
            source, target = task_cells[sid], aid
            corridor_x = GROUP_WIDTH - 12 + sibling_index * 2
            port_x = TASK_X + TASK_WIDTH - 14 + sibling_index * 3
            port_fraction = (port_x - TASK_X) / TASK_WIDTH
            points = [(port_x, launch_y), (corridor_x, launch_y),
                      (corridor_x, artifact.cy - group.y)]
            ports = _port_style((port_fraction, 1), (1, .5))
        edge = _cell(root, str(assoc["id"]), "", "edgeStyle=orthogonalEdgeStyle;rounded=0;jettySize=0;"
                     "dashed=1;html=0;endArrow=open;endFill=0;strokeColor=#6B7280;dataAssociation=1;" + ports,
                     parent=f"GROUP.{sid}", edge=True, source=source, target=target,
                     extra={"artifactPort": str(sibling_index + 1), "associationDirection": direction})
        _edge_geometry(edge, points)

    main = next((p for p in meta.get("participants") or [] if p.get("participant_kind") == "current_process"), None)
    external = [p for p in meta.get("participants") or [] if p is not main]
    participant_cells = {str((main or {}).get("id") or "PART_MAIN"): "PART_MAIN", "PART_MAIN": "PART_MAIN"}
    participant_boxes, occupied = {}, []
    messages = meta.get("message_flows") or []
    for index, participant in enumerate(external):
        pid = str(participant.get("id") or f"PART_EXT_{index + 1}")
        related = next((f for f in messages if str(f.get("source")) == pid or str(f.get("target")) == pid), None)
        linked_id = (str(related.get("target")) if related and str(related.get("source")) == pid
                     else str(related.get("source")) if related else "")
        linked = flow_boxes.get(linked_id)
        participant_name = _wrap(participant.get("name") or pid, 34, 4)
        participant_lines = participant_name.splitlines() or [""]
        participant_width = min(320, max(180, max(len(line) for line in participant_lines) * 6 + 20))
        participant_height = max(50, len(participant_lines) * 13 + 18)
        x = POOL_X + (linked.cx if linked else FLOW_LEFT + index * 230) - participant_width / 2
        candidate, tier = Box(max(20, x), 32, participant_width, participant_height), 0
        while any(not (candidate.right + 12 <= b.left or b.right + 12 <= candidate.left or
                       candidate.bottom + 8 <= b.top or b.bottom + 8 <= candidate.top) for b in occupied):
            tier += 1; candidate = Box(max(20, x), 32 + tier * (participant_height + 8),
                                       participant_width, participant_height)
        occupied.append(candidate); participant_boxes[pid] = candidate
        pcell = _cell(root, pid, "",
                      "swimlane;horizontal=1;startSize=0;html=0;whiteSpace=wrap;overflow=hidden;"
                      "fillColor=#F9FAFB;strokeColor=#6B7280;dashed=1;externalParticipant=1;", vertex=True)
        _geometry(pcell, candidate.x, candidate.y, candidate.width, candidate.height); participant_cells[pid] = pid
        _label_cell(root, f"LABEL.PARTICIPANT.{pid}", participant_name,
                    Box(candidate.x + 8, candidate.y + 7, candidate.width - 16, candidate.height - 14),
                    kind="participant", parent="1", extra_style=f"participantRef={pid};fontStyle=1;")

    for index, flow in enumerate(messages):
        source_id, target_id = str(flow.get("source")), str(flow.get("target"))
        source_cell = participant_cells.get(source_id, task_cells.get(source_id)); target_cell = participant_cells.get(target_id, task_cells.get(target_id))
        if not source_cell or not target_cell: continue
        source_external, target_external = source_id in participant_boxes, target_id in participant_boxes
        linked_id = target_id if source_external else source_id
        linked = flow_boxes.get(linked_id); participant = participant_boxes.get(source_id if source_external else target_id)
        if not linked or not participant: continue
        fid = str(flow.get("id") or f"MF{index + 1}")
        node_port = perimeter_port(flow_shapes.get(linked_id, RECTANGLE), "top", .9)
        node_x, participant_x, corridor_y = POOL_X + linked.x + linked.width * node_port[0], participant.cx, POOL_Y - 18 - index * 8
        points = [(participant_x, corridor_y), (node_x, corridor_y)]
        participant_port = perimeter_port(RECTANGLE, "bottom", .5)
        ports = (_port_style(participant_port, node_port) if source_external
                 else _port_style(node_port, participant_port))
        if not source_external: points.reverse()
        edge = _cell(root, fid, "", "edgeStyle=orthogonalEdgeStyle;rounded=0;jettySize=0;dashed=1;html=0;"
                     "endArrow=open;endFill=0;startArrow=oval;startFill=0;strokeColor=#1F2937;messageFlow=1;" + ports,
                     edge=True, source=source_cell, target=target_cell)
        _edge_geometry(edge, points)
        label = str(flow.get("name") or "").strip()
        if label:
            wrapped = _wrap(label, 24, 3)
            lines = wrapped.splitlines() or [""]
            label_width = min(220, max(110, max(len(line) for line in lines) * 6 + 16))
            label_height = max(20, len(lines) * 13 + 4)
            lx = min(participant_x, node_x) + abs(participant_x - node_x) / 2 - label_width / 2
            # Keep the message caption above its external participant; the
            # connector corridor remains in the gap between participants and
            # the main pool.
            _label_cell(root, f"LABEL.{fid}", wrapped,
                        Box(lx, max(4, participant.top - label_height - 4), label_width, label_height),
                        kind="message", parent="1", extra_style=f"flowRef={fid};")

    for index, annotation in enumerate(meta.get("annotations") or []):
        aid = str(annotation.get("id") or f"ANNOTATION.{index + 1}")
        cell = _cell(root, aid, _wrap(annotation.get("text"), 34, 4),
                     "shape=note;size=12;html=0;whiteSpace=wrap;overflow=hidden;fillColor=#FFFDEB;"
                     "strokeColor=#9CA3AF;fontSize=9;annotation=1;", parent="PART_MAIN", vertex=True)
        _geometry(cell, 40 + index * 240, pool_height - 62, 220, 45)

    diagram = ET.Element("diagram", {"id": str(model.get("process_id") or "process"), "name": "Page-1"})
    diagram.append(graph)
    mxfile = ET.Element("mxfile", {"host": "Agent Skills", "agent": "arman-bpmn-generator", "version": "2.1"})
    mxfile.append(diagram); output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(mxfile).write(output, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model"); parser.add_argument("render_meta"); parser.add_argument("output")
    parser.add_argument("--theme", default="config/bpmn-theme.json"); args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8")); meta = json.loads(Path(args.render_meta).read_text(encoding="utf-8"))
    theme_path = Path(args.theme); theme = json.loads(theme_path.read_text(encoding="utf-8")) if theme_path.is_file() else {}
    render(model, meta, Path(args.output), theme); print(f"Corporate lane-less draw.io saved: {args.output}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
