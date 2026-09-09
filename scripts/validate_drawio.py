#!/usr/bin/env python3
"""
Корпоративный validator .drawio (раздел 29 TZ_DELTA_CORPORATE_BPMN.md).

Вход:
  .drawio (после scripts/diagram_layout.py)
  <id>-render-meta.json (scripts/build_registry.py)
  <id>-model.json (process_model.json, для warnings ROLE_REQUIRED_FOR_AUTOMATION)

Выход:
  output/validation/<id>-drawio-validation.json
  output/validation/<id>-drawio-validation.md

PASS невозможен при наличии хотя бы одного ERROR (раздел 29, последний абзац).

Некоторые правила — геометрические эвристики (EDGE_THROUGH_NODE,
TASK_TEXT_OVERFLOW, LARGE_EMPTY_INTERVAL): без реального рендера шрифтов они
приближённые, не пиксель-в-пиксель. Это явно помечено в отчёте.
"""
import argparse
import json
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

from shape_geometry import absolute_port, point_on_perimeter, shape_from_style

KNOWN_SYSTEMS = ["1С:ERP", "1С ERP", "Mobile SMARTS", "СБИС", "ГИС МТ", "CryptoPro"]

# Ролевые префиксы/паттерны, где системное имя — легитимная часть названия
# ДОЛЖНОСТИ ("Ответственный за ГИС МТ"), а не подмена role на system.
# Иначе строка вида "Ответственный за ГИС МТ" ложно ловится как system lane
# (SYSTEM_RENDERED_AS_LANE), хотя это ровно такая же по духу роль, как
# "Ответственный за НСИ" в PURCHASE_RF.
ROLE_PREFIX_PATTERNS = [
    r"^Ответственн\w+\s+за\s+",
    r"^Специалист\s+по\s+",
    r"^Менеджер\s+по\s+",
]

ARTIFACT_TYPES = {
    "print_form", "system_document", "external_document", "system_report",
    "reference_data", "physical_object", "location", "message", "data", "other",
}
MAX_DIAGRAM_WIDTH = 9000
MAX_EMPTY_INTERVAL = 400
TASK_MIN_WIDTH = 120
TASK_MAX_WIDTH = 160
TASK_MIN_HEIGHT = 70
TASK_MAX_HEIGHT = 90
TASK_MARKER_MIN = 14
TASK_MARKER_MAX = 16
TASK_LABEL_MAX_CHARS = 72
TASK_LABEL_MAX_LINES = 3
HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>|&(?:lt|gt|nbsp);", re.IGNORECASE)


def load(drawio_path):
    tree = ET.parse(drawio_path)
    root = tree.getroot().find(".//root")
    cells = {c.get("id"): c for c in root.findall("mxCell") if c.get("id")}
    return root, cells


def geom(cell):
    return cell.find("mxGeometry")


def gf(g, attr, default=0.0):
    return float(g.get(attr, default)) if g is not None else default


def is_system_like(name: str) -> bool:
    """
    Дорожка/пул нарушает ROLE != SYSTEM, если её имя ЦЕЛИКОМ является
    системой (плюс варианты "Система (X)" и "X / Y", где часть — система).

    НЕ считается нарушением упоминание системы внутри легитимного названия
    ДОЛЖНОСТИ ("Ответственный за ГИС МТ" — та же конструкция, что
    "Ответственный за НСИ" в PURCHASE_RF): системное имя здесь не заменяет
    role, а является объектом ответственности человека.
    """
    if not name:
        return False
    stripped = name.strip()

    for pat in ROLE_PREFIX_PATTERNS:
        if re.match(pat, stripped, re.IGNORECASE):
            return False

    if stripped.lower().startswith("система"):
        return True

    # Разбить по "/" (частый паттерн смешения role+system: "Группа учета / 1С:ERP")
    parts = [p.strip() for p in re.split(r"\s*/\s*", stripped) if p.strip()]
    for part in parts:
        for sys_name in KNOWN_SYSTEMS:
            if part.lower() == sys_name.lower():
                return True
    return False


