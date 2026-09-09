#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bpmn_graph — BPMN-ориентированный реестр-граф → валидный BPMN 2.0 XML (детерминированно).

Конвейер (ADR-0020): таблицы по якорям `## Пулы/Дорожки/Узлы/Потоки` → граф → infer тегов
(матрица Тип+Подтип) → авто-вставка merge/split-шлюзов (нет шлюзов 1-вход-1-выход) →
валидация слоя A (методология BPMN, die до записи) → слоистая раскладка (дорожка × topo-глубина) →
BPMN 2.0 XML с DI. Слой B (bpmnlint) — отдельным Node-шагом (node/lint_bpmn.js).

Принцип: реестр = единственный источник правды; схема — воспроизводимый детерминированный вид.
Подключается из bpmn_gen.py (ветка `process-graph: true`). После раскладки модель отдаётся
как BPMN-, так и draw.io-эмиттеру (build_laid_out → emit_xml / drawio.emit_drawio).
"""
import os
import re
import sys

# Хелперы вынесены в локальный common (раньше — ядро OPP scripts/render_protocol).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import parse_front, split_row, is_sep_row, read  # noqa: E402

ACCENT = "#E3007D"      # PRB- акцент (фирстиль)
NEUTRAL = "#595959"

# ── раскладка (канон bpmn.io) ────────────────────────────────────────────────
POOL_X, POOL_Y = 160, 90
LANE_H, COL_W = 150, 200
LANE_LABEL_W = 30
EVENT_D, GW, TASK_W, TASK_H = 36, 50, 120, 90
EXT_POOL_H = 60

TASK_TAGS = {
    "user": "userTask", "service": "serviceTask", "manual": "manualTask",
    "send": "sendTask", "receive": "receiveTask", "script": "scriptTask",
    "businessrule": "businessRuleTask", "": "task",
}
GW_TAGS = {
    "xor": "exclusiveGateway", "and": "parallelGateway", "or": "inclusiveGateway",
    "event": "eventBasedGateway", "complex": "complexGateway", "": "exclusiveGateway",
}
EVENT_DEFS = {
    "message": "messageEventDefinition", "timer": "timerEventDefinition",
    "signal": "signalEventDefinition", "conditional": "conditionalEventDefinition",
    "error": "errorEventDefinition", "escalation": "escalationEventDefinition",
    "terminate": "terminateEventDefinition", "compensation": "compensateEventDefinition",
    "link": "linkEventDefinition",
}

# Семантические цвета по типу элемента (bioc) — приглушённая палитра «Первый Бит»/нотации
# заказчика: события старт=зелёный/конец=красный, шлюзы золотые, задачи по типу. (stroke, fill)
TYPE_COLORS = {
    "startEvent": ("#4F9D69", "#EAF5EE"),
    "endEvent": ("#C0504D", "#F8ECEC"),
    "intermediateCatchEvent": ("#C9A227", "#FBF6E3"),
    "intermediateThrowEvent": ("#C9A227", "#FBF6E3"),
    "boundaryEvent": ("#C9A227", "#FBF6E3"),
    "exclusiveGateway": ("#C9A227", "#FCF7E5"),
    "inclusiveGateway": ("#C9A227", "#FCF7E5"),
    "complexGateway": ("#C9A227", "#FCF7E5"),
    "eventBasedGateway": ("#C9A227", "#FCF7E5"),
    "parallelGateway": ("#3E7CB1", "#E9F1F8"),
    "userTask": ("#2E5C8A", "#EAF1FB"),
    "serviceTask": ("#5F6B76", "#EEF1F3"),
    "manualTask": ("#A8772E", "#FBF3E2"),
    "sendTask": ("#C2763F", "#FBEDE3"),
    "receiveTask": ("#C2763F", "#FBEDE3"),
    "businessRuleTask": ("#6B4C8A", "#F1EAF7"),
    "scriptTask": ("#4F8A6B", "#EAF4EF"),
    "subProcess": ("#2E5C8A", "#EAF1FB"),
    "callActivity": ("#2E5C8A", "#EAF1FB"),
    "task": ("#2E5C8A", "#EAF1FB"),
}


class BpmnError(Exception):
    pass


def xe(s):
    """XML-экранирование."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ════════════════════════════════════════════════════════════════════════════
#  ① PARSE: таблицы по якорям ## → структуры
# ════════════════════════════════════════════════════════════════════════════
def load_tables(path):
    """frontmatter + таблицы тела, индексированные по `## <заголовок>` (по ключевому слову)."""
    meta, body = parse_front(read(path))
    lines = body.split("\n")
    sections, cur = {}, "_intro"
    buf = []
    for ln in lines:
        h = re.match(r"^##\s+(.*)$", ln)
        if h:
            sections[cur] = buf
            cur, buf = h.group(1).strip().lower(), []
        else:
            buf.append(ln)
    sections[cur] = buf

    def pick(*keywords):
        for name, blines in sections.items():
            if any(k in name for k in keywords):
                t = _parse_table(blines)
                if t[0]:
                    return t
        return [], []

    return meta, {
        "pools": pick("пул"),
        "lanes": pick("дорож"),
        "nodes": pick("узл"),
        "flows": pick("поток"),
    }


def _parse_table(lines):
    """Первая markdown-таблица блока → (headers, rows[dict])."""
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
            break
        i += 1
    return headers, rows


def _col(row, *names):
    low = {k.strip().lower(): k for k in row}
    for n in names:
        if n.lower() in low:
            return row[low[n.lower()]].strip()
    for n in names:
        for k in row:
            if n.lower() in k.strip().lower():
                return row[k].strip()
    return ""


