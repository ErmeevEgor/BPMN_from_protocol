#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bpmn_gen — CLI генератора схем процессов из markdown-реестра (BPMN-ориентированный граф).

Команды:
    bpmn_gen.py bpmn   <реестр.md> <outdir> [--code КОД] [--mode as-is|to-be] [--lock] [--scale N]
        реестр (BPMN-граф) → BPMN 2.0 XML (детерминированно) → bpmnlint (слой B) →
        bpmn-js (Node, headless jsdom) → SVG → PNG.
    bpmn_gen.py drawio <реестр.md> <outdir> [--code КОД] [--mode as-is|to-be] [--lock] [--png]
        тот же граф и раскладка → .drawio (mxGraph) для правки в diagrams.net / draw.io.
        Чистый Python — Node не нужен. С --png также растрирует через draw.io Desktop CLI.
        [старт цели разморозки]
    bpmn_gen.py drawio-book <out.drawio> <reg1.md> <reg2.md> ... [--mode as-is|to-be] [--png]
        несколько реестров → ОДИН многостраничный .drawio (основной процесс + подпроцессы,
        страница на реестр). С --png растрирует каждую страницу (out_p1.png, out_p2.png, …).

Реестр-граф — по контракту spec/process-bpmn-spec.md (frontmatter `process-graph: true`).
Источник правды — реестр; .bpmn/.svg/.drawio — производные с sha1-шапкой (дрейф-чек).
Зависимости BPMN-пути: Node ≥18 + `npm install` в node/, rsvg-convert.

