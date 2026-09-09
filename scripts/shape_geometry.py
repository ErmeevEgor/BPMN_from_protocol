#!/usr/bin/env python3
"""Shared shape-aware connector geometry for rendering and validation."""
from __future__ import annotations

from math import sqrt


RECTANGLE = "rectangle"
DIAMOND = "diamond"
ELLIPSE = "ellipse"
SUPPORTED_SHAPES = {RECTANGLE, DIAMOND, ELLIPSE}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def perimeter_port(shape: str, side: str, position: float = 0.5) -> tuple[float, float]:
    """Return normalized coordinates on a visible shape perimeter.

    ``position`` advances along the requested bounding-box side. For a
    diamond the returned point satisfies
    ``abs(x - .5) + abs(y - .5) == .5``. For an ellipse it satisfies the
    unit-ellipse equation. Data Objects deliberately use the safe rectangle
    contract supported by draw.io's note shape.
    """
    position = clamp(position)
    shape = shape if shape in SUPPORTED_SHAPES else RECTANGLE
    if shape == RECTANGLE:
        return {
            "left": (0.0, position), "right": (1.0, position),
            "top": (position, 0.0), "bottom": (position, 1.0),
        }[side]
    if shape == DIAMOND:
        offset = abs(position - 0.5)
        return {
            "left": (offset, position), "right": (1.0 - offset, position),
            "top": (position, offset), "bottom": (position, 1.0 - offset),
        }[side]
    # Ellipse: solve the normalized ellipse equation around (.5, .5).
    radial = sqrt(max(0.0, 0.25 - (position - 0.5) ** 2))
    return {
        "left": (0.5 - radial, position), "right": (0.5 + radial, position),
        "top": (position, 0.5 - radial), "bottom": (position, 0.5 + radial),
    }[side]


def absolute_port(rect: tuple[float, float, float, float], port: tuple[float, float]) -> tuple[float, float]:
    x0, y0, x1, y1 = rect
    return x0 + (x1 - x0) * port[0], y0 + (y1 - y0) * port[1]


def point_on_perimeter(shape: str, point: tuple[float, float],
                       rect: tuple[float, float, float, float], tolerance: float = 1.5) -> bool:
    """Return whether an absolute point touches the visible shape contour."""
    x0, y0, x1, y1 = rect
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return False
    x, y = point
    if not (x0 - tolerance <= x <= x1 + tolerance and y0 - tolerance <= y <= y1 + tolerance):
        return False
    shape = shape if shape in SUPPORTED_SHAPES else RECTANGLE
    if shape == RECTANGLE:
        return min(abs(x - x0), abs(x - x1), abs(y - y0), abs(y - y1)) <= tolerance
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if shape == DIAMOND:
        normalized = abs(x - cx) / (width / 2) + abs(y - cy) / (height / 2)
        return abs(normalized - 1.0) * min(width, height) / 2 <= tolerance
    normalized_radius = sqrt(((x - cx) / (width / 2)) ** 2 + ((y - cy) / (height / 2)) ** 2)
    return abs(normalized_radius - 1.0) * min(width, height) / 2 <= tolerance


def shape_from_style(style: str) -> str:
    """Classify the actual draw.io Flow Node style."""
    style = style or ""
    if "mxgraph.bpmn.gateway" in style or "gatewayType=" in style or "rhombus" in style:
        return DIAMOND
    if "eventKind=" in style or "ellipse" in style or "mxgraph.bpmn.event" in style:
        return ELLIPSE
    return RECTANGLE