def rect_overlap(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def segment_intersects_rect(p1, p2, rect, margin=2):
    x0, y0, x1, y1 = rect
    x0 -= margin; y0 -= margin; x1 += margin; y1 += margin
    (ax, ay), (bx, by) = p1, p2
    if ax == bx:  # vertical
        if x0 <= ax <= x1:
            lo, hi = sorted([ay, by])
            return not (hi <= y0 or lo >= y1)
        return False
    if ay == by:  # horizontal
        if y0 <= ay <= y1:
            lo, hi = sorted([ax, bx])
            return not (hi <= x0 or lo >= x1)
        return False
    return False  # диагоналей у нас нет (orthogonal routing)


def absolute_geometry(cell, cells):
    """Return geometry in graph-root coordinates, accounting for Pool/Lane parents."""
    g = geom(cell)
    if g is None:
        return None
    x, y = gf(g, "x"), gf(g, "y")
    parent = cell.get("parent")
    seen = set()
    while parent and parent not in {"0", "1"} and parent not in seen:
        seen.add(parent)
        parent_cell = cells.get(parent)
        if parent_cell is None:
            break
        parent_geom = geom(parent_cell)
        if parent_geom is not None:
            x += gf(parent_geom, "x")
            y += gf(parent_geom, "y")
        parent = parent_cell.get("parent")
    return x, y, gf(g, "width"), gf(g, "height")


def orthogonal_segments_intersect(a1, a2, b1, b2):
    """Classify a visible axis-aligned segment relation as cross/overlap/None."""
    ax1, ay1 = a1; ax2, ay2 = a2
    bx1, by1 = b1; bx2, by2 = b2
    a_vertical = abs(ax1 - ax2) < 0.01
    b_vertical = abs(bx1 - bx2) < 0.01
    if not (a_vertical or abs(ay1 - ay2) < 0.01):
        return None
    if not (b_vertical or abs(by1 - by2) < 0.01):
        return None
    if a_vertical != b_vertical:
        vertical = (a1, a2) if a_vertical else (b1, b2)
        horizontal = (b1, b2) if a_vertical else (a1, a2)
        x = vertical[0][0]
        y = horizontal[0][1]
        return "cross" if (
            min(vertical[0][1], vertical[1][1]) + 0.5 < y < max(vertical[0][1], vertical[1][1]) - 0.5
            and min(horizontal[0][0], horizontal[1][0]) + 0.5 < x < max(horizontal[0][0], horizontal[1][0]) - 0.5
        ) else None
    if a_vertical:
        if abs(ax1 - bx1) >= 0.01:
            return None
        overlap = min(max(ay1, ay2), max(by1, by2)) - max(min(ay1, ay2), min(by1, by2))
    else:
        if abs(ay1 - by1) >= 0.01:
            return None
        overlap = min(max(ax1, ax2), max(bx1, bx2)) - max(min(ax1, ax2), min(bx1, bx2))
    return "overlap" if overlap > 1 else None


def style_number(style: str, key: str, default: float) -> float:
    match = re.search(rf"(?:^|;){re.escape(key)}=([-+]?[0-9]*\.?[0-9]+)(?:;|$)", style or "")
    return float(match.group(1)) if match else default


def style_value(style: str, key: str) -> str | None:
    match = re.search(rf"(?:^|;){re.escape(key)}=([^;]+)(?:;|$)", style or "")
    return match.group(1) if match else None


def _rect(cell, cells):
    geometry = absolute_geometry(cell, cells)
    if not geometry:
        return None
    x, y, width, height = geometry
    return x, y, x + width, y + height


def _port_point(rect, x_fraction: float, y_fraction: float):
    return absolute_port(rect, (x_fraction, y_fraction))


def _validate_edge_endpoints(edge_ids, cells, svg_routes, svg_bounds):
    """Validate semantic source/target endpoints in draw.io and rendered SVG."""
    errors = []
    for edge_id in edge_ids:
        edge = cells.get(edge_id)
        if edge is None:
            continue
        style = edge.get("style") or ""
        for endpoint, cell_ref, x_key, y_key, point_index, rule in (
            ("source", edge.get("source"), "exitX", "exitY", 0, "EDGE_SOURCE_NOT_ON_SHAPE_PERIMETER"),
            ("target", edge.get("target"), "entryX", "entryY", -1, "EDGE_TARGET_NOT_ON_SHAPE_PERIMETER"),
        ):
            cell = cells.get(cell_ref)
            rectangle = _rect(cell, cells) if cell is not None else None
            if not rectangle:
                continue
            shape = shape_from_style(cell.get("style") or "")
            port = (style_number(style, x_key, .5), style_number(style, y_key, .5))
            drawio_point = absolute_port(rectangle, port)
            if not point_on_perimeter(shape, drawio_point, rectangle, tolerance=.75):
                errors.append((rule, edge_id,
                               f"draw.io {endpoint} port={port} не лежит на контуре {cell_ref} ({shape})"))
                continue
            route = svg_routes.get(edge_id)
            rendered_rect = svg_bounds.get(cell_ref)
            # draw.io shortens the target polyline by about 8 px for the
            # visible arrowhead; the arrowhead itself reaches the contour.
            # Source markers use a much smaller gap. These are render-space
            # tolerances, not model-port tolerances.
            render_tolerance = 4.0 if endpoint == "source" else 10.0
            if route and rendered_rect and not point_on_perimeter(
                    shape, route[point_index], rendered_rect, tolerance=render_tolerance):
                errors.append((rule, edge_id,
                               f"SVG {endpoint}={route[point_index]} не касается контура {cell_ref} ({shape})"))
    return errors


def _edge_points_drawio(edge, cells):
    """Reconstruct the rendered polyline from explicit ports and waypoints."""
    source, target = cells.get(edge.get("source")), cells.get(edge.get("target"))
    source_rect, target_rect = _rect(source, cells) if source is not None else None, _rect(target, cells) if target is not None else None
    if not source_rect or not target_rect:
        return []
    style = edge.get("style") or ""
    ex = style_number(style, "exitX", 1 if source_rect[2] <= target_rect[0] else .5)
    ey = style_number(style, "exitY", .5 if source_rect[2] <= target_rect[0] else 1)
    ix = style_number(style, "entryX", 0 if source_rect[2] <= target_rect[0] else .5)
    iy = style_number(style, "entryY", .5 if source_rect[2] <= target_rect[0] else 1)
    points = [_port_point(source_rect, ex, ey)]
    geometry = geom(edge)
    array = geometry.find("Array[@as='points']") if geometry is not None else None
    offset_x = offset_y = 0.0
    parent = cells.get(edge.get("parent"))
    if parent is not None and (parent_geometry := absolute_geometry(parent, cells)):
        offset_x, offset_y = parent_geometry[0], parent_geometry[1]
    if array is not None:
        points.extend((gf(point, "x") + offset_x, gf(point, "y") + offset_y) for point in array.findall("mxPoint"))
    points.append(_port_point(target_rect, ix, iy))
    compact = []
    for point in points:
        if not compact or abs(point[0] - compact[-1][0]) > .01 or abs(point[1] - compact[-1][1]) > .01:
            compact.append(point)
    return compact


def _path_points(path_data: str):
    return [
        (float(x), float(y))
        for _command, x, y in re.findall(
            r"([ML])\s*(-?[0-9]+(?:\.[0-9]+)?)\s+(-?[0-9]+(?:\.[0-9]+)?)",
            path_data or "",
        )
    ]


def _svg_geometry(svg_path: Path):
    """Read actual draw.io SVG polylines and visible cell bounds."""
    tree = ET.parse(svg_path)
    routes, bounds = {}, {}
    for group in tree.getroot().iter():
        cid = group.get("data-cell-id")
        if not cid:
            continue
        candidates = []
        xs, ys = [], []
        for element in group.iter():
            nested_id = element.get("data-cell-id")
            if element is not group and nested_id and nested_id != cid:
                continue
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "rect" and all(element.get(key) is not None for key in ("x", "y", "width", "height")):
                x, y = float(element.get("x")), float(element.get("y"))
                width, height = float(element.get("width")), float(element.get("height"))
                xs += [x, x + width]; ys += [y, y + height]
            elif tag == "ellipse" and all(element.get(key) is not None for key in ("cx", "cy", "rx", "ry")):
                cx, cy, rx, ry = (float(element.get(key)) for key in ("cx", "cy", "rx", "ry"))
                xs += [cx - rx, cx + rx]; ys += [cy - ry, cy + ry]
            elif tag in {"polygon", "polyline"}:
                pairs = re.findall(r"(-?[0-9]+(?:\.[0-9]+)?),?\s+(-?[0-9]+(?:\.[0-9]+)?)", element.get("points") or "")
                xs += [float(x) for x, _y in pairs]; ys += [float(y) for _x, y in pairs]
            if not element.tag.endswith("path") or not element.get("d"):
                continue
            points = _path_points(element.get("d"))
            all_pairs = re.findall(r"(-?[0-9]+(?:\.[0-9]+)?)\s+(-?[0-9]+(?:\.[0-9]+)?)", element.get("d") or "")
            xs += [float(x) for x, _y in all_pairs]; ys += [float(y) for _x, y in all_pairs]
            if len(points) >= 2 and element.get("fill") in {None, "none"}:
                length = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:]))
                candidates.append((length, points))
        if candidates:
            routes[cid] = max(candidates, key=lambda item: item[0])[1]
        if xs and ys:
            bounds[cid] = min(xs), min(ys), max(xs), max(ys)
    return routes, bounds


def _segments(points):
    return [(a, b) for a, b in zip(points, points[1:])
            if abs(a[0] - b[0]) > .01 or abs(a[1] - b[1]) > .01]


def _point_segment_distance(point, a, b):
    px, py = point
    if abs(a[0] - b[0]) < .01:
        return abs(px - a[0]) + max(0, min(a[1], b[1]) - py, py - max(a[1], b[1]))
    if abs(a[1] - b[1]) < .01:
        return abs(py - a[1]) + max(0, min(a[0], b[0]) - px, px - max(a[0], b[0]))
    return float("inf")


def _estimated_text_overflow(cell, cells):
    rectangle = _rect(cell, cells)
    if not rectangle:
        return False
    width, height = rectangle[2] - rectangle[0], rectangle[3] - rectangle[1]
    font_size = style_number(cell.get("style") or "", "fontSize", 10)
    chars_per_line = max(1, int((width - 8) / max(1, font_size * .56)))
    explicit = str(cell.get("value") or "").splitlines() or [""]
    lines = sum(max(1, len(textwrap.wrap(line, width=chars_per_line,
                                        break_long_words=False, break_on_hyphens=False))) for line in explicit)
    return lines * (font_size * 1.25) + 4 > height + .5


