#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common — общие хелперы BPMN-генератора (stdlib-only, без внешних пакетов).

Извлечены без изменения поведения из ядра OPP (`scripts/render_protocol.py` и
`scripts/diagram.py`, ADR-0018) при выносе движка `process-viz` в отдельный проект.
Здесь только то, что нужно BPMN-пути: парсинг frontmatter/markdown-таблиц, поиск
бинарников (node/rsvg), разбор CLI-опций, манифест схем и защёлка от дрейфа.

Источник правды — markdown-реестр; .bpmn/.svg/.drawio — производные с sha1-шапкой.
"""
import sys
import os
import re
import shutil
import hashlib


# ── Фирстиль «Первый Бит» (одна правда цвета с исходным движком) ─────────────
FONT = "Noto Sans"
MAGENTA = (0xE3, 0x00, 0x7D)      # фирменный акцент H2
HEADER_BG = "595959"              # шапка таблиц (нейтральный серый)
BLACK = (0x1A, 0x1A, 0x1A)
WHITE = (0xFF, 0xFF, 0xFF)
GREY = (0x6E, 0x6E, 0x6E)
ACCENT = "#E3007D"                # PRB-/CR-/активный путь (= hx(MAGENTA))


def die(msg):
    sys.stderr.write("bpmn-gen: " + msg + "\n")
    sys.exit(1)


def warn(msg):
    sys.stderr.write("bpmn-gen: ВНИМАНИЕ — " + msg + "\n")


# ════════════════════════════════════════════════════════════════════════════
#  Чтение файла + парсинг frontmatter и markdown-таблиц (stdlib-only)
# ════════════════════════════════════════════════════════════════════════════
def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_front(text):
    """Вернуть (meta: dict, body: str). meta пуст, если frontmatter нет."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta = {}
    for ln in lines[1:end]:
        s = ln.strip()
        if not s or s.startswith("#") or ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, "\n".join(lines[end + 1:])


def split_row(raw):
    """'| a | b |' → ['a','b'] (учитывая экранированные \\|)."""
    cells = re.split(r"(?<!\\)\|", raw.strip())
    if cells and cells[0].strip() == "":
        cells = cells[1:]
    if cells and cells[-1].strip() == "":
        cells = cells[:-1]
    return [c.strip() for c in cells]


def is_sep_row(raw):
    """Строка-разделитель таблицы: |---|:--:|…|."""
    s = raw.strip()
    return bool(re.match(r"^\|?[\s:\-|]+\|?$", s)) and "-" in s


# ════════════════════════════════════════════════════════════════════════════
#  Поиск бинарников (die() — не молчаливый фолбэк)
# ════════════════════════════════════════════════════════════════════════════
def _find(cands, paths, install):
    for c in cands:
        hit = shutil.which(c)
        if hit:
            return hit
    for p in paths:
        if os.path.isfile(p):
            return p
    die("%s не найден. Установи: %s" % (cands[0], install))


def rsvg_bin():
    return _find(("rsvg-convert", "rsvg-convert.exe"),
                 ("/opt/homebrew/bin/rsvg-convert", "/usr/local/bin/rsvg-convert", "/usr/bin/rsvg-convert"),
                 "brew install librsvg / apt install librsvg2-bin")


def node_bin():
    return _find(("node", "node.exe"),
                 ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node",
                  r"C:\Program Files\nodejs\node.exe"),
                 "установи Node.js ≥18 (nodejs.org) и выполни `npm install` в node/ (нужно для BPMN)")


def drawio_bin():
    """draw.io Desktop CLI: на PATH (`drawio`) или бандл-приложение. None если нет
    (PNG-экспорт из .drawio опционален — сам .drawio собирается без него)."""
    for c in ("drawio", "draw.io"):
        hit = shutil.which(c)
        if hit:
            return hit
    for p in ("/Applications/draw.io.app/Contents/MacOS/draw.io",
              os.path.expanduser("~/Applications/draw.io.app/Contents/MacOS/draw.io"),
              r"C:\Program Files\draw.io\draw.io.exe"):
        if os.path.isfile(p):
            return p
    return None