История движка (ADR из проекта OPP): 0019 (детерминированный эмиттер + bpmn-js рендер),
0020 (граф-реестр + двухслойная валидация), 0021 (визуал + объекты данных).
"""
import os
import re
import sys
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from common import (  # noqa: E402
    die, warn, find_col, cell, first_id, GATEWAY_RX, ACCENT,
    parse_opts, read, parse_front, load_table, table_sig, write_manifest,
    rsvg_bin, node_bin, drawio_bin,
)
import bpmn_graph  # noqa: E402
import drawio  # noqa: E402

_NODE_DIR = os.path.join(_HERE, "node")


def bxml(s):
    """Экранирование для XML-атрибутов/текста BPMN."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def emit_bpmn_xml(headers, rows, mode, title):
    """processes/скелет → BPMN 2.0 XML с раскладкой (pool + дорожки по Ответственный +
    задачи/шлюзы + потоки + DI-координаты). Раскладку («умную часть») держим здесь
    детерминированно; bpmn-js — тонкий рендер нотации."""
    step_c = find_col(headers, "Шаг процесса", "Шаг", "ПРОЦЕСС", "Процесс", "Подпроцесс")
    own_c = find_col(headers, "Ответственный") or find_col(headers, "ВЛАДЕЛЕЦ", "Владелец")
    sys_c = find_col(headers, "Система", "СИСТЕМЫ", "Системы")
    desc_c = find_col(headers, "Описание действий", "Описание")
    prob_c = find_col(headers, "Проблемы")
    if not step_c:
        die("в реестре процессов не найдена колонка шага (Шаг процесса/ПРОЦЕСС). Заголовки: %s" % headers)
    steps = [r for r in rows if cell(r, step_c)]
    if not steps:
        die("нет строк-шагов для BPMN")

    lanes = []
    for r in steps:
        o = cell(r, own_c) or "— не назначен —"
        if o not in lanes:
            lanes.append(o)
    lidx = {o: i for i, o in enumerate(lanes)}

    POOL_X, POOL_Y, LANE_H, COL_W = 160, 80, 120, 170
    CONTENT_X0, EVENT_D, GW, TASK_W, TASK_H = POOL_X + 100, 36, 50, 130, 80

    def ln_of(r):
        return lidx[cell(r, own_c) or "— не назначен —"]

    els = [{"id": "StartEvent_1", "kind": "start", "name": "Начало", "lane": ln_of(steps[0])}]
    for i, r in enumerate(steps):
        desc = cell(r, desc_c) if desc_c else ""
        els.append({
            "id": "Node_%d" % (i + 1),
            "kind": "gateway" if (desc and GATEWAY_RX.search(desc)) else "task",
            "name": cell(r, step_c), "lane": ln_of(r),
            "sys": cell(r, sys_c) if sys_c else "",
            "prb": first_id(cell(r, prob_c), r"PRB-\d+") if prob_c else "",
        })
    els.append({"id": "EndEvent_1", "kind": "end", "name": "Конец", "lane": ln_of(steps[-1])})

    for col, el in enumerate(els):
        cx = CONTENT_X0 + 40 + col * COL_W
        cy = POOL_Y + el["lane"] * LANE_H + LANE_H // 2
        w, h = (EVENT_D, EVENT_D) if el["kind"] in ("start", "end") else (GW, GW) if el["kind"] == "gateway" else (TASK_W, TASK_H)
        el.update(x=cx - w // 2, y=cy - h // 2, w=w, h=h, cx=cx, cy=cy)

    pool_w = (CONTENT_X0 - POOL_X) + 40 + (len(els) - 1) * COL_W + COL_W // 2 + 40
    pool_h = len(lanes) * LANE_H
    flows = [("Flow_%d" % (i + 1), els[i], els[i + 1]) for i in range(len(els) - 1)]

    def waypoints(s, t):
        sx, sy, tx, ty = s["x"] + s["w"], s["cy"], t["x"], t["cy"]
        if sy == ty:
            return [(sx, sy), (tx, ty)]
        mx = (sx + tx) // 2
        return [(sx, sy), (mx, sy), (mx, ty), (tx, ty)]

    P = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
         'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
         'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
         'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
         'xmlns:bioc="http://bpmn.io/schema/bpmn/biocolor/1.0" '
         'id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">',
         '  <bpmn:collaboration id="Collaboration_1">',
         '    <bpmn:participant id="Participant_1" name="" processRef="Process_1" />',
         '  </bpmn:collaboration>',
         '  <bpmn:process id="Process_1" isExecutable="false">',
         '    <bpmn:laneSet id="LaneSet_1">']
    for o in lanes:
        li = lidx[o]
        P.append('      <bpmn:lane id="Lane_%d" name="%s">' % (li, bxml(o)))
        P += ['        <bpmn:flowNodeRef>%s</bpmn:flowNodeRef>' % e["id"] for e in els if e["lane"] == li]
        P.append('      </bpmn:lane>')
    P.append('    </bpmn:laneSet>')
    tagmap = {"start": "startEvent", "end": "endEvent", "gateway": "exclusiveGateway", "task": "task"}
    for el in els:
        inc = "".join('<bpmn:incoming>%s</bpmn:incoming>' % f[0] for f in flows if f[2] is el)
        out = "".join('<bpmn:outgoing>%s</bpmn:outgoing>' % f[0] for f in flows if f[1] is el)
        nm = el["name"]
        if el["kind"] == "task" and el.get("sys"):
            nm = "%s\n— %s" % (nm, el["sys"])      # система как 2-я строка в задаче (Ур.3↔Ур.5)
        tag = tagmap[el["kind"]]
        P.append('    <bpmn:%s id="%s" name="%s">%s%s</bpmn:%s>' % (tag, el["id"], bxml(nm), inc, out, tag))
    for fid, s, t in flows:
        P.append('    <bpmn:sequenceFlow id="%s" sourceRef="%s" targetRef="%s" />' % (fid, s["id"], t["id"]))
    P.append('  </bpmn:process>')
    P += ['  <bpmndi:BPMNDiagram id="BPMNDiagram_1">',
          '    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_1">',
          '      <bpmndi:BPMNShape id="Participant_1_di" bpmnElement="Participant_1" isHorizontal="true">',
          '        <dc:Bounds x="%d" y="%d" width="%d" height="%d" />' % (POOL_X, POOL_Y, pool_w, pool_h),
          '      </bpmndi:BPMNShape>']
    for o in lanes:
        li = lidx[o]
        P += ['      <bpmndi:BPMNShape id="Lane_%d_di" bpmnElement="Lane_%d" isHorizontal="true">' % (li, li),
              '        <dc:Bounds x="%d" y="%d" width="%d" height="%d" />' % (POOL_X + 30, POOL_Y + li * LANE_H, pool_w - 30, LANE_H),
              '      </bpmndi:BPMNShape>']
    for el in els:
        accent = ' bioc:stroke="%s" bioc:fill="#FFFFFF"' % ACCENT if el.get("prb") else ''
        marker = ' isMarkerVisible="true"' if el["kind"] == "gateway" else ''
        P.append('      <bpmndi:BPMNShape id="%s_di" bpmnElement="%s"%s%s>' % (el["id"], el["id"], marker, accent))
        P.append('        <dc:Bounds x="%d" y="%d" width="%d" height="%d" />' % (el["x"], el["y"], el["w"], el["h"]))
        if el["kind"] in ("start", "end", "gateway"):
            P.append('        <bpmndi:BPMNLabel><dc:Bounds x="%d" y="%d" width="104" height="28" /></bpmndi:BPMNLabel>'
                     % (el["cx"] - 52, el["y"] + el["h"] + 5))
        P.append('      </bpmndi:BPMNShape>')
    for fid, s, t in flows:
        P.append('      <bpmndi:BPMNEdge id="%s_di" bpmnElement="%s">' % (fid, fid))
        P += ['        <di:waypoint x="%d" y="%d" />' % (x, y) for (x, y) in waypoints(s, t)]
        P.append('      </bpmndi:BPMNEdge>')
    P += ['    </bpmndi:BPMNPlane>', '  </bpmndi:BPMNDiagram>', '</bpmn:definitions>']
    return "\n".join(P) + "\n"