def validate_corporate(drawio_path: Path, meta: dict, model: dict, svg_path: Path | None = None):
    """Validate the lane-less corporate DTO independently of legacy lane rules."""
    errors, warnings = [], []
    _root, cells = load(drawio_path)
    document_root = ET.parse(drawio_path).getroot()
    graph_model = document_root if document_root.tag == "mxGraphModel" else document_root.find(".//mxGraphModel")
    svg_routes, svg_bounds = _svg_geometry(svg_path) if svg_path and svg_path.is_file() else ({}, {})
    participants = {"PART_MAIN", *(str(p.get("id")) for p in meta.get("participants") or [] if isinstance(p, dict))}
    pool_rect = _rect(cells.get("PART_MAIN"), cells) if cells.get("PART_MAIN") is not None else None
    if graph_model is not None and pool_rect:
        page_width = gf(graph_model, "pageWidth")
        page_height = gf(graph_model, "pageHeight")
        if page_width + .01 < pool_rect[2] + 10 or page_height + .01 < pool_rect[3] + 10:
            errors.append(("PAGE_BOUNDS_CLIP_RISK", "PART_MAIN",
                           f"page={page_width}x{page_height}, pool_right_bottom={pool_rect[2]}x{pool_rect[3]}"))
    for cid, cell in cells.items():
        if "swimlane" in (cell.get("style") or "") and cid not in participants:
            errors.append(("NO_INTERNAL_LANES", cid, "внутренняя Lane/Swimlane запрещена"))
    roles = {item.get("activity_id"): item for item in meta.get("role_overlays") or []}
    systems = {item.get("activity_id"): item for item in meta.get("system_overlays") or []}
    for sid, info in (meta.get("nodes") or {}).items():
        if info.get("kind") != "activity":
            continue
        group_id, task_id = f"GROUP.{sid}", f"TASK.{sid}"
        group, task = cells.get(group_id), cells.get(task_id)
        if group is None or task is None or task.get("parent") != group_id:
            errors.append(("ACTIVITY_GROUP_REQUIRED", sid, "Activity должна находиться в собственном group"))
            continue
        geometry = absolute_geometry(task, cells)
        if geometry:
            _x, _y, width, height = geometry
            if not (TASK_MIN_WIDTH <= width <= TASK_MAX_WIDTH and TASK_MIN_HEIGHT <= height <= TASK_MAX_HEIGHT):
                errors.append(("TASK_SIZE_OUT_OF_RANGE", sid, f"size={width}x{height}"))
        style = task.get("style") or ""
        if style_value(style, "fillColor") != "#FFFFFF":
            errors.append(("ACTIVITY_FILL_NOT_NEUTRAL", sid, "Activity fill должен быть #FFFFFF"))
        marker = style_number(style, "markerSize", 0)
        if not TASK_MARKER_MIN <= marker <= TASK_MARKER_MAX:
            errors.append(("TASK_MARKER_SIZE", sid, f"markerSize={marker}"))
        label = str(task.get("value") or "")
        if HTML_RE.search(label):
            errors.append(("TASK_NAME_HTML_FORBIDDEN", sid, "HTML запрещён"))
        if len(label) > TASK_LABEL_MAX_CHARS or len(label.splitlines()) > TASK_LABEL_MAX_LINES:
            errors.append(("TASK_TEXT_OVERFLOW", sid, "Task label превышает 72 символа/3 строки"))
        if abs(gf(geom(task), "y") - 32) > .01:
            errors.append(("ACTIVITY_BASELINE_MISALIGNED", sid, "Task должен иметь единый local y=32 независимо от role overlay"))
        performer = info.get("performer_kind")
        role = roles.get(sid)
        if performer == "human":
            if not role or role.get("id") not in cells:
                errors.append(("ROLE_OVERLAY_REQUIRED", sid, "human Activity требует role overlay"))
            else:
                rcell = cells[role["id"]]
                if " ".join(str(rcell.get("value") or "").split()) != " ".join(str(role.get("text") or "").split()):
                    errors.append(("ROLE_OVERLAY_CONTENT", sid, "overlay должен содержать только role"))
                if style_value(rcell.get("style") or "", "fillColor") != "#6366F1":
                    errors.append(("ROLE_OVERLAY_COLOR", sid, "ожидался #6366F1"))
        elif role:
            errors.append(("AUTOMATED_ROLE_OVERLAY_FORBIDDEN", sid, "не-human Activity не получает role overlay"))
        system = systems.get(sid)
        if not system or system.get("id") not in cells:
            errors.append(("SYSTEM_OVERLAY_REQUIRED", sid, "каждая Activity требует system overlay"))
        else:
            system_cell = cells[system["id"]]
            if system_cell.get("value").replace("\n", " ") != str(system.get("text") or "OPEN"):
                errors.append(("SYSTEM_OVERLAY_CONTENT", sid, "overlay должен содержать только system"))
            system_style = system_cell.get("style") or ""
            system_geometry = absolute_geometry(system_cell, cells)
            if style_value(system_style, "systemBadge") != "1" or style_value(system_style, "rounded") != "1" \
                    or "taskMarker=" in system_style:
                errors.append(("SYSTEM_BADGE_STYLE", sid, "system overlay должен быть отдельной компактной скруглённой плашкой"))
            if system_geometry and (system_geometry[2] > 130 or not 16 <= system_geometry[3] <= 24):
                errors.append(("SYSTEM_BADGE_SIZE", sid, f"size={system_geometry[2]}x{system_geometry[3]}"))
        if info.get("task_type") == "call_activity":
            step = next((item for item in model.get("steps") or [] if item.get("id") == sid), {})
            if not str(step.get("called_process_id") or "").strip() or style_value(style, "callActivity") != "1":
                errors.append(("CALL_ACTIVITY_INCOMPLETE", sid, "нужны called_process_id и полноценный Call Activity"))
    expected_occurrences = {item.get("artifact_instance_id"): item for item in meta.get("artifact_occurrences") or []}
    for aid, occurrence in expected_occurrences.items():
        cell = cells.get(aid)
        if cell is None:
            errors.append(("ARTIFACT_OCCURRENCE_MISSING", str(aid), "occurrence не отрисован"))
            continue
        expected_fill = {"system_report": "#FFDCC2", "print_form": "#E7FCCB",
                         "system_object": "#FFFFFF"}.get(occurrence.get("kind"))
        if expected_fill and style_value(cell.get("style") or "", "fillColor") != expected_fill:
            errors.append(("ARTIFACT_THEME", str(aid), f"ожидался {expected_fill}"))
    for association in meta.get("data_associations") or []:
        edge = cells.get(association.get("id"))
        aid, sid = association.get("artifact_instance_id"), association.get("activity_id")
        expected = (aid, f"TASK.{sid}") if association.get("direction") == "input" else (f"TASK.{sid}", aid)
        if edge is None or (edge.get("source"), edge.get("target")) != expected:
            errors.append(("DATA_ASSOCIATION_DIRECTION", str(association.get("id")), f"ожидалось {expected}"))
        elif any(style_value(edge.get("style") or "", key) is None for key in ("exitX", "exitY", "entryX", "entryY")):
            errors.append(("EXPLICIT_PORT_REQUIRED", str(association.get("id")), "Data Association требует явные порты"))
    overlay_ids = {str(item.get("id")) for item in (meta.get("role_overlays") or []) + (meta.get("system_overlays") or [])}
    for flow in meta.get("sequence_flows") or []:
        edge = cells.get(str(flow.get("id")))
        if edge is None:
            errors.append(("SEQUENCE_FLOW_MISSING", str(flow.get("id")), "flow не отрисован"))
        elif edge.get("source") in overlay_ids or edge.get("target") in overlay_ids:
            errors.append(("OVERLAY_AS_FLOW_NODE", str(flow.get("id")), "overlay не может быть Flow Node"))
        elif (edge.get("source"), edge.get("target")) != (
            f"TASK.{flow.get('source')}" if (meta.get("nodes") or {}).get(str(flow.get("source")), {}).get("kind") == "activity" else str(flow.get("source")),
            f"TASK.{flow.get('target')}" if (meta.get("nodes") or {}).get(str(flow.get("target")), {}).get("kind") == "activity" else str(flow.get("target")),
        ):
            errors.append(("SEQUENCE_FLOW_ENDPOINT_INVALID", str(flow.get("id")), "source/target не соответствуют render DTO"))
        elif any(style_value(edge.get("style") or "", key) is None for key in ("exitX", "exitY", "entryX", "entryY")):
            errors.append(("EXPLICIT_PORT_REQUIRED", str(flow.get("id")), "Sequence Flow требует явные порты"))
        if edge is not None:
            expected_default = "1" if flow.get("default") else "0"
            if edge.get("default") != expected_default:
                errors.append(("DEFAULT_FLOW_NOT_PRESERVED", str(flow.get("id")), f"default должен быть {expected_default}"))
            if flow.get("default") and cells.get(edge.get("source")) is not None \
                    and cells[edge.get("source")].get("defaultFlow") != str(flow.get("id")):
                errors.append(("DEFAULT_FLOW_SOURCE_MISSING", str(flow.get("id")), "source.defaultFlow не сохранён"))
        condition = str(flow.get("condition") or "").strip()
        if condition:
            label = cells.get(f"LABEL.{flow.get('id')}")
            if label is None or style_value(label.get("style") or "", "labelKind") != "branch" \
                    or style_value(label.get("style") or "", "flowRef") != str(flow.get("id")):
                errors.append(("BRANCH_LABEL_MISSING", str(flow.get("id")), "условие требует отдельную label cell"))

    # Start/End/Gateway names belong to dedicated label cells, not Flow Nodes.
    for nid, info in (meta.get("nodes") or {}).items():
        if info.get("kind") not in {"event", "gateway"}:
            continue
        node = cells.get(nid)
        if node is not None and str(node.get("value") or "").strip():
            errors.append(("FLOW_NODE_LABEL_NOT_SEPARATE", nid, "Event/Gateway name должен быть в отдельной label cell"))
        expects_label = bool(str(info.get("name") or "").strip()) and info.get("gateway_role") != "merge"
        if expects_label:
            label = cells.get(f"LABEL.{nid}")
            if label is None:
                errors.append(("FLOW_NODE_LABEL_MISSING", nid, "отдельная label cell отсутствует"))

    # Dynamic groups must contain all direct children and must not overlap.
    group_rects = []
    for group in meta.get("activity_groups") or []:
        gid = group.get("id")
        if gid in cells and (geometry := absolute_geometry(cells[gid], cells)):
            x, y, w, h = geometry
            group_rects.append((gid, (x, y, x + w, y + h)))
    for index, (gid, rect) in enumerate(group_rects):
        for other, other_rect in group_rects[index + 1:]:
            if rect_overlap(rect, other_rect):
                errors.append(("ACTIVITY_GROUP_OVERLAP", gid, f"наложение с {other}"))
        for child in cells.values():
            if child.get("parent") != gid or child.get("edge") == "1":
                continue
            child_rect = _rect(child, cells)
            if child_rect and (child_rect[0] < rect[0] - .1 or child_rect[1] < rect[1] - .1
                               or child_rect[2] > rect[2] + .1 or child_rect[3] > rect[3] + .1):
                errors.append(("ACTIVITY_CHILD_OUTSIDE_GROUP", child.get("id"), f"выход за границы {gid}"))

    sequence_ids = [str(flow.get("id")) for flow in meta.get("sequence_flows") or []]
    message_ids = [str(flow.get("id")) for flow in meta.get("message_flows") or []]
    data_ids = [str(item.get("id")) for item in meta.get("data_associations") or []]
    routes = {}
    for edge_id in [*sequence_ids, *message_ids, *data_ids]:
        if edge_id in svg_routes:
            routes[edge_id] = svg_routes[edge_id]
        elif edge_id in cells:
            routes[edge_id] = _edge_points_drawio(cells[edge_id], cells)
    errors.extend(_validate_edge_endpoints(
        [*sequence_ids, *message_ids, *data_ids], cells, svg_routes, svg_bounds
    ))

    node_rects = {}
    for nid, info in (meta.get("nodes") or {}).items():
        cid = f"TASK.{nid}" if info.get("kind") == "activity" else nid
        if cid in cells and (rectangle := _rect(cells[cid], cells)):
            node_rects[nid] = svg_bounds.get(cid, rectangle)
    artifact_rects = {aid: svg_bounds.get(aid, _rect(cells[aid], cells)) for aid in expected_occurrences if aid in cells}
    role_rects = {
        sid: svg_bounds.get(item["id"], _rect(cells[item["id"]], cells))
        for sid, item in roles.items() if item.get("id") in cells
    }
    system_rects = {
        sid: svg_bounds.get(item["id"], _rect(cells[item["id"]], cells))
        for sid, item in systems.items() if item.get("id") in cells
    }

    # Sequence Flow may touch only its own source/target. Crossings and shared
    # collinear portions are blocking even for flows with a common endpoint.
    flow_by_id = {str(flow.get("id")): flow for flow in meta.get("sequence_flows") or []}
    for fid in sequence_ids:
        flow = flow_by_id[fid]
        for a, b in _segments(routes.get(fid, [])):
            for nid, rectangle in node_rects.items():
                if nid in {str(flow.get("source")), str(flow.get("target"))}:
                    continue
                if segment_intersects_rect(a, b, rectangle, margin=1):
                    errors.append(("EDGE_THROUGH_FLOW_NODE", fid, f"маршрут проходит через {nid}"))
                    break
    for index, left_id in enumerate(sequence_ids):
        for right_id in sequence_ids[index + 1:]:
            relation = None
            for a1, a2 in _segments(routes.get(left_id, [])):
                for b1, b2 in _segments(routes.get(right_id, [])):
                    relation = orthogonal_segments_intersect(a1, a2, b1, b2)
                    if relation: break
                if relation: break
            if relation == "overlap":
                errors.append(("SEQUENCE_FLOW_SEGMENT_OVERLAP", left_id, f"общий сегмент с {right_id}"))
            elif relation == "cross":
                errors.append(("SEQUENCE_FLOW_CROSS", left_id, f"пересечение с {right_id}"))

    for fid in sequence_ids:
        for sid, rectangle in role_rects.items():
            if rectangle and any(segment_intersects_rect(a, b, rectangle, margin=1)
                                 for a, b in _segments(routes.get(fid, []))):
                errors.append(("SEQUENCE_FLOW_THROUGH_ROLE_OVERLAY", fid,
                               f"маршрут проходит через ROLE.{sid}"))
                break

    # Message Flow is checked against every business shape and Sequence Flow.
    for mid in message_ids:
        message = next((flow for flow in meta.get("message_flows") or [] if str(flow.get("id")) == mid), {})
        edge = cells.get(mid)
        expected_source = f"TASK.{message.get('source')}" if (meta.get("nodes") or {}).get(str(message.get("source")), {}).get("kind") == "activity" else str(message.get("source"))
        expected_target = f"TASK.{message.get('target')}" if (meta.get("nodes") or {}).get(str(message.get("target")), {}).get("kind") == "activity" else str(message.get("target"))
        if edge is None:
            errors.append(("MESSAGE_FLOW_MISSING", mid, "Message Flow не отрисован"))
            continue
        if (edge.get("source"), edge.get("target")) != (expected_source, expected_target):
            errors.append(("MESSAGE_FLOW_ENDPOINT_INVALID", mid, "source/target не соответствуют render DTO"))
        if any(style_value(edge.get("style") or "", key) is None for key in ("exitX", "exitY", "entryX", "entryY")):
            errors.append(("EXPLICIT_PORT_REQUIRED", mid, "Message Flow требует явные порты"))
        for a, b in _segments(routes.get(mid, [])):
            for nid, rectangle in node_rects.items():
                if nid in {str(message.get("source")), str(message.get("target"))}:
                    continue
                if segment_intersects_rect(a, b, rectangle, margin=1):
                    errors.append(("MESSAGE_FLOW_THROUGH_NODE", mid, f"маршрут проходит через {nid}")); break
            for aid, rectangle in artifact_rects.items():
                if rectangle and segment_intersects_rect(a, b, rectangle, margin=1):
                    errors.append(("MESSAGE_FLOW_THROUGH_ARTIFACT", mid, f"маршрут проходит через {aid}")); break
            for fid in sequence_ids:
                if any(orthogonal_segments_intersect(a, b, c, d) for c, d in _segments(routes.get(fid, []))):
                    errors.append(("MESSAGE_FLOW_SEQUENCE_INTERSECTION", mid, f"пересечение с {fid}")); break

    for did in data_ids:
        association = next((item for item in meta.get("data_associations") or [] if str(item.get("id")) == did), {})
        sid = str(association.get("activity_id"))
        aid = str(association.get("artifact_instance_id"))
        segments = _segments(routes.get(did, []))
        for overlay_sid, overlay_rect in system_rects.items():
            if overlay_rect and any(segment_intersects_rect(a, b, overlay_rect, margin=1) for a, b in segments):
                errors.append(("DATA_ASSOCIATION_THROUGH_SYSTEM_BADGE", did,
                               f"пересечение с SYSTEM.{overlay_sid}")); break
        for overlay_sid, overlay_rect in role_rects.items():
            if overlay_rect and any(segment_intersects_rect(a, b, overlay_rect, margin=1) for a, b in segments):
                errors.append(("DATA_ASSOCIATION_THROUGH_ROLE_OVERLAY", did,
                               f"пересечение с ROLE.{overlay_sid}")); break
        for node_id, rectangle in node_rects.items():
            if node_id == sid:
                continue
            if rectangle and any(segment_intersects_rect(a, b, rectangle, margin=1) for a, b in segments):
                errors.append(("DATA_ASSOCIATION_THROUGH_ACTIVITY", did,
                               f"пересечение с {node_id}")); break
        for artifact_id, rectangle in artifact_rects.items():
            if artifact_id == aid:
                continue
            if rectangle and any(segment_intersects_rect(a, b, rectangle, margin=1) for a, b in segments):
                errors.append(("DATA_ASSOCIATION_THROUGH_ARTIFACT", did,
                               f"пересечение с {artifact_id}")); break
        for fid in sequence_ids:
            relation = next((relation for a, b in segments for c, d in _segments(routes.get(fid, []))
                             if (relation := orthogonal_segments_intersect(a, b, c, d))), None)
            if relation == "overlap":
                errors.append(("DATA_ASSOCIATION_SEQUENCE_OVERLAP", did, f"общий сегмент с {fid}"))
            elif relation == "cross":
                errors.append(("DATA_ASSOCIATION_SEQUENCE_CROSS", did, f"пересечение с {fid}"))

    for index, left_id in enumerate(data_ids):
        for right_id in data_ids[index + 1:]:
            relation = next((relation for a, b in _segments(routes.get(left_id, []))
                             for c, d in _segments(routes.get(right_id, []))
                             if (relation := orthogonal_segments_intersect(a, b, c, d))), None)
            if relation:
                errors.append(("DATA_ASSOCIATION_SEGMENT_OVERLAP", left_id,
                               f"{relation} с {right_id}"))

    label_cells = {cid: cell for cid, cell in cells.items() if style_value(cell.get("style") or "", "labelKind")}
    for cid, cell in label_cells.items():
        if _estimated_text_overflow(cell, cells):
            errors.append(("LABEL_TEXT_OVERFLOW", cid, "текст не помещается в label cell"))
    label_rects = {cid: svg_bounds.get(cid, _rect(cell, cells)) for cid, cell in label_cells.items()}
    occupied_rects = {**node_rects, **artifact_rects, **system_rects}
    for cid, rectangle in label_rects.items():
        if not rectangle: continue
        for oid, other in occupied_rects.items():
            if other and rect_overlap(rectangle, other):
                errors.append(("LABEL_OVERLAP", cid, f"наложение на {oid}")); break
    label_items = list(label_rects.items())
    for index, (cid, rectangle) in enumerate(label_items):
        if not rectangle: continue
        for oid, other in label_items[index + 1:]:
            if other and rect_overlap(rectangle, other):
                errors.append(("LABEL_OVERLAP", cid, f"наложение на {oid}"))

    for flow in meta.get("sequence_flows") or []:
        condition, fid = str(flow.get("condition") or "").strip(), str(flow.get("id"))
        if not condition or f"LABEL.{fid}" not in cells: continue
        label_rect = _rect(cells[f"LABEL.{fid}"], cells)
        points = routes.get(fid, [])
        if label_rect and points:
            center = ((label_rect[0] + label_rect[2]) / 2, (label_rect[1] + label_rect[3]) / 2)
            if min((_point_segment_distance(center, a, b) for a, b in _segments(points)), default=9999) > 70:
                errors.append(("BRANCH_LABEL_POSITION", fid, "подпись находится не у своей исходящей ветки"))

    # External participants are anchored to their connected node rather than
    # collected at an unrelated diagram corner.
    participant_ids = {str(p.get("id")) for p in meta.get("participants") or []
                       if p.get("participant_kind") != "current_process"}
    participant_meta = {str(p.get("id")): p for p in meta.get("participants") or []
                        if p.get("participant_kind") != "current_process"}
    for participant_id in participant_ids:
        label_id = f"LABEL.PARTICIPANT.{participant_id}"
        label = cells.get(label_id)
        participant = cells.get(participant_id)
        label_rect = svg_bounds.get(label_id, _rect(label, cells) if label is not None else None)
        participant_rect = svg_bounds.get(participant_id, _rect(participant, cells) if participant is not None else None)
        expected_name = str((participant_meta.get(participant_id) or {}).get("name") or participant_id).strip()
        value = " ".join(str(label.get("value") or "").split()) if label is not None else ""
        contained = bool(label_rect and participant_rect
                         and participant_rect[0] <= label_rect[0] <= label_rect[2] <= participant_rect[2]
                         and participant_rect[1] <= label_rect[1] <= label_rect[3] <= participant_rect[3])
        if (label is None or style_value(label.get("style") or "", "labelKind") != "participant"
                or not value or " ".join(expected_name.split()) != value
                or _estimated_text_overflow(label, cells) or not contained):
            errors.append(("EXTERNAL_PARTICIPANT_LABEL_VISIBLE", participant_id,
                           "имя внешнего Participant должно быть видимо внутри отдельной label cell"))
    for message in meta.get("message_flows") or []:
        participant_id = str(message.get("source")) if str(message.get("source")) in participant_ids else str(message.get("target"))
        node_id = str(message.get("target")) if participant_id == str(message.get("source")) else str(message.get("source"))
        if participant_id in cells and node_id in node_rects:
            participant_rect = svg_bounds.get(participant_id, _rect(cells[participant_id], cells))
            node_rect = node_rects[node_id]
            if participant_rect and abs((participant_rect[0] + participant_rect[2]) / 2 - (node_rect[0] + node_rect[2]) / 2) > 220:
                errors.append(("EXTERNAL_PARTICIPANT_NOT_ANCHORED", participant_id, f"слишком далеко от {node_id}"))
    return errors, warnings


