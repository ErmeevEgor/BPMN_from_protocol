#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
drawio — эмиттер draw.io (mxGraph) из того же BPMN-граф-реестра, что и BPMN 2.0.

[Старт цели разморозки трека process-viz: редактируемые схемы процессов в формате draw.io.]

Переиспользует граф и раскладку bpmn_graph.build_laid_out() — валидация методологии слоя A
та же, что у BPMN-пути, — и сериализует модель в mxGraph XML (diagrams.net / app.diagrams.net).
Элементы — абсолютными координатами в слое root/1 (раскладка уже посчитана); пулы/дорожки —
фоновыми swimlane; семантические цвета берём из bpmn_graph.TYPE_COLORS (PRB- → маджента).

Граница v1 (честно):
  • рёбра несут вычисленные промежуточные точки, но draw.io сам выбирает точку крепления к
    периметру узла — мелкие отклонения правятся в редакторе мышью;
  • дорожки положены абсолютными координатами (не вложены в пул как parent) — перетаскивание
    пула не двигает дорожки; это упрощение v1;
  • только граф-режим (frontmatter `process-graph: true`).
Дальше: вложенные пулы/дорожки (relative-геометрия), BPMN-стенсилы draw.io (иконки задач/событий),
фиксированные точки крепления рёбер (exitX/entryX) для 1:1 с раскладкой.
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import bpmn_graph  # noqa: E402
from bpmn_graph import TYPE_COLORS, ACCENT, NEUTRAL  # noqa: E402

# ── стили рёбер/контейнеров/объектов (mxGraph) ───────────────────────────────
# Стрелки удвоенной толщины (strokeWidth=2 vs дефолтные 1).
SEQ_STYLE = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;"
             "strokeColor=#595959;strokeWidth=2;")
MSG_STYLE = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;endArrow=open;"
             "startArrow=oval;startFill=0;strokeColor=#595959;strokeWidth=2;")
ASSOC_STYLE = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;dashed=1;endArrow=open;"
               "strokeColor=#9A9A9A;strokeWidth=2;")
POOL_STYLE = ("swimlane;html=1;horizontal=0;startSize=22;fillColor=none;strokeColor=#595959;"
              "fontColor=#595959;fontStyle=1;")
LANE_STYLE = "swimlane;html=1;horizontal=0;startSize=22;fillColor=none;strokeColor=#B0B0B0;fontColor=#595959;"
# дорожка системных ролей (Тип дорожки: system) — серая полоса под цвет сервисной задачи
SYSLANE_STYLE = ("swimlane;html=1;horizontal=0;startSize=22;fillColor=#EEF1F3;strokeColor=#B0B0B0;"
                 "fontColor=#595959;")
# теневое ИТ (Excel/Word/почта/мессенджеры) — тёплый янтарный оттенок объекта (отличие от системного)
SHADOW_FILL = "#FBF3E2"
ARTLANE_STYLE = ("swimlane;html=1;horizontal=0;startSize=22;fillColor=#F7F7F7;strokeColor=#B0B0B0;"
                 "fontColor=#595959;fontStyle=2;")
# у объектов-артефактов подпись — ПОД фигурой
DATAOBJ_STYLE = ("shape=note;whiteSpace=wrap;html=1;size=14;fillColor=#FFFFFF;strokeColor=#595959;"
                 "fontColor=#1A1A1A;verticalLabelPosition=bottom;verticalAlign=top;")
DATASTORE_STYLE = ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;"
                   "fillColor=#FFFFFF;strokeColor=#595959;fontColor=#1A1A1A;"
                   "verticalLabelPosition=bottom;verticalAlign=top;")

# Подпись ПОД фигурой (события/шлюзы — не поверх элемента).
LBL_BELOW = "verticalLabelPosition=bottom;verticalAlign=top;align=center;"

# trigger события → symbol стенсила mxgraph.bpmn.shape (подтверждено эмпирически рендером)
EV_SYMBOL = {"none": "general", "message": "message", "timer": "timer", "signal": "signal",
             "error": "error", "escalation": "escalation", "terminate": "terminate",
             "conditional": "conditional", "link": "link", "compensation": "compensation"}