def _truthy(s):
    return s.strip().lower() in ("да", "yes", "true", "1", "x", "✓")


# ════════════════════════════════════════════════════════════════════════════
#  build graph
# ════════════════════════════════════════════════════════════════════════════
def build_graph(tables, lane_less=False):
    ph, prows = tables["pools"]
    lh, lrows = tables["lanes"]
    nh, nrows = tables["nodes"]
    fh, frows = tables["flows"]
    if not nrows or not frows:
        raise BpmnError("process-graph:true требует непустые таблицы «Узлы» и «Потоки»")

    pools = {}
    for r in prows:
        pid = _col(r, "pool_id", "id")
        if not pid:
            continue
        pools[pid] = {"id": pid, "name": _col(r, "участник", "name", "имя"),
                      "kind": _col(r, "тип пула", "kind") or "internal",
                      "blackbox": _truthy(_col(r, "чёрный ящик", "blackbox", "черный ящик")),
                      "lanes": []}

    lanes = {}
    for r in lrows:
        lid = _col(r, "lane_id", "id")
        if not lid:
            continue
        lanes[lid] = {"id": lid, "name": _col(r, "роль", "name", "ответственный", "имя"),
                      "pool": _col(r, "pool_id"), "parent": _col(r, "родитель", "parent"),
                      "kind": _col(r, "тип дорожки", "вид дорожки", "kind").lower()}

    nodes = {}
    order = []
    for r in nrows:
        nid = _col(r, "node_id", "id")
        if not nid:
            continue
        nodes[nid] = {
            "id": nid, "type": _col(r, "тип узла", "type").lower(),
            "subtype": _col(r, "подтип", "subtype").lower(),
            "name": _col(r, "шаг", "имя", "название", "name"),
            "lane": _col(r, "lane_id", "дорожка"),
            "trigger": _col(r, "триггер", "trigger").lower() or "none",
            "system": _col(r, "система", "system"),
            "prb": (re.search(r"PRB-\d+", _col(r, "проблемы", "проблема")) or [None])[0]
                   if re.search(r"PRB-\d+", _col(r, "проблемы", "проблема")) else "",
            "result": _col(r, "результат", "result", "выход"),
            "input": _col(r, "вход", "input", "входные данные"),
            "documentation": _col(r, "documentation", "документация"),
            "attached_to": _col(r, "attached_to"), "called": _col(r, "called"),
            "row": len(order) + 1,
        }
        order.append(nid)

    flows, msgflows = [], []
    for r in frows:
        fid = _col(r, "flow_id", "id")
        src, tgt = _col(r, "источник", "src", "from"), _col(r, "цель", "tgt", "to")
        if not (fid and src and tgt):
            continue
        kind = _col(r, "вид", "kind").lower() or "seq"
        rec = {"id": fid, "src": src, "tgt": tgt,
               "cond": _col(r, "условие", "condition"),
               "default": _truthy(_col(r, "default", "иначе")),
               "msg": _col(r, "сообщение", "message")}
        (msgflows if kind in ("msg", "message", "сообщение") else flows).append(rec)

    # пул по умолчанию
    if not pools:
        pools["P_main"] = {"id": "P_main", "name": "Процесс", "kind": "internal",
                           "blackbox": False, "lanes": []}
    # Legacy mode synthesizes a technical default lane.  Corporate 2.1 keeps
    # the white-box Process lane-less and parents nodes directly to the pool.
    if not lanes and not lane_less:
        main = next(p for p in pools if not pools[p]["blackbox"])
        for nid in order:
            lid = nodes[nid]["lane"] or "L_default"
            nodes[nid]["lane"] = lid
            if lid not in lanes:
                lanes[lid] = {"id": lid, "name": lid, "pool": main, "parent": "", "kind": ""}
    # привязать дорожки к пулам
    main_pool = next((p for p in pools if not pools[p]["blackbox"]), list(pools)[0])
    for lid, ln in lanes.items():
        if not ln["pool"]:
            ln["pool"] = main_pool
        pools.setdefault(ln["pool"], {"id": ln["pool"], "name": ln["pool"], "kind": "internal",
                                      "blackbox": False, "lanes": []})
        pools[ln["pool"]]["lanes"].append(lid)
    # пул узла = пул его дорожки; вид дорожки (system/…) запоминаем на узле для infer_tags
    for nid in order:
        lid = nodes[nid]["lane"]
        nodes[nid]["pool"] = lanes[lid]["pool"] if lid in lanes else main_pool
        nodes[nid]["lane_kind"] = lanes[lid].get("kind", "") if lid in lanes else ""
    return pools, lanes, nodes, order, flows, msgflows


# ════════════════════════════════════════════════════════════════════════════
#  ② INFER теги + авто-вставка шлюзов
# ════════════════════════════════════════════════════════════════════════════
def infer_tags(nodes):
    for n in nodes.values():
        t, st = n["type"], n["subtype"]
        if t == "start":
            n["tag"] = "startEvent"
        elif t == "end":
            n["tag"] = "endEvent"
        elif t == "intermediate":
            n["tag"] = "intermediateThrowEvent" if st == "throw" else "intermediateCatchEvent"
        elif t == "boundary":
            n["tag"] = "boundaryEvent"
        elif t == "gateway":
            n["tag"] = GW_TAGS.get(st, "exclusiveGateway")
        elif t in ("subprocess", "callactivity"):
            n["tag"] = "subProcess" if t == "subprocess" else "callActivity"
        elif t == "task":
            if st in TASK_TAGS and st:
                n["tag"] = TASK_TAGS[st]
            else:  # auto-вывод по полям (подтип пуст)
                if n.get("lane_kind") == "system":
                    n["tag"] = "serviceTask"   # системная дорожка → действие системы без человека
                elif n["system"]:
                    n["tag"] = "userTask"       # человек + система (ручной ввод)
                else:
                    n["tag"] = "manualTask"     # человек без системы
        else:
            raise BpmnError("узел %s: неизвестный «Тип узла» = «%s»" % (n["id"], t))
        n["is_gateway"] = (t == "gateway")
        n["is_event"] = t in ("start", "end", "intermediate", "boundary")