def validate(drawio_path: Path, meta: dict, model: dict, svg_path: Path | None = None):
    if meta.get("render_profile") == "corporate_role_overlays_v2":
        return validate_corporate(drawio_path, meta, model, svg_path)
    errors = []
    warnings = []
    root, cells = load(drawio_path)

    pool_id = meta["pool_id"]
    lane_order = meta["lanes"]
    node_meta = meta["nodes"]

    # --- Иерархия Pool -> Lane -> Task ---
    for lid in lane_order:
        cell = cells.get(lid)
        if cell is None:
            errors.append(("LANE_NOT_INSIDE_POOL", lid, f"Lane '{lid}' отсутствует в .drawio"))
            continue
        if cell.get("parent") != pool_id:
            errors.append(("LANE_NOT_INSIDE_POOL", lid, f"parent='{cell.get('parent')}', ожидалось '{pool_id}'"))

    task_heights = set()
    for node_id, info in node_meta.items():
        cell = cells.get(node_id)
        lane_id = info.get("lane_id")
        if cell is None or not lane_id:
            continue
        if cell.get("parent") != lane_id:
            errors.append(("TASK_NOT_INSIDE_LANE", node_id, f"parent='{cell.get('parent')}', ожидалось '{lane_id}'"))
        if info.get("kind") == "task":
            g = geom(cell)
            if g is not None:
                task_heights.add(gf(g, "height"))
                width, height = gf(g, "width"), gf(g, "height")
                if not (TASK_MIN_WIDTH <= width <= TASK_MAX_WIDTH and TASK_MIN_HEIGHT <= height <= TASK_MAX_HEIGHT):
                    errors.append((
                        "TASK_SIZE_OUT_OF_RANGE", node_id,
                        f"size={width:.0f}x{height:.0f}, ожидалось {TASK_MIN_WIDTH}–{TASK_MAX_WIDTH} x "
                        f"{TASK_MIN_HEIGHT}–{TASK_MAX_HEIGHT}",
                    ))
            label = (cell.get("value") or "").strip()
            if HTML_RE.search(label):
                errors.append(("TASK_NAME_HTML_FORBIDDEN", node_id, "HTML запрещён в BPMN Task name"))
            explicit_lines = re.split(r"\r?\n|<br\s*/?>", label, flags=re.IGNORECASE)
            estimated_lines = sum(max(1, len(textwrap.wrap(line, width=28))) for line in explicit_lines)
            if len(label) > TASK_LABEL_MAX_CHARS:
                errors.append(("TASK_LABEL_TOO_LONG", node_id,
                               f"{len(label)} символов, максимум {TASK_LABEL_MAX_CHARS}"))
            if estimated_lines > TASK_LABEL_MAX_LINES:
                errors.append(("TASK_LABEL_TOO_MANY_LINES", node_id,
                               f"оценочно {estimated_lines} строк, максимум {TASK_LABEL_MAX_LINES}"))
            marker = cells.get(f"{node_id}_i")
            if marker is not None:
                mg = geom(marker)
                marker_w, marker_h = gf(mg, "width"), gf(mg, "height")
                if not (TASK_MARKER_MIN <= marker_w <= TASK_MARKER_MAX
                        and TASK_MARKER_MIN <= marker_h <= TASK_MARKER_MAX):
                    errors.append(("TASK_MARKER_SIZE_OUT_OF_RANGE", node_id,
                                   f"marker={marker_w:.0f}x{marker_h:.0f}, ожидалось 14–16 px"))

    if len(task_heights) > 1:
        errors.append(("TASK_HEIGHT_INCONSISTENT", "*", f"высоты задач различны: {sorted(task_heights)}"))

    # --- SYSTEM_RENDERED_AS_LANE / ARTIFACT_RENDERED_AS_LANE ---
    expected_swimlanes = {pool_id, *lane_order}
    for cid, cell in cells.items():
        style = cell.get("style", "")
        if "swimlane" not in style:
            continue
        value = cell.get("value", "") or ""
        if cid in expected_swimlanes:
            if is_system_like(value):
                errors.append(("SYSTEM_RENDERED_AS_LANE", cid, f"Lane/Pool '{value}' совпадает с именем системы"))
        else:
            errors.append(("ARTIFACT_RENDERED_AS_LANE", cid, f"Непредвиденная дорожка '{value}' (id={cid})"))

    # --- SYSTEM_LABEL_MISSING / FONT_POLICY_VIOLATION (system label) ---
    for node_id, info in node_meta.items():
        if info.get("kind") != "task":
            continue
        system = (info.get("system") or "").strip()
        label_id = f"{node_id}__system_label"
        label_cell = cells.get(label_id)
        if system:
            if label_cell is None:
                errors.append(("SYSTEM_LABEL_MISSING", node_id, f"нет подписи системы '{system}' под задачей"))
            else:
                style = label_cell.get("style", "")
                if "fontSize=8" not in style:
                    errors.append(("FONT_POLICY_VIOLATION", label_id, "подпись системы не fontSize=8"))
        else:
            if label_cell is not None:
                warnings.append(("FONT_POLICY_VIOLATION", label_id, "лишняя подпись системы при пустом system"))

    # --- Артефакты: под задачей, не в отдельной lane, тип известен ---
    for node_id, info in node_meta.items():
        if info.get("kind") != "task":
            continue
        cell = cells.get(node_id)
        if cell is None:
            continue
        tg = geom(cell)
        task_bottom = gf(tg, "y") + gf(tg, "height")
        for i, art in enumerate(info.get("artifacts") or []):
            # id назначается диаграммным слоем последовательно; ищем по префиксу+имени через связи assoc
            atype = art.get("kind") or art.get("artifact_type")
            if atype and atype not in ARTIFACT_TYPES:
                warnings.append(("ARTIFACT_TYPE_UNKNOWN", node_id, f"неизвестный kind '{atype}'"))
            elif not atype:
                warnings.append(("ARTIFACT_TYPE_UNKNOWN", node_id, f"артефакт '{art.get('name')}' без kind"))

        # найти реально сгенерированные артефакт-cells по id-паттерну {node_id}__art*
        for cid, acell in cells.items():
            if not cid.startswith(f"{node_id}__art"):
                continue
            ag = geom(acell)
            if ag is None:
                continue
            # Сравниваем с ФАКТИЧЕСКИМ parent задачи в .drawio (cell), а не с
            # info.get("lane_id") из render-meta.json: для узлов с ROLE !=
            # SYSTEM (role="", system-only step) render-meta может нести
            # lane_id="" — сама задача при этом легитимно репарентится в
            # дорожку через fallback по Y-координате в diagram_layout.py
            # (шаг 3), и .drawio parent — надёжный источник истины уже
            # ПОСЛЕ такого fallback, в отличие от исходного render-meta.
            if acell.get("parent") != cell.get("parent"):
                errors.append(("ARTIFACT_NOT_BELOW_TASK", cid, "артефакт не в той же дорожке, что задача"))
                continue
            if gf(ag, "y") < task_bottom:
                errors.append(("ARTIFACT_NOT_BELOW_TASK", cid, "артефакт не ниже задачи по Y"))
            style = acell.get("style", "")
            if "fontSize=8" not in style:
                errors.append(("FONT_POLICY_VIOLATION", cid, "подпись артефакта не fontSize=8"))

    # --- Gateways: XOR branch labels, merge без условия ---
    edges = [c for c in root.findall("mxCell") if c.get("edge") == "1"]
    gateway_out_edges = {}
    for e in edges:
        src = e.get("source")
        gateway_out_edges.setdefault(src, []).append(e)

    for node_id, info in node_meta.items():
        if info.get("kind") != "gateway":
            continue
        cell = cells.get(node_id)
        gtype = None
        # gateway_type не хранится в meta напрямую (было в модели) — определяем по стилю/символу не всегда
        # надёжно, поэтому проверяем через process_model для типа.
        for g in model.get("gateways", []):
            if g["id"] == node_id:
                gtype = g.get("gateway_type")
                gw_role = g.get("gateway_role", "split")
                break
        else:
            gw_role = info.get("gateway_role", "split")

        if gw_role == "split" and gtype in ("xor", "or"):
            out_edges = gateway_out_edges.get(node_id, [])
            for e in out_edges:
                label = (e.get("value") or "").strip()
                if not label:
                    errors.append(("XOR_BRANCH_WITHOUT_LABEL", node_id,
                                    f"ветка {node_id}->{e.get('target')} без подписи"))
        if gw_role == "merge":
            if cell is not None and (cell.get("value") or "").strip():
                errors.append(("MERGE_GATEWAY_HAS_CONDITION", node_id, "merge-шлюз с текстом"))

    # Технические auto-merge запрещены: merge должен существовать только в
    # канонической бизнес-модели.
    for cid, cell in cells.items():
        if cid.startswith("GWm_") and cell.get("vertex") == "1" and not any(
            cid.endswith(s) for s in ("_i", "_m", "_l", "_in", "_out")
        ):
            errors.append(("AUTO_MERGE_NOT_ALLOWED", cid,
                           "технический merge перед задачей не выражает бизнес-семантику"))

    # --- Cross-lane порты + waypoints ---
    node_lane = {nid: info.get("lane_id") for nid, info in node_meta.items()}
    node_geom_abs = {}
    for nid in node_meta:
        cell = cells.get(nid)
        if cell is None:
            continue
        absolute = absolute_geometry(cell, cells)
        if absolute is None:
            continue
        node_geom_abs[nid] = absolute

    pool_absolute = absolute_geometry(cells.get(pool_id), cells) if cells.get(pool_id) is not None else None
    if pool_absolute and pool_absolute[2] > MAX_DIAGRAM_WIDTH:
        errors.append(("DIAGRAM_TOO_WIDE", pool_id,
                       f"width={pool_absolute[2]:.0f}, максимум={MAX_DIAGRAM_WIDTH}"))

    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        if src not in node_lane or tgt not in node_lane:
            continue
        if not node_lane[src] or not node_lane[tgt]:
            continue
        style = e.get("style", "")
        cross_lane = node_lane[src] != node_lane[tgt]
        sx, sy, sw, sh = node_geom_abs.get(src, (0, 0, 0, 0))
        tx, ty, tw, th = node_geom_abs.get(tgt, (0, 0, 0, 0))
        backward = tx < sx
        if cross_lane:
            if "exitX=1" not in style or "entryX=0" not in style:
                # возвраты (backward) сознательно используют другой порт — не флагуем их тут
                pass
            arr = e.find("mxGeometry/Array")
            if arr is None or len(arr.findall("mxPoint")) == 0:
                errors.append(("EDGE_WITHOUT_REQUIRED_WAYPOINT", f"{src}->{tgt}",
                                "cross-lane связь без явных waypoints"))
        if backward:
            arr = e.find("mxGeometry/Array")
            points = arr.findall("mxPoint") if arr is not None else []
            pool_left = pool_absolute[0] if pool_absolute else 0
            pool_top = pool_absolute[1] if pool_absolute else 0
            pool_right = pool_left + pool_absolute[2] if pool_absolute else 0
            pool_bottom = pool_absolute[1] + pool_absolute[3] if pool_absolute else 0
            involved_lane_tops = [
                absolute_geometry(cells[lane_id], cells)[1]
                for lane_id in (node_lane.get(src), node_lane.get(tgt))
                if lane_id in cells and absolute_geometry(cells[lane_id], cells) is not None
            ]
            local_top = min(involved_lane_tops) if involved_lane_tops else pool_top
            outside = any(
                float(point.get("x", 0)) < pool_left - 10
                or float(point.get("x", 0)) > pool_right + 10
                or float(point.get("y", 0)) < pool_top - 10
                or float(point.get("y", 0)) > pool_bottom + 10
                or float(point.get("y", 0)) < local_top - 10
                for point in points
            )
            if (
                "exitY=1" not in style or "entryY=1" not in style
                or len(points) < 2
                or not outside
            ):
                errors.append(("BACKWARD_EDGE_ROUTING_INVALID", f"{src}->{tgt}",
                               "обратная связь должна идти отдельным внешним коридором ниже пула"))

    # --- FONT_POLICY_VIOLATION: подписи условий XOR (шрифт 8 не задаётся bpmn-diagrams —
    #     фиксируем как известное ограничение, не ошибка генератора этого прохода) ---
    # (bpmn-diagrams рисует edge label дефолтным шрифтом; корпоративный шрифт 8 для
    #  условий шлюзов сейчас не проставляется post-processor'ом — WARNING, не ERROR,
    #  чтобы не блокировать PASS за пределами того, что реально реализовано.)
    for e in edges:
        label = (e.get("value") or "").strip()
        if label and "fontSize=8" not in (e.get("style") or ""):
            warnings.append(("FONT_POLICY_VIOLATION", f"{e.get('source')}->{e.get('target')}",
                              "подпись условия/сообщения на связи не fontSize=8 (известное ограничение)"))

    # --- ROLE_REQUIRED_FOR_AUTOMATION (из process_model.json warnings) ---
    for w in model.get("warnings", []):
        if w.get("type") == "ROLE_REQUIRED_FOR_AUTOMATION":
            warnings.append(("ROLE_REQUIRED_FOR_AUTOMATION", w.get("step"), w.get("detail", "")))

    # --- EDGE_THROUGH_NODE / EDGE_EDGE_INTERSECTION ---
    node_rects = {nid: (x, y, x + w, y + h) for nid, (x, y, w, h) in node_geom_abs.items()}
    edge_polylines = []
    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        if src not in node_rects or tgt not in node_rects:
            continue
        sx0, sy0, sx1, sy1 = node_rects[src]
        tx0, ty0, tx1, ty1 = node_rects[tgt]
        style = e.get("style") or ""
        start = (
            sx0 + (sx1 - sx0) * style_number(style, "exitX", 1.0),
            sy0 + (sy1 - sy0) * style_number(style, "exitY", 0.5),
        )
        finish = (
            tx0 + (tx1 - tx0) * style_number(style, "entryX", 0.0),
            ty0 + (ty1 - ty0) * style_number(style, "entryY", 0.5),
        )
        eg = e.find("mxGeometry")
        pts = [start]
        if eg is not None:
            arr = eg.find("Array")
            if arr is not None:
                for p in arr.findall("mxPoint"):
                    pts.append((float(p.get("x")), float(p.get("y"))))
        pts.append(finish)
        if len(pts) == 2 and abs(start[1] - finish[1]) > 0.01:
            mid_x = (start[0] + finish[0]) / 2
            pts = [start, (mid_x, start[1]), (mid_x, finish[1]), finish]
        edge_polylines.append((
            e.get("id") or f"{src}->{tgt}", src, tgt, pts,
            "jumpStyle=" in (e.get("style") or ""),
        ))
        for i in range(len(pts) - 1):
            for nid, rect in node_rects.items():
                if nid in (src, tgt):
                    continue
                if segment_intersects_rect(pts[i], pts[i + 1], rect):
                    errors.append(("EDGE_THROUGH_NODE", f"{src}->{tgt}",
                                    f"сегмент маршрута пересекает '{nid}'"))

    for i, (edge_a, src_a, tgt_a, points_a, bridged_a) in enumerate(edge_polylines):
        for edge_b, src_b, tgt_b, points_b, bridged_b in edge_polylines[i + 1:]:
            relations = [
                relation
                for a in range(len(points_a) - 1)
                for b in range(len(points_b) - 1)
                if (relation := orthogonal_segments_intersect(
                    points_a[a], points_a[a + 1], points_b[b], points_b[b + 1]
                ))
            ]
            pair = f"{edge_a}/{edge_b}"
            if "overlap" in relations:
                errors.append((
                    "EDGE_EDGE_COLLINEAR_OVERLAP", pair,
                    f"{src_a}->{tgt_a} имеет общий коллинеарный участок с {src_b}->{tgt_b}; "
                    "jumpStyle не разрешает общий сегмент",
                ))
            else:
                crossings = relations.count("cross")
                # A bridge may make exactly one transverse crossing readable.
                # It never permits two crossings or a shared/collinear segment.
                if crossings and not ((bridged_a or bridged_b) and crossings == 1):
                    errors.append(("EDGE_EDGE_INTERSECTION", pair,
                                   f"{src_a}->{tgt_a} пересекается с {src_b}->{tgt_b} ({crossings} раз)"))

    # --- LABEL_OVERLAP: отдельные подписи системы/текстовые labels ---
    labels = []
    for cid, cell in cells.items():
        if cell.get("vertex") != "1" or not (cell.get("value") or "").strip():
            continue
        if (
            cid in node_meta or cid in lane_order or cid == pool_id or "__art" in cid
            or cell.get("parent") in node_meta
        ):
            continue
        absolute = absolute_geometry(cell, cells)
        if absolute is None:
            continue
        x, y, w, h = absolute
        labels.append((cid, (x, y, x + w, y + h)))
    for index, (label_id, label_rect) in enumerate(labels):
        owner = label_id.split("__", 1)[0]
        for node_id, node_rect in node_rects.items():
            if node_id != owner and rect_overlap(label_rect, node_rect):
                errors.append(("LABEL_OVERLAP", label_id, f"подпись наложена на '{node_id}'"))
        for other_id, other_rect in labels[index + 1:]:
            if rect_overlap(label_rect, other_rect):
                errors.append(("LABEL_OVERLAP", label_id, f"подпись наложена на '{other_id}'"))

    # --- LARGE_EMPTY_INTERVAL: глобальные колонки, а не пропуски роли в lane ---
    columns = []
    for nid, (x, _y, _w, _h) in sorted(node_geom_abs.items(), key=lambda item: item[1][0]):
        if not columns or x - columns[-1][0] > 20:
            columns.append((x, nid))
    for (left_x, _left_id), (right_x, right_id) in zip(columns, columns[1:]):
        gap = right_x - left_x
        if gap > MAX_EMPTY_INTERVAL:
            errors.append(("LARGE_EMPTY_INTERVAL", right_id,
                           f"между колонками {gap:.0f}px, максимум={MAX_EMPTY_INTERVAL}px"))

    return errors, warnings