def _write_with_header(xml, out_path, reg, kind, code, mode, sig, lock, ext, base, outdir):
    """Единая шапка SOURCE/SRCPATHS/LOCKED после <?xml?> + защёлка от затирания залоченного."""
    hdr = ("<!-- SOURCE: %s · kind=%s · code=%s · mode=%s · sha1=%s -->\n<!-- SRCPATHS: %s -->\n"
           % (os.path.basename(reg), kind, code or "-", mode, sig, os.path.abspath(reg)))
    if lock:
        hdr += "<!-- LOCKED -->\n"
    lines = xml.split("\n", 1)
    out_xml = lines[0] + "\n" + hdr + (lines[1] if len(lines) > 1 else "")
    if os.path.isfile(out_path) and "<!-- LOCKED" in read(out_path) and not lock:
        out_path = os.path.join(outdir, base + ".regen" + ext)
        warn("%s залочен — новый вариант рядом: %s (сверь вручную)" % (base + ext, os.path.basename(out_path)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out_xml)
    return out_path


def cmd_bpmn(rest):
    """Настоящий BPMN 2.0: реестр → BPMN XML (детерминированно) → bpmn-js (Node) → SVG → PNG."""
    o = parse_opts(rest)
    if len(o["pos"]) < 2:
        die("bpmn: нужно <реестр.md> <outdir>")
    reg, outdir = o["pos"][0], o["pos"][1]
    if not os.path.isfile(reg):
        die("реестр не найден: " + reg)
    meta0, _body = parse_front(read(reg))
    graph_mode = str(meta0.get("process-graph", "")).strip().lower() in ("true", "да", "yes", "1")
    code = o["code"]
    os.makedirs(outdir, exist_ok=True)
    code_slug = re.sub(r"[^0-9A-Za-z]+", "-", code).strip("-") if code else "overview"
    base = "schema_bpmn_%s_%s" % (code_slug, o["mode"])
    bpmn_path = os.path.join(outdir, base + ".bpmn")
    svg_path = os.path.join(outdir, base + ".svg")
    png_path = os.path.join(outdir, base + ".png")
    sig = table_sig([reg])

    if graph_mode:
        # BPMN-ориентированный граф-реестр (ADR-0020): parse→infer→авто-шлюзы→валидация-A→layout
        try:
            xml, _meta = bpmn_graph.generate(reg, o["mode"], sig)
        except bpmn_graph.BpmnError as e:
            die("BPMN (граф-режим, %s) не прошёл валидацию методологии:\n%s" % (os.path.basename(reg), e))
    else:
        # фолбэк: плоский линейный скелет (без шлюзов; profile: skeleton)
        meta, headers, rows = load_table(reg)
        if code:
            kc = find_col(headers, "КОД", "code", "Код")
            if not kc:
                die("--code %s задан, но в реестре нет колонки КОД — фильтр по коду невозможен" % code)
            rows = [r for r in rows if cell(r, kc) == code or cell(r, kc).startswith(code + ".")]
            if not rows:
                die("по --code %s в реестре нет строк (колонка %s)" % (code, kc))
        mode_ru = "TO-BE" if o["mode"] == "to-be" else "AS-IS"
        base_t = meta.get("title") or (("Процесс " + code) if code else "Процесс")
        title = base_t if re.search(r"as[\s-]?is|to[\s-]?be", base_t, re.I) else "%s (%s)" % (base_t, mode_ru)
        xml = emit_bpmn_xml(headers, rows, o["mode"], title)

    bpmn_path = _write_with_header(xml, bpmn_path, reg, "bpmn", code, o["mode"], sig, o["lock"],
                                   ".bpmn", base, outdir)

    nm = _NODE_DIR
    if not os.path.isfile(os.path.join(nm, "node_modules", "bpmn-js", "package.json")):
        die("BPMN-стек не установлен. Выполни `npm install` в %s" % nm)

    # gate: bpmnlint (слой B) для граф-режима — невалидный BPMN не собираем
    linter = os.path.join(nm, "lint_bpmn.js")
    if graph_mode and os.path.isfile(linter):
        lr = subprocess.run([node_bin(), linter, bpmn_path], capture_output=True)
        if lr.returncode != 0:
            for pth in (bpmn_path, svg_path, png_path):
                if os.path.exists(pth):
                    os.remove(pth)
            die("bpmnlint (слой B) отверг BPMN (артефакты удалены):\n"
                + lr.stderr.decode("utf-8", "ignore")[-800:])

    # рендер настоящей BPMN-нотации через bpmn-js (Node, headless jsdom)
    r = subprocess.run([node_bin(), os.path.join(nm, "render_bpmn.js"), bpmn_path, svg_path], capture_output=True)
    if r.returncode != 0 or not os.path.exists(svg_path):
        die("render_bpmn.js не создал SVG:\n" + r.stderr.decode("utf-8", "ignore")[-600:])
    # PNG в высоком DPI: для широких схем фикс-ширина даёт даунскейл/размытие — растрируем
    # в 2× натурального размера (чёткость при вставке в docx), либо точную ширину при --scale.
    rsvg_size = ["-w", str(int(o["scale"]))] if o["scale"] else ["--zoom", "2"]
    rr = subprocess.run([rsvg_bin()] + rsvg_size +
                        ["--background-color", "white", svg_path, "-o", png_path], capture_output=True)
    if rr.returncode != 0 or not os.path.exists(png_path):
        die("rsvg-convert не создал PNG из BPMN-SVG: " + rr.stderr.decode("utf-8", "ignore")[-300:])
    write_manifest(outdir)
    print("OK :", base, "→", outdir, "(bpmn,svg,png)", "[граф · bpmnlint ✓]" if graph_mode else "[скелет]")


def cmd_drawio(rest):
    """Тот же граф/раскладка → .drawio (mxGraph) для редактирования в diagrams.net. Чистый Python, без Node.
    [старт цели разморозки: редактируемые схемы в формате draw.io]"""
    o = parse_opts(rest)
    if len(o["pos"]) < 2:
        die("drawio: нужно <реестр.md> <outdir>")
    reg, outdir = o["pos"][0], o["pos"][1]
    if not os.path.isfile(reg):
        die("реестр не найден: " + reg)
    meta0, _body = parse_front(read(reg))
    graph_mode = str(meta0.get("process-graph", "")).strip().lower() in ("true", "да", "yes", "1")
    if not graph_mode:
        die("drawio (v1) требует BPMN-граф-реестр (frontmatter `process-graph: true`). "
            "Плоский скелет для draw.io пока не поддержан — используй команду `bpmn`.")
    code = o["code"]
    os.makedirs(outdir, exist_ok=True)
    code_slug = re.sub(r"[^0-9A-Za-z]+", "-", code).strip("-") if code else "overview"
    base = "schema_drawio_%s_%s" % (code_slug, o["mode"])
    out_path = os.path.join(outdir, base + ".drawio")
    sig = table_sig([reg])
    try:
        xml, _meta = drawio.generate(reg, o["mode"], sig)
    except bpmn_graph.BpmnError as e:
        die("draw.io (граф-режим, %s) не прошёл валидацию методологии:\n%s" % (os.path.basename(reg), e))
    out_path = _write_with_header(xml, out_path, reg, "drawio", code, o["mode"], sig, o["lock"],
                                  ".drawio", base, outdir)
    extra = ""
    if o["png"]:
        extra = _drawio_png(out_path)
    write_manifest(outdir)
    print("OK :", base, "→", outdir, "(drawio%s)" % extra, "[граф]")


def _drawio_png(drawio_path):
    """Дорастрировать .drawio → PNG официальным draw.io Desktop CLI (если установлен).
    Опционально: сам .drawio готов и без него. Возвращает ',png' при успехе, '' иначе."""
    binp = drawio_bin()
    if not binp:
        warn("draw.io Desktop не найден — PNG не сделан (.drawio готов). "
             "Для PNG-экспорта: brew install --cask drawio")
        return ""
    png_path = os.path.splitext(drawio_path)[0] + ".png"
    # электрон-флаги (--no-sandbox/--disable-gpu) ЛОМАЮТ разбор аргументов draw.io — не добавлять.
    r = subprocess.run([binp, "-x", "-f", "png", "-s", "2", "-b", "12", "-o", png_path, drawio_path],
                       capture_output=True)
    if r.returncode == 0 and os.path.exists(png_path):
        return ",png"
    warn("draw.io export не создал PNG (.drawio готов). Закрой запущенный draw.io и повтори, либо "
         "открой .drawio вручную. " + r.stderr.decode("utf-8", "ignore")[-200:])
    return ""


def cmd_drawio_book(rest):
    """Несколько реестров-графов → ОДИН многостраничный .drawio (основной процесс + подпроцессы,
    страница на реестр). Использование: drawio-book <out.drawio> <reg1.md> <reg2.md> ... [--mode] [--png]"""
    o = parse_opts(rest)
    if len(o["pos"]) < 2:
        die("drawio-book: нужно <out.drawio> <reg1.md> [reg2.md ...]")
    out_path, regs = o["pos"][0], o["pos"][1:]
    for r in regs:
        if not os.path.isfile(r):
            die("реестр не найден: " + r)
        meta0, _b = parse_front(read(r))
        if str(meta0.get("process-graph", "")).strip().lower() not in ("true", "да", "yes", "1"):
            die("drawio-book: реестр %s не граф-режим (нужен `process-graph: true`)" % os.path.basename(r))
    if not out_path.endswith(".drawio"):
        out_path += ".drawio"
    outdir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(out_path))[0]
    sig = table_sig(regs)
    try:
        xml, titles = drawio.generate_book(regs, o["mode"], sig)
    except bpmn_graph.BpmnError as e:
        die("draw.io-сборник не прошёл валидацию методологии (слой A):\n%s" % e)
    out_path = _write_with_header(xml, out_path, regs[0], "drawio-book", None, o["mode"], sig, o["lock"],
                                  ".drawio", base, outdir)
    extra = _drawio_png_pages(regs, o["mode"], os.path.splitext(out_path)[0]) if o["png"] else ""
    write_manifest(outdir)
    print("OK :", os.path.basename(out_path), "— %d страниц%s" % (len(titles), extra))
    for i, t in enumerate(titles, 1):
        print("     %d) %s" % (i, t))