def auto_gateways(nodes, order, flows):
    """Insert only a technical split after a non-gateway with multiple exits.

    A merge before a node with multiple inputs is not inserted: convergence may
    be represented directly by incoming Sequence Flows unless the canonical
    business model explicitly contains a merge gateway.
    """
    def ins(nid):
        return [f for f in flows if f["tgt"] == nid]

    def outs(nid):
        return [f for f in flows if f["src"] == nid]

    new_nodes = []
    # SPLIT: не-шлюз с ≥2 выходами
    for nid in list(order):
        n = nodes[nid]
        o_ = outs(nid)
        if len(o_) >= 2 and not n["is_gateway"]:
            gid = "GWs_%s" % nid
            g = {"id": gid, "type": "gateway", "subtype": "xor", "tag": "exclusiveGateway",
                 "name": "", "lane": n["lane"], "pool": n["pool"], "trigger": "none",
                 "system": "", "prb": "", "is_gateway": True, "is_event": False,
                 "attached_to": "", "called": "", "row": n["row"]}
            nodes[gid] = g
            new_nodes.append(gid)
            for f in o_:
                f["src"] = gid  # условия переезжают на шлюз вместе с ребром
            flows.append({"id": "%s_out" % gid, "src": nid, "tgt": gid,
                          "cond": "", "default": False, "msg": ""})
    order.extend(new_nodes)
    return new_nodes


def build_data(nodes, order):
    """Объекты системы, связанные с действиями (методика заказчика): Результат → выход, Вход → вход.
    Метка = текст из реестра («Заявка (Создание)»); префикс DS:/Система: → хранилище (цилиндр),
    иначе документ (data object). Объект ассоциируется с задачей пунктиром."""
    data = []
    for nid in order:
        n = nodes[nid]
        if not (n["tag"].endswith("Task") or n["tag"] in ("subProcess", "callActivity")):
            continue
        for field, direction in (("input", "in"), ("result", "out")):
            raw_value = (n.get(field) or "").strip()
            if not raw_value:
                continue
            values = [value.strip() for value in raw_value.split(";;") if value.strip()]
            for index, val in enumerate(values, 1):
            # опц. маркер инструмента-«дома» артефакта (теневое ИТ): "Реестр @Excel", "Письмо @Почта"
                home = ""
                m = re.search(r"(?:^|\s)@\s*([^@]+?)\s*$", val)
                if m:
                    home = m.group(1).strip()
                    val = val[:m.start()].strip()
                kind = "store" if val.lower().startswith(("ds:", "система:", "бд:")) else "object"
                name = re.sub(r"^(DS:|DOC:|Система:|БД:)\s*", "", val, flags=re.I).strip()
                suffix = "%s_%s_%s" % (nid, direction, index)
                data.append({"id": "Data_%s" % suffix, "ref": "DataRef_%s" % suffix,
                             "assoc": "Assoc_%s" % suffix, "prop": "Prop_%s" % suffix,
                             "task": nid, "dir": direction, "kind": kind, "name": name, "home": home})
    return data