# ════════════════════════════════════════════════════════════════════════════
#  Чтение реестра-таблицы (первая markdown-таблица тела)
# ════════════════════════════════════════════════════════════════════════════
def load_table(path):
    """parse_front + первая markdown-таблица тела → (meta, headers, rows[list[dict]])."""
    if not os.path.isfile(path):
        die("реестр не найден: " + path)
    meta, body = parse_front(read(path))
    lines = body.split("\n")
    headers, rows, i = [], [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and is_sep_row(lines[i + 1]):
            headers = split_row(lines[i])
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if is_sep_row(lines[i]):
                    i += 1
                    continue
                cells = split_row(lines[i])
                rows.append({headers[j]: (cells[j].strip() if j < len(cells) else "")
                             for j in range(len(headers))})
                i += 1
            break  # только первая таблица
        i += 1
    return meta, headers, rows


def find_col(headers, *cands):
    """Имя колонки по кандидатам: точное совпадение (без регистра), затем вхождение. None если нет."""
    low = {h.strip().lower(): h for h in headers}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    for c in cands:
        for h in headers:
            if c.lower() in h.strip().lower():
                return h
    return None


def cell(row, colname):
    return (row.get(colname, "") if colname else "").strip()


def first_id(text, pat):
    m = re.search(pat, text)
    return m.group(0) if m else ""


# маркеры «шаг-решение» для fallback-скелета (плоская цепочка без графа)
GATEWAY_RX = re.compile(r"(если|иначе|согласов|проверк|контрол|sod|лимит|превыш|утвержд|откло)", re.I)


# ════════════════════════════════════════════════════════════════════════════
#  CLI-опции
# ════════════════════════════════════════════════════════════════════════════
def parse_opts(rest):
    o = {"code": None, "mode": "as-is", "emit": ["png", "svg", "dot"], "scale": None,
         "cluster": True, "lock": False, "png": False, "pos": []}
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--code":
            o["code"] = rest[i + 1]; i += 2
        elif a == "--png":           # для drawio: дорастрировать .drawio → PNG через draw.io Desktop
            o["png"] = True; i += 1
        elif a == "--mode":
            o["mode"] = rest[i + 1]; i += 2
        elif a == "--emit":
            o["emit"] = [x.strip() for x in rest[i + 1].split(",") if x.strip()]; i += 2
        elif a == "--scale":
            o["scale"] = int(rest[i + 1]); i += 2
        elif a == "--no-cluster":
            o["cluster"] = False; i += 1
        elif a == "--cluster":
            o["cluster"] = True; i += 1
        elif a == "--lock":
            o["lock"] = True; i += 1
        else:
            o["pos"].append(a); i += 1
    if o["mode"] not in ("as-is", "to-be"):
        die("--mode должен быть as-is|to-be, а не «%s»" % o["mode"])
    return o


# ════════════════════════════════════════════════════════════════════════════
#  Манифест и защёлка от дрейфа (sha1 строк-таблиц реестра)
# ════════════════════════════════════════════════════════════════════════════
def table_sig(paths):
    h = hashlib.sha1()
    for p in sorted(paths):
        if not os.path.isfile(p):
            continue
        for ln in read(p).split("\n"):
            if ln.lstrip().startswith("|"):
                h.update(ln.strip().encode("utf-8"))
    return h.hexdigest()[:10]


def parse_dot_header(path):
    """SOURCE-шапка из .dot (// SOURCE:), .bpmn/.drawio (<!-- SOURCE: … -->)."""
    txt = read(path)
    src = re.search(r"SOURCE: (.+?)(?: -->|$)", txt, re.M)
    sig = re.search(r"sha1=([0-9a-f]+)", txt)
    kind = re.search(r"kind=(\S+)", txt)
    paths = re.search(r"SRCPATHS: (.+?)(?: -->|$)", txt, re.M)
    return {
        "source": src.group(1).strip() if src else "",
        "sig": sig.group(1) if sig else "",
        "kind": kind.group(1) if kind else "",
        "paths": paths.group(1).strip().split("|") if paths else [],
        "locked": ("// LOCKED" in txt) or ("<!-- LOCKED" in txt),
    }


def write_manifest(outdir):
    entries = []
    for f in sorted(os.listdir(outdir)):
        if f.endswith(".bpmn") and not f.endswith(".regen.bpmn"):
            base = f[:-5]
        elif f.endswith(".drawio") and not f.endswith(".regen.drawio"):
            base = f[:-7]
        elif f.endswith(".dot") and not f.endswith(".regen.dot"):
            base = f[:-4]
        else:
            continue
        png = base + ".png"
        has_png = os.path.exists(os.path.join(outdir, png))
        hd = parse_dot_header(os.path.join(outdir, f))
        artifact = "diagrams/" + png if has_png else f
        entries.append((hd["kind"] or "?", artifact, hd["source"]))
    lines = ["# Манифест схем — рабочая память", "",
             "> Сгенерировано из реестров. Источник правды — реестр, не картинка.",
             "", "| № | Тип | Артефакт | Источник |", "|---|---|---|---|"]
    for i, (kind, art, src) in enumerate(entries, 1):
        lines.append("| %d | %s | `%s` | %s |" % (i, kind, art, src))
    with open(os.path.join(outdir, "diagrams-manifest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