def _drawio_png_pages(regs, mode, out_base):
    """Растрировать каждую страницу → PNG (out_base_p1.png, …). draw.io CLI игнорирует
    --page-index (всегда первая страница), поэтому рендерим КАЖДЫЙ реестр отдельным
    одностраничным .drawio во временном файле и экспортируем его. Опционально."""
    binp = drawio_bin()
    if not binp:
        warn("draw.io Desktop не найден — PNG страниц не сделаны (.drawio готов). brew install --cask drawio")
        return ""
    ok = 0
    for i, reg in enumerate(regs):
        try:
            xml, _m = drawio.generate(reg, mode, "x")
        except bpmn_graph.BpmnError:
            continue
        fd, tmp = tempfile.mkstemp(suffix=".drawio")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(xml)
        png = "%s_p%d.png" % (out_base, i + 1)
        r = subprocess.run([binp, "-x", "-f", "png", "-s", "2", "-b", "12", "-o", png, tmp],
                           capture_output=True)
        os.remove(tmp)
        if r.returncode == 0 and os.path.exists(png):
            ok += 1
        else:
            warn("страница %d не растрирована: %s" % (i + 1, r.stderr.decode("utf-8", "ignore")[-160:]))
    return " (+%d/%d png)" % (ok, len(regs)) if ok else ""


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "bpmn":
        cmd_bpmn(rest)
    elif cmd == "drawio":
        cmd_drawio(rest)
    elif cmd == "drawio-book":
        cmd_drawio_book(rest)
    else:
        # совместимость: `bpmn_gen.py <реестр> <outdir>` == bpmn
        cmd_bpmn(argv)


if __name__ == "__main__":
    main(sys.argv[1:])