# ════════════════════════════════════════════════════════════════════════════
#  ③ VALIDATE — слой A (методология BPMN, до записи XML)
# ════════════════════════════════════════════════════════════════════════════
def validate(pools, lanes, nodes, order, flows, msgflows):
    err = []
    ids = set(nodes) | set(pools)

    for nid in order:
        node = nodes[nid]
        if node["tag"].endswith("Task") or node["tag"] in ("subProcess", "callActivity"):
            if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>|&(?:lt|gt|nbsp);", node.get("name") or "", re.I):
                err.append("узел %s: HTML запрещён в BPMN name" % nid)

    def ins(nid):
        return [f for f in flows if f["tgt"] == nid]

    def outs(nid):
        return [f for f in flows if f["src"] == nid]

    # ссылочная целостность
    seen = set()
    for nid in order:
        if nid in seen:
            err.append("дубликат node_id «%s»" % nid)
        seen.add(nid)
        if nodes[nid]["lane"] and nodes[nid]["lane"] not in lanes:
            err.append("узел %s: дорожка «%s» не объявлена" % (nid, nodes[nid]["lane"]))
    for f in flows + msgflows:
        if f["src"] not in ids:
            err.append("поток %s: источник «%s» не найден" % (f["id"], f["src"]))
        if f["tgt"] not in ids:
            err.append("поток %s: цель «%s» не найдена" % (f["id"], f["tgt"]))

    # события start/end по пулам
    for pid, p in pools.items():
        if p["blackbox"]:
            continue
        pnodes = [n for n in nodes.values() if n["pool"] == pid]
        starts = [n for n in pnodes if n["type"] == "start"]
        ends = [n for n in pnodes if n["type"] == "end"]
        none_starts = [n for n in starts if n["trigger"] == "none"]
        if not starts:
            err.append("пул %s: нет стартового события" % pid)
        if not ends:
            err.append("пул %s: нет конечного события" % pid)
        if len(none_starts) > 1:
            err.append("пул %s: более одного none-start события (%d)" % (pid, len(none_starts)))

    for nid in order:
        n = nodes[nid]
        i_, o_ = ins(nid), outs(nid)
        if n["type"] == "start" and (len(i_) != 0 or len(o_) != 1):
            err.append("старт %s: должно быть 0 входов и 1 выход (есть %d/%d)" % (nid, len(i_), len(o_)))
        if n["type"] == "end" and (len(i_) < 1 or len(o_) != 0):
            err.append("конец %s: должно быть ≥1 вход и 0 выходов (есть %d/%d)" % (nid, len(i_), len(o_)))
        if n["is_gateway"]:
            if len(i_) <= 1 and len(o_) <= 1:
                err.append("шлюз %s — линейный (1 вход/1 выход): это Activity, а не шлюз "
                           "(в реестре сделай его задачей или добавь ветви)" % nid)
            # XOR/OR split: условия на ветвях + ровно один default
            if n["subtype"] in ("xor", "or") and len(o_) >= 2:
                defaults = [f for f in o_ if f["default"]]
                noncond = [f for f in o_ if not f["cond"] and not f["default"]]
                if len(defaults) > 1:
                    err.append("шлюз %s: более одной ветви default" % nid)
                if noncond:
                    err.append("шлюз %s: ветви без условия и без default: %s "
                               "(XOR/OR требует условие на каждой ветви, кроме одной default)"
                               % (nid, ", ".join(f["id"] for f in noncond)))
            if n["subtype"] == "and":
                if any(f["cond"] or f["default"] for f in o_):
                    err.append("шлюз %s (AND): у параллельных ветвей не должно быть условий/default" % nid)
        elif not n["is_event"] or n["type"] == "intermediate":
            # задачи/intermediate: неявный split запрещён. Несколько входов
            # допустимы без технического merge; явный merge нужен только если
            # он несёт бизнес-семантику синхронизации/выбора.
            if len(o_) >= 2:
                err.append("узел %s: ≥2 исходящих без шлюза (неявный split) — нужен gateway" % nid)

    # потоки: seq внутри пула, msg между пулами
    for f in flows:
        sp = nodes.get(f["src"], {}).get("pool")
        tp = nodes.get(f["tgt"], {}).get("pool")
        if sp and tp and sp != tp:
            err.append("seq-поток %s пересекает границу пула (%s→%s) — между пулами только messageFlow"
                       % (f["id"], sp, tp))
    for f in msgflows:
        s_pool = nodes[f["src"]]["pool"] if f["src"] in nodes else f["src"]
        t_pool = nodes[f["tgt"]]["pool"] if f["tgt"] in nodes else f["tgt"]
        if s_pool == t_pool:
            err.append("msg-поток %s в пределах одного пула (%s) — messageFlow только между пулами"
                       % (f["id"], s_pool))

    # достижимость от start / до end
    adj = {}
    for f in flows:
        adj.setdefault(f["src"], []).append(f["tgt"])
    starts = [nid for nid in order if nodes[nid]["type"] == "start"]
    reach = set()
    stack = list(starts)
    while stack:
        x = stack.pop()
        if x in reach:
            continue
        reach.add(x)
        stack.extend(adj.get(x, []))
    for nid in order:
        if nid not in reach:
            err.append("узел %s недостижим от стартового события" % nid)
    return err