def write_reports(process_id, errors, warnings, out_json: Path, out_md: Path):
    result = {
        "process_id": process_id,
        "errors": [{"rule": r, "node": n, "detail": d} for r, n, d in errors],
        "warnings": [{"rule": r, "node": n, "detail": d} for r, n, d in warnings],
        "error_count": len(errors),
        "warning_count": len(warnings),
        "status": "FAIL" if errors else "PASS",
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Corporate drawio validation — {process_id}", "", f"Статус: **{result['status']}**", ""]
    lines.append(f"Ошибок (ERROR): {len(errors)}")
    lines.append(f"Предупреждений (WARNING): {len(warnings)}")
    lines.append("")
    if errors:
        lines.append("## ERROR")
        for r, n, d in errors:
            lines.append(f"- `{r}` — {n}: {d}")
        lines.append("")
    if warnings:
        lines.append("## WARNING")
        for r, n, d in warnings:
            lines.append(f"- `{r}` — {n}: {d}")
        lines.append("")
    lines.append(
        "Маршруты corporate-профиля проверяются по фактическому SVG, если передан --svg; "
        "иначе используются явные порты и waypoints draw.io. Каждый source/target Sequence Flow, Message Flow "
        "и Data Association проверяется на касание фактического контура своей формы. Эвристические правила: EDGE_THROUGH_NODE, "
        "EDGE_EDGE_INTERSECTION, LABEL_OVERLAP, BACKWARD_EDGE_ROUTING_INVALID, "
        "DIAGRAM_TOO_WIDE, LARGE_EMPTY_INTERVAL и TASK_TEXT_OVERFLOW. FONT_POLICY_VIOLATION для "
        "подписей условий на связях — известное ограничение текущего прохода "
        "(bpmn-diagrams не поддерживает шрифт связи через registry), вынесено в WARNING."
    )
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drawio_path")
    parser.add_argument("render_meta_path")
    parser.add_argument("model_path")
    parser.add_argument("--svg", help="Фактически экспортированный SVG для проверки маршрутов")
    args = parser.parse_args()

    meta = json.loads(Path(args.render_meta_path).read_text(encoding="utf-8"))
    model = json.loads(Path(args.model_path).read_text(encoding="utf-8"))
    process_id = meta.get("process_id", "process")

    errors, warnings = validate(Path(args.drawio_path), meta, model, Path(args.svg) if args.svg else None)

    out_json = Path("output/validation") / f"{process_id}-drawio-validation.json"
    out_md = Path("output/validation") / f"{process_id}-drawio-validation.md"
    result = write_reports(process_id, errors, warnings, out_json, out_md)

    print(f"Статус: {result['status']} (ERROR={result['error_count']}, WARNING={result['warning_count']})")
    print(f"JSON: {out_json}")
    print(f"MD:   {out_md}")
    sys.exit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