# tag события → outline (тонкий старт / толстый конец / двойное кольцо промежуточного)
EV_OUTLINE = {"startEvent": "standard", "endEvent": "end",
              "intermediateCatchEvent": "catching", "intermediateThrowEvent": "throwing",
              "boundaryEvent": "eventInt"}
# подтип шлюза → глиф-маркер (gateway2 symbol в этой версии draw.io не рисуется — рисуем глиф сами)
GW_GLYPH = {"xor": "✕", "and": "✚", "or": "◯", "complex": "✳", "event": "⬠", "": "✕"}
# tag задачи → СТИЛЬ иконки типа (дочерний элемент в левом-верхнем углу).
# send/receive — чёткий конверт shape=message (залитый = отправка, контурный = приём);
# стенсилы bpmn.send_task/receive_task в draw.io рисуются неразборчивым квадратом.
TASK_ICON = {
    "userTask": "shape=mxgraph.bpmn.user_task;html=1;",
    "serviceTask": "shape=mxgraph.bpmn.service_task;html=1;",
    "sendTask": "shape=message;html=1;fillColor=#1A1A1A;strokeColor=#FFFFFF;",
    "receiveTask": "shape=message;html=1;fillColor=none;strokeColor=#1A1A1A;",
    "manualTask": "shape=mxgraph.bpmn.manual_task;html=1;",
    "scriptTask": "shape=mxgraph.bpmn.script_task;html=1;",
    "businessRuleTask": "shape=mxgraph.bpmn.business_rule_task;html=1;",
}


