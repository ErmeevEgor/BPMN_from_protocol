#!/usr/bin/env python3
"""Read final, uncompressed draw.io geometry for BPMN-DI export.

The final draw.io document is the single visual layout source. Connector ports
are deliberately read from the *edge* style; vertex styles never define an
edge endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from shape_geometry import absolute_port, shape_from_style


@dataclass(frozen=True)
class Bounds:
    x: float
    y: float
    width: float
    height: float

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.x + self.width, self.y + self.height


def style_value(style: str, key: str) -> str | None:
    match = re.search(rf"(?:^|;){re.escape(key)}=([^;]+)(?:;|$)", style or "")
    return match.group(1) if match else None


def style_number(style: str, key: str, default: float) -> float:
    value = style_value(style, key)
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def load_cells(path: Path) -> dict[str, ET.Element]:
    document = ET.parse(path).getroot()
    root = document if document.tag == "root" else document.find(".//root")
    if root is None:
        raise ValueError(f"draw.io root not found: {path}")
    return {cell.get("id"): cell for cell in root.findall("mxCell") if cell.get("id")}


def geometry(cell: ET.Element | None) -> ET.Element | None:
    return cell.find("mxGeometry") if cell is not None else None


def number(element: ET.Element | None, attribute: str, default: float = 0.0) -> float:
    try:
        return float(element.get(attribute, default)) if element is not None else default
    except (TypeError, ValueError):
        return default


def absolute_bounds(cell: ET.Element | None, cells: dict[str, ET.Element]) -> Bounds | None:
    geom = geometry(cell)
    if geom is None or cell is None:
        return None
    x, y = number(geom, "x"), number(geom, "y")
    parent = cell.get("parent")
    seen: set[str] = set()
    while parent and parent not in {"0", "1"} and parent not in seen:
        seen.add(parent)
        parent_cell = cells.get(parent)
        parent_geom = geometry(parent_cell)
        x += number(parent_geom, "x")
        y += number(parent_geom, "y")
        parent = parent_cell.get("parent") if parent_cell is not None else None
    return Bounds(x, y, number(geom, "width"), number(geom, "height"))


def endpoint_port(edge: ET.Element, endpoint: str) -> tuple[float, float]:
    """Return normalized endpoint from this edge's own style."""
    style = edge.get("style") or ""
    if endpoint == "source":
        return style_number(style, "exitX", .5), style_number(style, "exitY", .5)
    return style_number(style, "entryX", .5), style_number(style, "entryY", .5)


def endpoint_point(edge: ET.Element, endpoint: str,
                   cells: dict[str, ET.Element]) -> tuple[float, float] | None:
    reference = edge.get(endpoint)
    bounds = absolute_bounds(cells.get(reference), cells)
    if bounds is None:
        return None
    return absolute_port(bounds.rect, endpoint_port(edge, endpoint))


def edge_points(edge: ET.Element, cells: dict[str, ET.Element]) -> list[tuple[float, float]]:
    """Return source port, explicit waypoints, and target port in root space."""
    start = endpoint_point(edge, "source", cells)
    end = endpoint_point(edge, "target", cells)
    if start is None or end is None:
        return []
    points = [start]
    edge_geom = geometry(edge)
    array = edge_geom.find("Array[@as='points']") if edge_geom is not None else None
    offset_x = offset_y = 0.0
    parent_bounds = absolute_bounds(cells.get(edge.get("parent")), cells)
    if parent_bounds is not None:
        offset_x, offset_y = parent_bounds.x, parent_bounds.y
    if array is not None:
        points.extend((number(point, "x") + offset_x, number(point, "y") + offset_y)
                      for point in array.findall("mxPoint"))
    points.append(end)
    compact: list[tuple[float, float]] = []
    for point in points:
        if not compact or abs(point[0] - compact[-1][0]) > .01 or abs(point[1] - compact[-1][1]) > .01:
            compact.append(point)
    return compact


def cell_shape(cell: ET.Element | None) -> str:
    return shape_from_style(cell.get("style") or "") if cell is not None else "rectangle"


def label_bounds(cells: dict[str, ET.Element], cell_id: str) -> Bounds | None:
    return absolute_bounds(cells.get(f"LABEL.{cell_id}"), cells)