# ════════════════════════════════════════════════════════════════════════════
#  ④ LAYOUT — слоистая (дорожка × topo-глубина)
# ════════════════════════════════════════════════════════════════════════════
def layout(pools, lanes, nodes, order, flows, msgflows, data=None):
    data = data or []
    succ = {}
    for f in flows:
        succ.setdefault(f["src"], []).append(f["tgt"])
    # back-edges по DFS от стартов
    starts = [nid for nid in order if nodes[nid]["type"] == "start"]
    back = set()
    state = {}

    def dfs(u):
        state[u] = 1
        for v in sorted(succ.get(u, [])):
            if state.get(v, 0) == 1:
                back.add((u, v))
            elif state.get(v, 0) == 0:
                dfs(v)
        state[u] = 2

    for s in starts:
        if state.get(s, 0) == 0:
            dfs(s)
    for nid in order:  # на случай отдельных компонент
        if state.get(nid, 0) == 0:
            dfs(nid)

    # longest-path layering без back-edges
    fwd = {}
    indeg = {nid: 0 for nid in order}
    for f in flows:
        if (f["src"], f["tgt"]) in back:
            continue
        fwd.setdefault(f["src"], []).append(f["tgt"])
        indeg[f["tgt"]] += 1
    layer = {nid: 0 for nid in order}
    from collections import deque
    q = deque(sorted([nid for nid in order if indeg[nid] == 0]))
    seen_topo = []
    ind = dict(indeg)
    while q:
        u = q.popleft()
        seen_topo.append(u)
        for v in sorted(fwd.get(u, [])):
            layer[v] = max(layer[v], layer[u] + 1)
            ind[v] -= 1
            if ind[v] == 0:
                q.append(v)
    # лейны по индексу
    lane_order = []
    for pid, p in pools.items():
        for lid in p["lanes"]:
            if lid not in lane_order:
                lane_order.append(lid)
    lane_y = {lid: i for i, lid in enumerate(lane_order)}

    # предки без back-edges + топо-порядок
    preds = {nid: [] for nid in order}
    for f in flows:
        if (f["src"], f["tgt"]) not in back and f["tgt"] in preds:
            preds[f["tgt"]].append(f["src"])
    topo = seen_topo + [nid for nid in order if nid not in seen_topo]

    # КОЛОНКА: топо-порядок, строго правее предков, УНИКАЛЬНА в паре (колонка, дорожка).
    # → узлы не накладываются (одна ячейка = один узел), поток идёт слева-направо,
    #   параллельные ветви в разных дорожках выравниваются по колонке.
    col = {}
    used_cell = set()
    for nid in topo:
        base = max([col[p] + 1 for p in preds[nid] if p in col] or [0])
        c = max(layer[nid], base)
        lane = nodes[nid]["lane"]
        while (c, lane) in used_cell:
            c += 1
        used_cell.add((c, lane))
        col[nid] = c

    content_x0 = POOL_X + LANE_LABEL_W + 55
    for nid in order:
        n = nodes[nid]
        cx = content_x0 + col[nid] * COL_W
        li = lane_y.get(n["lane"], 0)
        cy = POOL_Y + li * LANE_H + LANE_H // 2
        if n["is_event"]:
            w = h = EVENT_D
        elif n["is_gateway"]:
            w = h = GW
        else:
            w, h = TASK_W, TASK_H
        n.update(x=cx - w // 2, y=cy - h // 2, w=w, h=h, cx=cx, cy=cy, layer=col[nid], lane_i=li)

    # bounds внутреннего пула
    int_pools = [pid for pid, p in pools.items() if not p["blackbox"]]
    maxcol = max((n["layer"] for n in nodes.values()), default=0)
    pool_w = (content_x0 - POOL_X) + (maxcol + 1) * COL_W + 40
    for pid in int_pools:
        p = pools[pid]
        plw = [lid for lid in lane_order if lid in p["lanes"]]
        p["bounds"] = (POOL_X, POOL_Y, pool_w, max(1, len(plw)) * LANE_H)
        for lid in plw:
            li = lane_y[lid]
            lanes[lid]["bounds"] = (POOL_X + LANE_LABEL_W, POOL_Y + li * LANE_H,
                                    pool_w - LANE_LABEL_W, LANE_H)
    lanes_bottom = POOL_Y + len(lane_order) * LANE_H
    # Объекты системы — на ОТДЕЛЬНЫХ дорожках-артефактах пула, названных по системе действия
    # (артефакт = объект системы; без системы → «Вне системы»: Excel и пр.). Дорожки кладём
    # под дорожками-ролями; объекты в них — по X задачи; связь действие↔объект — орто-пунктир.
    data_h = 0
    if data:
        OW, OH, ART_H = 36, 50, 110

        def _art_sys(d):
            s = (nodes[d["task"]].get("system") or "").strip()
            s = re.sub(r"^(выполняется|пишет|пишется|читает|читается|формируется|регистрируется|"
                       r"хранится|создаётся|создается|ведётся|ведется)[^:]*:\s*", "", s, flags=re.I).strip()
            return s or "Вне системы"

        groups, okeys = {}, []
        for d in data:
            # дорожка артефакта — по «дому» инструмента (теневое ИТ), иначе по системе действия
            key = (nodes[d["task"]]["pool"], d.get("home") or _art_sys(d))
            if key not in groups:
                groups[key] = []
                okeys.append(key)
            groups[key].append(d)
        art_h_by_pool = {}
        y_cursor = lanes_bottom
        for (pid, sysname) in okeys:
            ly = y_cursor
            pools[pid].setdefault("artifact_lanes", []).append(
                (sysname, (POOL_X + LANE_LABEL_W, ly, pool_w - LANE_LABEL_W, ART_H)))
            art_h_by_pool[pid] = art_h_by_pool.get(pid, 0) + ART_H
            last_x = -10 ** 9
            for d in sorted(groups[(pid, sysname)], key=lambda z: (nodes[z["task"]]["cx"], z["dir"], z["task"])):
                tn = nodes[d["task"]]
                dx = max(tn["cx"], last_x + OW + 22)
                last_x = dx
                d["x"], d["y"], d["w"], d["h"] = int(dx - OW / 2), ly + (ART_H - OH) // 2, OW, OH
                d["cx"], d["cy"] = int(dx), ly + ART_H // 2
                tb = tn["y"] + tn["h"]
                midy = (tb + d["y"]) // 2
                if d["dir"] == "out":          # действие → объект
                    d["wp"] = [(tn["cx"], tb), (tn["cx"], midy), (d["cx"], midy), (d["cx"], d["y"])]
                else:                          # объект → действие
                    d["wp"] = [(d["cx"], d["y"]), (d["cx"], midy), (tn["cx"], midy), (tn["cx"], tb)]
            y_cursor += ART_H
        data_h = y_cursor - lanes_bottom
        # внутренние пулы расширяем на высоту их дорожек-артефактов
        for pid, p in pools.items():
            if not p["blackbox"] and "bounds" in p and art_h_by_pool.get(pid):
                x, y, w, h = p["bounds"]
                p["bounds"] = (x, y, w, h + art_h_by_pool[pid])
    # каналы под полосой данных для петель; внешние пулы — ещё ниже
    chan_base = lanes_bottom + (data_h + 22 if data_h else 14)
    back_list = sorted(back)
    back_chan = {bk: chan_base + i * 16 for i, bk in enumerate(back_list)}
    ext_y = chan_base + max(1, len(back_list)) * 16 + 18
    for pid, p in pools.items():
        if p["blackbox"]:
            p["bounds"] = (POOL_X, ext_y, pool_w, EXT_POOL_H)
            p["cx"], p["cy"] = POOL_X + pool_w // 2, ext_y + EXT_POOL_H // 2
            ext_y += EXT_POOL_H + 30

    # порты: несколько входящих/исходящих рёбер распределяем по стороне узла
    out_e = {nid: [] for nid in order}
    in_e = {nid: [] for nid in order}
    for f in flows:
        out_e[f["src"]].append(f)
        in_e[f["tgt"]].append(f)
    for nid in order:
        out_e[nid].sort(key=lambda f: (nodes[f["tgt"]]["lane_i"], nodes[f["tgt"]].get("layer", 0), f["id"]))
        in_e[nid].sort(key=lambda f: (nodes[f["src"]]["lane_i"], nodes[f["src"]].get("layer", 0), f["id"]))

    for f in flows:
        s, t = nodes[f["src"]], nodes[f["tgt"]]
        oi, oc = out_e[f["src"]].index(f), len(out_e[f["src"]])
        ii, ic = in_e[f["tgt"]].index(f), len(in_e[f["tgt"]])
        if (f["src"], f["tgt"]) in back:
            f["wp"] = _route_back(s, t, oi, oc, ii, ic, back_chan[(f["src"], f["tgt"])])
        else:
            f["wp"] = _route_fwd(s, t, oi, oc, ii, ic)
    for f in msgflows:
        s = nodes.get(f["src"])
        t = nodes.get(f["tgt"])
        if s is None:  # из пула в узел — вертикаль в координате X узла
            sp = pools[f["src"]]
            f["wp"] = [(t["cx"], sp["bounds"][1]), (t["cx"], t["y"] + t["h"])]
        elif t is None:  # из узла в пул — вертикаль в координате X узла
            tp = pools[f["tgt"]]
            f["wp"] = [(s["cx"], s["y"] + s["h"]), (s["cx"], tp["bounds"][1])]
        else:
            f["wp"] = _route_fwd(s, t, 0, 1, 0, 1)
    return back


def _port(node, side, i, count):
    """Координата порта на стороне узла (right/left → по высоте; bottom → по ширине)."""
    if side in ("right", "left"):
        return node["y"] + int(round(node["h"] * (i + 1) / (count + 1)))
    return node["x"] + int(round(node["w"] * (i + 1) / (count + 1)))


def _route_fwd(s, t, oi, oc, ii, ic):
    """Прямое ребро. Горизонтальный «ствол» — в чистой марже дорожки ИСТОЧНИКА (над/под узлами,
    смотря куда цель), вертикали — в зазорах между колонками. Так ребро не идёт поверх узлов
    между источником и целью; веер из шлюза разведён по портам (oi) и стволам."""
    sy = _port(s, "right", oi, oc)
    ty = _port(t, "left", ii, ic)
    sx, tx = s["x"] + s["w"], t["x"]
    # выровнены и в соседней колонке без промежутка — прямая
    if abs(sy - ty) <= 2 and (t["x"] - sx) <= COL_W:
        return [(sx, sy), (tx, ty)]
    # ствол в марже дорожки источника: сверху, если цель выше; иначе снизу
    half = s["h"] // 2
    if t.get("lane_i", 0) < s.get("lane_i", 0):
        trunk = s["cy"] - half - 16 - oi * 11
    else:
        trunk = s["cy"] + half + 16 + oi * 11
    dropx = sx + 12 + oi * 15                     # выход вправо (веер разведён), затем в ствол
    gapx = t["x"] - 14 - ii * 10                  # вертикаль в зазоре слева от цели
    if gapx <= dropx + 8:
        gapx = dropx + 12
    return [(sx, sy), (dropx, sy), (dropx, trunk), (gapx, trunk), (gapx, ty), (tx, ty)]


def _route_back(s, t, oi, oc, ii, ic, chan_y):
    """Петля-возврат: низ источника → (вертикали в зазорах колонок) → канал под дорожками → цель.
    Вертикали в зазорах, чтобы петля не пересекала узлы под источником/над целью."""
    sx = _port(s, "bottom", oi, oc)
    tx = _port(t, "bottom", ii, ic)
    sb, tb = s["y"] + s["h"], t["y"] + t["h"]
    sgap = s["x"] + s["w"] + 16 + oi * 7         # зазор справа от источника
    tgap = t["x"] - 16 - ii * 7                  # зазор слева от цели
    return [(sx, sb), (sx, sb + 10), (sgap, sb + 10), (sgap, chan_y),
            (tgap, chan_y), (tgap, tb + 10), (tx, tb + 10), (tx, tb)]


# ════════════════════════════════════════════════════════════════════════════
#  ⑤ EMIT BPMN 2.0 XML (+ DI)
# ════════════════════════════════════════════════════════════════════════════
def emit_xml(meta, pools, lanes, nodes, order, flows, msgflows, data, src_basename, sig, mode, lock=False):
    data_by_task = {}
    for d in data:
        data_by_task.setdefault(d["task"], []).append(d)
    P = ['<?xml version="1.0" encoding="UTF-8"?>']
    # SOURCE/SRCPATHS/LOCKED-шапку добавляет вызывающий cmd_bpmn (единообразно, с дрейф-чеком)
    P.append('<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
             'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
             'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
             'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
             'xmlns:bioc="http://bpmn.io/schema/bpmn/biocolor/1.0" '
             'id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">')

    for d in data:
        if d["kind"] == "store":
            P.append('  <bpmn:dataStore id="%s" name="%s" />' % (d["id"], xe(d["name"])))

    int_pools = [pid for pid in pools if not pools[pid]["blackbox"]]
    # ── collaboration ──
    P.append('  <bpmn:collaboration id="Collaboration_1">')
    for pid, p in pools.items():
        if p["blackbox"]:
            P.append('    <bpmn:participant id="%s" name="%s" />' % (pid, xe(p["name"])))
        else:
            P.append('    <bpmn:participant id="%s" name="%s" processRef="Process_%s" />'
                     % (pid, xe(p["name"]), pid))
    for f in msgflows:
        P.append('    <bpmn:messageFlow id="%s" name="%s" sourceRef="%s" targetRef="%s" />'
                 % (f["id"], xe(f["msg"]), f["src"], f["tgt"]))
    P.append('  </bpmn:collaboration>')

    # ── process на внутренний пул ──
    for pid in int_pools:
        p = pools[pid]
        pnodes = [nid for nid in order if nodes[nid]["pool"] == pid]
        pflows = [f for f in flows if nodes.get(f["src"], {}).get("pool") == pid]
        P.append('  <bpmn:process id="Process_%s" isExecutable="false">' % pid)
        if p["lanes"]:
            P.append('    <bpmn:laneSet id="LaneSet_%s">' % pid)
            for lid in p["lanes"]:
                P.append('      <bpmn:lane id="%s" name="%s">' % (lid, xe(lanes[lid]["name"])))
                for nid in pnodes:
                    if nodes[nid]["lane"] == lid:
                        P.append('        <bpmn:flowNodeRef>%s</bpmn:flowNodeRef>' % nid)
                P.append('      </bpmn:lane>')
            P.append('    </bpmn:laneSet>')
        for nid in pnodes:
            P.append(_node_xml(nodes[nid], flows, data_by_task.get(nid, [])))
        # объекты системы (data object/store) этого пула — связь с действиями
        for d in data:
            if nodes[d["task"]]["pool"] != pid:
                continue
            if d["kind"] == "store":
                P.append('    <bpmn:dataStoreReference id="%s" name="%s" dataStoreRef="%s" />'
                         % (d["ref"], xe(d["name"]), d["id"]))
            else:
                P.append('    <bpmn:dataObjectReference id="%s" name="%s" dataObjectRef="%s" />'
                         % (d["ref"], xe(d["name"]), d["id"]))
                P.append('    <bpmn:dataObject id="%s" />' % d["id"])
        for f in pflows:
            cond = ""
            if f["cond"]:
                cond = ('><bpmn:conditionExpression xsi:type="bpmn:tFormalExpression" '
                        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">%s'
                        '</bpmn:conditionExpression></bpmn:sequenceFlow>' % xe(f["cond"]))
            P.append('    <bpmn:sequenceFlow id="%s" sourceRef="%s" targetRef="%s"%s%s'
                     % (f["id"], f["src"], f["tgt"],
                        (' name="%s"' % xe(f["cond"])) if f["cond"] else "",
                        cond if cond else " />"))
        # default-атрибут на шлюзах
        P.append('  </bpmn:process>')

    # ── DI ──
    P.append('  <bpmndi:BPMNDiagram id="BPMNDiagram_1">')
    P.append('    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Collaboration_1">')
    for pid, p in pools.items():
        b = p["bounds"]
        P.append('      <bpmndi:BPMNShape id="%s_di" bpmnElement="%s" isHorizontal="true">' % (pid, pid))
        P.append('        <dc:Bounds x="%d" y="%d" width="%d" height="%d" />' % b)
        P.append('      </bpmndi:BPMNShape>')
    for lid, ln in lanes.items():
        if "bounds" not in ln:
            continue
        b = ln["bounds"]
        P.append('      <bpmndi:BPMNShape id="%s_di" bpmnElement="%s" isHorizontal="true">' % (lid, lid))
        P.append('        <dc:Bounds x="%d" y="%d" width="%d" height="%d" />' % b)
        P.append('      </bpmndi:BPMNShape>')
    for nid in order:
        n = nodes[nid]
        stroke, fill = TYPE_COLORS.get(n["tag"], (NEUTRAL, "#FFFFFF"))
        if n.get("prb"):
            stroke = ACCENT      # проблемный шаг — маджента-акцент перебивает цвет типа
        color = ' bioc:stroke="%s" bioc:fill="%s"' % (stroke, fill)
        marker = ' isMarkerVisible="true"' if n["is_gateway"] and n["subtype"] in ("xor", "", "or") else ''
        P.append('      <bpmndi:BPMNShape id="%s_di" bpmnElement="%s"%s%s>' % (nid, nid, marker, color))
        P.append('        <dc:Bounds x="%d" y="%d" width="%d" height="%d" />' % (n["x"], n["y"], n["w"], n["h"]))
        if n["is_event"] or n["is_gateway"]:
            P.append('        <bpmndi:BPMNLabel><dc:Bounds x="%d" y="%d" width="100" height="28" /></bpmndi:BPMNLabel>'
                     % (n["cx"] - 50, n["y"] + n["h"] + 5))
        P.append('      </bpmndi:BPMNShape>')
    # фигуры объектов системы (data object/store)
    for d in data:
        P.append('      <bpmndi:BPMNShape id="%s_di" bpmnElement="%s">' % (d["ref"], d["ref"]))
        P.append('        <dc:Bounds x="%d" y="%d" width="%d" height="%d" />' % (d["x"], d["y"], d["w"], d["h"]))
        P.append('        <bpmndi:BPMNLabel><dc:Bounds x="%d" y="%d" width="92" height="28" /></bpmndi:BPMNLabel>'
                 % (d["cx"] - 46, d["y"] + d["h"] + 2))
        P.append('      </bpmndi:BPMNShape>')
    for f in flows + msgflows:
        P.append('      <bpmndi:BPMNEdge id="%s_di" bpmnElement="%s">' % (f["id"], f["id"]))
        for (x, y) in f["wp"]:
            P.append('        <di:waypoint x="%d" y="%d" />' % (x, y))
        P.append('      </bpmndi:BPMNEdge>')
    # рёбра ассоциаций объект↔действие (пунктир)
    for d in data:
        P.append('      <bpmndi:BPMNEdge id="%s_di" bpmnElement="%s">' % (d["assoc"], d["assoc"]))
        for (x, y) in d["wp"]:
            P.append('        <di:waypoint x="%d" y="%d" />' % (x, y))
        P.append('      </bpmndi:BPMNEdge>')
    P.append('    </bpmndi:BPMNPlane>')
    P.append('  </bpmndi:BPMNDiagram>')
    P.append('</bpmn:definitions>')
    return "\n".join(P) + "\n"


def _node_xml(n, flows, data_list=()):
    inc = "".join('<bpmn:incoming>%s</bpmn:incoming>' % f["id"] for f in flows if f["tgt"] == n["id"])
    out = "".join('<bpmn:outgoing>%s</bpmn:outgoing>' % f["id"] for f in flows if f["src"] == n["id"])
    assoc = ""
    for d in data_list:
        if d["dir"] == "out":
            assoc += ('<bpmn:dataOutputAssociation id="%s"><bpmn:targetRef>%s</bpmn:targetRef>'
                      '</bpmn:dataOutputAssociation>' % (d["assoc"], d["ref"]))
        else:
            assoc += ('<bpmn:property id="%s" name="__targetRef_placeholder" />'
                      '<bpmn:dataInputAssociation id="%s"><bpmn:sourceRef>%s</bpmn:sourceRef>'
                      '<bpmn:targetRef>%s</bpmn:targetRef></bpmn:dataInputAssociation>'
                      % (d["prop"], d["assoc"], d["ref"], d["prop"]))
    name = n["name"]
    if n["tag"].endswith("Task") and n.get("system"):
        sysid = (re.search(r"SYS-\d+", n["system"]) or [None])[0]
        if re.search(r"SYS-\d+", n["system"]):
            name = "%s\n— %s" % (name, sysid)
    attrs = 'id="%s"' % n["id"]
    if name:
        attrs += ' name="%s"' % xe(name)
    # default-поток на XOR/OR-шлюзе
    if n["is_gateway"] and n["subtype"] in ("xor", "or"):
        dft = [f for f in flows if f["src"] == n["id"] and f["default"]]
        if dft:
            attrs += ' default="%s"' % dft[0]["id"]
    if n["tag"] == "callActivity" and n.get("called"):
        attrs += ' calledElement="%s"' % xe(n["called"])
    documentation = n.get("documentation") or ""
    body = (('<bpmn:documentation>%s</bpmn:documentation>' % xe(documentation))
            if documentation else "") + inc + out + assoc
    # eventDefinition по триггеру
    if n["is_event"] and n["trigger"] != "none" and n["trigger"] in EVENT_DEFS:
        body += '<bpmn:%s />' % EVENT_DEFS[n["trigger"]]
    return '    <bpmn:%s %s>%s</bpmn:%s>' % (n["tag"], attrs, body, n["tag"])


# ════════════════════════════════════════════════════════════════════════════
#  Оркестрация
# ════════════════════════════════════════════════════════════════════════════
def build_laid_out(path, mode):
    """Реестр-граф → модель ПОСЛЕ раскладки (общая для BPMN- и draw.io-эмиттеров):
    (meta, pools, lanes, nodes, order, flows, msgflows, data). Координаты узлов/рёбер/объектов
    уже посчитаны. Бросает BpmnError со списком проблем (вызывающий делает die)."""
    meta, tables = load_tables(path)
    lane_less = meta.get("profile") == "corporate_role_overlays_v2"
    pools, lanes, nodes, order, flows, msgflows = build_graph(tables, lane_less=lane_less)
    infer_tags(nodes)
    auto_gateways(nodes, order, flows)
    errors = validate(pools, lanes, nodes, order, flows, msgflows)
    if errors:
        raise BpmnError("валидация BPMN (слой A) — %d проблем:\n  - %s" % (len(errors), "\n  - ".join(errors)))
    data = build_data(nodes, order)
    layout(pools, lanes, nodes, order, flows, msgflows, data)
    return meta, pools, lanes, nodes, order, flows, msgflows, data


def generate(path, mode, sig, lock=False):
    """Реестр-граф → (BPMN 2.0 xml, meta)."""
    meta, pools, lanes, nodes, order, flows, msgflows, data = build_laid_out(path, mode)
    xml = emit_xml(meta, pools, lanes, nodes, order, flows, msgflows, data,
                   os.path.basename(path), sig, mode, lock)
    return xml, meta


if __name__ == "__main__":
    # автономный прогон: bpmn_graph.py <реестр.md> [out.bpmn]
    src = sys.argv[1]
    xml, meta = generate(src, "as-is", "test00")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + ".bpmn"
    with open(out, "w", encoding="utf-8") as f:
        f.write(xml)
    print("BPMN:", out)