def xa(s):
    """Экранирование значения атрибута mxCell (value): & < > " + перенос строки → &#10;."""
    s = (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return s.replace("\n", "&#10;")


def _vertex(cid, value, style, x, y, w, h, parent="1"):
    return ('        <mxCell id="%s" value="%s" style="%s" vertex="1" parent="%s">\n'
            '          <mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry" />\n'
            '        </mxCell>' % (cid, xa(value), style, parent, x, y, w, h))


def _node_cells(n, idp):
    """Узел → список ячеек: родитель + опц. дочерний BPMN-маркер (тип шлюза / иконка типа задачи /
    [+] подпроцесса). Цвет по TYPE_COLORS; PRB → маджента. Подписи событий/шлюзов — под фигурой."""
    nid = idp + n["id"]
    stroke, fill = TYPE_COLORS.get(n["tag"], (NEUTRAL, "#FFFFFF"))
    if n.get("prb"):
        stroke = ACCENT
    name = n["name"]
    out = []
    if n["is_event"]:
        # outline (старт/конец/промежут.) + symbol (тип триггера); круг БЕЗ подписи
        outline = EV_OUTLINE.get(n["tag"], "standard")
        symbol = EV_SYMBOL.get(n.get("trigger", "none"), "general")
        st = ("shape=mxgraph.bpmn.shape;perimeter=ellipsePerimeter;html=1;outline=%s;symbol=%s;"
              "fillColor=%s;strokeColor=%s;" % (outline, symbol, fill, stroke))
        out.append(_vertex(nid, "", st, n["x"], n["y"], n["w"], n["h"]))
        # подпись — дочерним text-боксом под кругом с переносом (длинные не вылезают за пул)
        LW = 132
        lst = "text;html=1;align=center;verticalAlign=top;whiteSpace=wrap;fontColor=#1A1A1A;"
        out.append(_child("%s_l" % nid, name, lst, nid, (n["w"] - LW) // 2, n["h"] + 2, LW, 40))
    elif n["is_gateway"]:
        # ромб + дочерний глиф-маркер типа (✕/✚/◯); подпись снизу
        st = "rhombus;whiteSpace=wrap;html=1;%sfillColor=%s;strokeColor=%s;fontColor=#1A1A1A;" % (LBL_BELOW, fill, stroke)
        out.append(_vertex(nid, name, st, n["x"], n["y"], n["w"], n["h"]))
        glyph = GW_GLYPH.get(n.get("subtype", ""), "✕")
        mst = ("text;html=1;align=center;verticalAlign=middle;fontSize=%d;fontStyle=1;"
               "strokeColor=none;fillColor=none;fontColor=#1A1A1A;" % max(14, n["h"] // 2))
        out.append('        <mxCell id="%s_m" value="%s" style="%s" vertex="1" parent="%s">\n'
                   '          <mxGeometry x="0" y="0" width="%d" height="%d" as="geometry" />\n'
                   '        </mxCell>' % (nid, glyph, mst, nid, n["w"], n["h"]))
    else:
        # задача / подпроцесс: скруг. прямоуг.; система 2-й строкой; иконка типа в углу
        if n["tag"].endswith("Task") and n.get("system"):
            sysid = (re.search(r"SYS-\d+", n["system"]) or [None])[0]
            if sysid:
                name = "%s\n— %s" % (name, sysid)
        sw = ";strokeWidth=3" if n["tag"] in ("subProcess", "callActivity") else ""
        icon = TASK_ICON.get(n["tag"])
        # у типизированной задачи иконка-маркер в углу (крупнее для читаемости) — текст ниже
        valign = "verticalAlign=top;spacingTop=30;" if icon else "verticalAlign=middle;"
        st = "rounded=1;whiteSpace=wrap;html=1;%sfillColor=%s;strokeColor=%s;fontColor=#1A1A1A%s;" % (valign, fill, stroke, sw)
        out.append(_vertex(nid, name, st, n["x"], n["y"], n["w"], n["h"]))
        if icon:
            out.append(_child("%s_i" % nid, "", icon, nid, 6, 5, 24, 24))
        elif n["tag"] in ("subProcess", "callActivity"):
            # маркер свёрнутого подпроцесса [+] снизу по центру
            pst = ("rounded=0;html=1;strokeColor=%s;fillColor=none;fontSize=14;fontStyle=1;"
                   "align=center;verticalAlign=middle;" % stroke)
            out.append(_child("%s_p" % nid, "+", pst, nid, n["w"] // 2 - 9, n["h"] - 20, 18, 16))
    return out


def _edge(cid, value, style, src, tgt):
    """Ребро только с привязкой к элементам (source/target). Без промежуточных точек и
    фиксированных портов — draw.io роутит ортогонально сам (чище, не «хаотично»)."""
    return ('        <mxCell id="%s" value="%s" style="%s" edge="1" parent="1" source="%s" target="%s">\n'
            '          <mxGeometry relative="1" as="geometry" />\n'
            '        </mxCell>' % (cid, xa(value), style, src, tgt))


def _child(cid, value, style, parent, x, y, w, h):
    """Дочерний элемент (геометрия относительно родителя): маркер/иконка/подпись-бокс."""
    return ('        <mxCell id="%s" value="%s" style="%s" vertex="1" parent="%s">\n'
            '          <mxGeometry x="%d" y="%d" width="%d" height="%d" as="geometry" />\n'
            '        </mxCell>' % (cid, xa(value), style, parent, x, y, w, h))


def _build_cells(pools, lanes, nodes, order, flows, msgflows, data, idp=""):
    """Модель после раскладки → список mxCell-строк. idp — префикс id (изоляция страниц
    в многостраничном файле: id и все ссылки src/tgt префиксуются одинаково)."""
    cells = []
    # пулы (фон) — первыми, чтобы оказаться позади
    for pid, p in pools.items():
        if "bounds" not in p:
            continue
        x, y, w, h = p["bounds"]
        cells.append(_vertex(idp + pid, p["name"], POOL_STYLE, x, y, w, h))
    # дорожки-роли (системная — отдельным стилем-полосой)
    for lid, ln in lanes.items():
        if "bounds" not in ln:
            continue
        x, y, w, h = ln["bounds"]
        style = SYSLANE_STYLE if ln.get("kind") == "system" else LANE_STYLE
        cells.append(_vertex(idp + lid, ln["name"], style, x, y, w, h))
    # дорожки-артефакты (объекты системы), названные по системе действия
    for pid, p in pools.items():
        for k, (sysname, bounds) in enumerate(p.get("artifact_lanes", [])):
            x, y, w, h = bounds
            cells.append(_vertex("%s%s_art%d" % (idp, pid, k), sysname, ARTLANE_STYLE, x, y, w, h))
    # узлы (поверх дорожек) — с BPMN-маркерами типов
    for nid in order:
        cells.extend(_node_cells(nodes[nid], idp))
    # объекты системы (документ/хранилище); теневое ИТ (@дом) — янтарным оттенком
    for d in data:
        style = DATASTORE_STYLE if d["kind"] == "store" else DATAOBJ_STYLE
        if d.get("home"):
            style = style.replace("fillColor=#FFFFFF", "fillColor=%s" % SHADOW_FILL)
        cells.append(_vertex(idp + d["ref"], d["name"], style, d["x"], d["y"], d["w"], d["h"]))
    # рёбра — только привязка к элементам (source/target), draw.io роутит ортогонально сам
    for f in flows:
        cells.append(_edge(idp + f["id"], f.get("cond", ""), SEQ_STYLE, idp + f["src"], idp + f["tgt"]))
    for f in msgflows:
        cells.append(_edge(idp + f["id"], f.get("msg", ""), MSG_STYLE, idp + f["src"], idp + f["tgt"]))
    for d in data:
        src, tgt = (d["task"], d["ref"]) if d["dir"] == "out" else (d["ref"], d["task"])
        cells.append(_edge(idp + d["assoc"], "", ASSOC_STYLE, idp + src, idp + tgt))
    return cells


def _diagram_page(cells, page_id, page_name):
    """Список mxCell-строк → блок <diagram> (отдельная страница draw.io со своим mxGraphModel)."""
    body = "\n".join(cells)
    return ('  <diagram id="%s" name="%s">\n'
            '    <mxGraphModel dx="1200" dy="850" grid="0" gridSize="10" guides="1" tooltips="1" '
            'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1169" pageHeight="826" '
            'math="0" shadow="0">\n'
            '      <root>\n'
            '        <mxCell id="0" />\n'
            '        <mxCell id="1" parent="0" />\n'
            '%s\n'
            '      </root>\n'
            '    </mxGraphModel>\n'
            '  </diagram>') % (xa(page_id), xa(page_name[:60]), body)


def _wrap_mxfile(pages):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<mxfile host="bpmn-gen" type="device">\n'
            + "\n".join(pages) + "\n"
            + '</mxfile>\n')


def emit_drawio(meta, pools, lanes, nodes, order, flows, msgflows, data, src_basename, sig, mode):
    """Модель после раскладки → одностраничный mxGraph XML. Параллель emit_xml() BPMN-эмиттера."""
    title = meta.get("title") or os.path.splitext(src_basename)[0]
    cells = _build_cells(pools, lanes, nodes, order, flows, msgflows, data)
    return _wrap_mxfile([_diagram_page(cells, "process", title)])


def generate(path, mode, sig, lock=False):
    """Реестр-граф → (.drawio xml, meta). Валидация слоя A — та же, что у BPMN (build_laid_out)."""
    meta, pools, lanes, nodes, order, flows, msgflows, data = bpmn_graph.build_laid_out(path, mode)
    xml = emit_drawio(meta, pools, lanes, nodes, order, flows, msgflows, data,
                      os.path.basename(path), sig, mode)
    return xml, meta


def generate_book(paths, mode, sig):
    """Несколько реестров-графов → один многостраничный .drawio (страница на реестр).
    Каждая страница изолирована префиксом id (p1_/p2_/…). Возвращает (xml, [titles])."""
    pages, titles = [], []
    for i, path in enumerate(paths):
        meta, pools, lanes, nodes, order, flows, msgflows, data = bpmn_graph.build_laid_out(path, mode)
        name = meta.get("title") or os.path.splitext(os.path.basename(path))[0]
        titles.append(name)
        cells = _build_cells(pools, lanes, nodes, order, flows, msgflows, data, idp="p%d_" % (i + 1))
        pages.append(_diagram_page(cells, "page-%d" % (i + 1), name))
    return _wrap_mxfile(pages), titles


if __name__ == "__main__":
    # автономный прогон: drawio.py <реестр.md> [out.drawio]
    src = sys.argv[1]
    xml, meta = generate(src, "as-is", "test00")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + ".drawio"
    with open(out, "w", encoding="utf-8") as f:
        f.write(xml)
    print("DRAWIO:", out)
