#!/usr/bin/env python3
"""
Постобработка .drawio, сгенерированного bpmn-diagrams, под внутренний
корпоративный стандарт (TZ_DELTA_CORPORATE_BPMN.md).

bpmn-diagrams не поддерживает нужную нам вложенность и раскладку "из коробки"
(раздел 11 дельта-ТЗ) — этот модуль ПОСЛЕ штатной генерации:

1. Строит правильную иерархию Pool → Lane → Task/Gateway/Event (сейчас всё
   плоское, parent="1" у всех) — LANE_NOT_INSIDE_POOL / TASK_NOT_INSIDE_LANE.
2. Увеличивает высоту дорожек, чтобы под каждым Task поместились: подпись
   системы (шрифт 8) и артефакты (под задачей, не в отдельной lane).
3. Вставляет подпись системы под Task (центрировано, шрифт 8) —
   ТОЛЬКО если у задачи есть system в render-meta.json.
4. Вставляет артефакты под Task (под подписью системы, если она есть) с
   ассоциацией (не Sequence Flow) по каноническому kind (legacy
   artifact_type поддерживается только на входе).
5. Задаёт явные порты (exitX=1/exitY=0.5, entryX=0/entryY=0.5) на прямых
   ("вперёд") Sequence Flow — раздел 13, 41 дельта-ТЗ.
6. Удаляет технические auto-merge (GWm_*), перенаправляя входящие Sequence
   Flow прямо в бизнес-задачу. Merge остаётся только когда он явно задан в
   канонической модели.
7. Уплотняет горизонтальные колонки и прокладывает обратные связи по
   отдельным внешним коридорам.

Все размерные константы — раздел 15 дельта-ТЗ, централизованно ниже.

Вход:  сырой .drawio от bpmn-diagrams + <id>-render-meta.json (build_registry.py)
Выход: .drawio, соответствующий корпоративному стандарту.
"""
import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

# --- Централизованные routing/layout константы (раздел 15 дельта-ТЗ) ---
TASK_WIDTH = 150
TASK_HEIGHT = 80
TASK_TYPE_MARKER_SIZE = 16
LANE_HEIGHT_BASE = 145
LANE_PADDING = 30            # верхний отступ Task от верха дорожки (как у bpmn-diagrams)
SYSTEM_LABEL_GAP = 6
SYSTEM_LABEL_HEIGHT = 16
ARTIFACT_GAP = 10
ARTIFACT_WIDTH = 70
ARTIFACT_HEIGHT = 40
ARTIFACT_LABEL_HEIGHT = 28
ARTIFACT_H_SPACING = 10       # расстояние между несколькими артефактами одной задачи
CROSS_LANE_CORRIDOR_GAP = 0   # зарезервировано на будущее (явные коридоры)
RETURN_FLOW_OFFSET = 40       # зарезервировано на будущее (возвраты по внешнему контуру)
COLUMN_STEP = 180
CONTENT_LEFT = 70
RIGHT_MARGIN = 100
MAX_COLUMN_CLUSTER_DELTA = 90

LANE_HEIGHT_EXTRA = (
    SYSTEM_LABEL_GAP + SYSTEM_LABEL_HEIGHT +
    ARTIFACT_GAP + ARTIFACT_HEIGHT + ARTIFACT_LABEL_HEIGHT
)
LANE_HEIGHT_NEW = LANE_HEIGHT_BASE + LANE_HEIGHT_EXTRA

ARTIFACT_STYLES = {
    "system_document": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#FFFFFF;strokeColor=#595959;",
    "print_form": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#FFF7E0;strokeColor=#C9A227;",
    "external_document": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#F3F3F3;strokeColor=#8C8C8C;dashed=1;",
    "system_report": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#EAF1FB;strokeColor=#2E5C8A;",
    "reference_data": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#E8F5E9;strokeColor=#4F8A54;",
    "physical_object": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#FCE8E6;strokeColor=#A65A4E;",
    "location": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#F3E8FD;strokeColor=#7E57A5;",
    "message": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#E3F2FD;strokeColor=#3978A8;",
    "data": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#EEF2F6;strokeColor=#607D8B;",
    "other": "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#FFFFFF;strokeColor=#B0B0B0;dashed=1;",
    None: "shape=note;whiteSpace=wrap;html=1;size=12;fillColor=#FFFFFF;strokeColor=#B0B0B0;dashed=1;",
}

FONT8 = "fontSize=8;"


def load_xml(path: Path):
    tree = ET.parse(path)
    root_layer = tree.getroot().find(".//root")
    if root_layer is None:
        raise ValueError(f"Не найден <root> в {path}")
    return tree, root_layer


def geom(cell):
    return cell.find("mxGeometry")


def gf(g, attr, default=0.0):
    return float(g.get(attr, default))


def new_cell(cell_id, value, style, parent, x, y, w, h, vertex="1", extra_attrs=None):
    c = ET.Element("mxCell", {
        "id": cell_id, "value": value, "style": style,
        "vertex": vertex, "parent": parent,
    })
    if extra_attrs:
        c.attrib.update(extra_attrs)
    g = ET.SubElement(c, "mxGeometry", {
        "x": str(x), "y": str(y), "width": str(w), "height": str(h), "as": "geometry",
    })
    return c


def set_ports(cell, ports):
    style = cell.get("style", "")
    style = re.sub(r"(?:exit|entry)(?:X|Y|Dx|Dy)=[^;]*;", "", style)
    cell.set("style", style + ports)


def apply(drawio_path: Path, meta_path: Path, out_path: Path, warnings: list):
    tree, root = load_xml(drawio_path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    cells = {c.get("id"): c for c in root.findall("mxCell") if c.get("id")}
    pool_id = meta["pool_id"]
    lane_order = meta["lanes"]
    node_meta = meta["nodes"]

    def artifact_rows(info):
        return max(1, math.ceil(len(info.get("artifacts") or []) / 2))

    task_tail = SYSTEM_LABEL_GAP + SYSTEM_LABEL_HEIGHT + ARTIFACT_GAP
    artifact_row_height = ARTIFACT_HEIGHT + ARTIFACT_LABEL_HEIGHT + 8
    happy_rows = max(
        (artifact_rows(info) for info in node_meta.values()
         if info.get("kind") == "task" and info.get("layout_band", "happy") != "error"),
        default=1,
    )
    has_error_band = any(info.get("layout_band") == "error" for info in node_meta.values())
    error_rows = max(
        (artifact_rows(info) for info in node_meta.values()
         if info.get("kind") == "task" and info.get("layout_band") == "error"),
        default=1,
    )
    happy_band_y = LANE_PADDING
    happy_band_height = TASK_HEIGHT + task_tail + happy_rows * artifact_row_height
    error_band_y = happy_band_y + happy_band_height + 50
    error_band_height = TASK_HEIGHT + task_tail + error_rows * artifact_row_height
    lane_height_new = max(
        LANE_HEIGHT_NEW,
        (error_band_y + error_band_height + 25) if has_error_band
        else (happy_band_y + happy_band_height + 25),
    )

    # bpmn-diagrams исторически вставлял merge перед каждым узлом с несколькими
    # входами. Это техническая оптимизация графа, а не бизнес-семантика. Входы
    # безопасно направляются в единственную цель auto-merge, после чего сам
    # шлюз и его служебные дочерние ячейки удаляются.
    removed_auto_merges = []
    for merge_id, merge_cell in list(cells.items()):
        if (
            not merge_id.startswith("GWm_") or merge_cell.get("vertex") != "1"
            or merge_cell.get("parent") != "1"
        ):
            continue
        outgoing = [c for c in root.findall("mxCell") if c.get("edge") == "1" and c.get("source") == merge_id]
        incoming = [c for c in root.findall("mxCell") if c.get("edge") == "1" and c.get("target") == merge_id]
        if len(outgoing) != 1:
            warnings.append(
                f"AUTO_MERGE_NOT_REMOVED: '{merge_id}' имеет {len(outgoing)} исходящих связей"
            )
            continue
        target = outgoing[0].get("target")
        if not target:
            warnings.append(f"AUTO_MERGE_NOT_REMOVED: '{merge_id}' без целевой задачи")
            continue
        for edge in incoming:
            edge.set("target", target)
        to_remove = [outgoing[0], merge_cell]
        to_remove.extend(c for c in root.findall("mxCell") if c.get("parent") == merge_id)
        for cell in to_remove:
            if cell in list(root):
                root.remove(cell)
            cells.pop(cell.get("id"), None)
        removed_auto_merges.append(merge_id)
    if removed_auto_merges:
        warnings.append(
            "AUTO_MERGE_REMOVED: " + ", ".join(sorted(removed_auto_merges))
        )

    if pool_id not in cells:
        raise ValueError(f"Pool '{pool_id}' не найден в .drawio")
    pool_cell = cells[pool_id]
    pg = geom(pool_cell)
    pool_x, pool_y = gf(pg, "x"), gf(pg, "y")
    # Подпись пула (генератор ставит generic "Процесс") накладывается на подпись
    # первой дорожки визуально — для одиночного процесса пул без подписи не
    # теряет информацию (роль есть на каждой дорожке), убираем текст.
    pool_cell.set("value", "")

    # --- Снимок исходной геометрии дорожек (до изменения) ---
    orig_lane_geom = {}
    for lid in lane_order:
        if lid not in cells:
            continue
        lg = geom(cells[lid])
        orig_lane_geom[lid] = (gf(lg, "x"), gf(lg, "y"), gf(lg, "width"), gf(lg, "height"))

    # --- 1. Реструктурировать дорожки: parent=pool, новая высота, стек по порядку ---
    for i, lid in enumerate(lane_order):
        if lid not in cells:
            warnings.append(f"Lane '{lid}' объявлена в модели, но отсутствует в .drawio")
            continue
        lane_cell = cells[lid]
        lg = geom(lane_cell)
        orig_x, _orig_y, orig_w, _orig_h = orig_lane_geom[lid]
        lg.set("x", str(orig_x - pool_x))
        lg.set("y", str(i * lane_height_new))
        lg.set("height", str(lane_height_new))
        lane_cell.set("parent", pool_id)

    pg.set("height", str(lane_height_new * len(lane_order)))

    # --- helper: найти исходную дорожку по абсолютной Y (для авто-вставленных узлов) ---
    def find_lane_by_abs_y(y_abs):
        for lid, (lx, ly, lw, lh) in orig_lane_geom.items():
            if ly <= y_abs < ly + lh:
                return lid
        return None

    # --- 2. Реparent всех узлов модели (tasks/gateways/events) в свою дорожку ---
    reparented_node_ids = set()
    node_lane = {}
    for node_id, info in node_meta.items():
        cell = cells.get(node_id)
        if cell is None:
            continue
        lane_id = info.get("lane_id")
        if not lane_id or lane_id not in orig_lane_geom:
            continue
        g = geom(cell)
        if g is None:
            continue
        orig_x, orig_y = gf(g, "x"), gf(g, "y")
        lane_x, lane_y, _lw, _lh = orig_lane_geom[lane_id]
        g.set("x", str(orig_x - lane_x))
        g.set("y", str(orig_y - lane_y))
        cell.set("parent", lane_id)
        reparented_node_ids.add(node_id)
        node_lane[node_id] = lane_id

    # --- 3. Реparent прочих авто-вставленных узлов, которых нет в meta ---
    # Это ровно те vertex-ячейки верхнего уровня (parent="1"), которые не пул,
    # не дорожка и не дочерняя подячейка (иконка/лейбл) уже обработанного узла.
    known_child_suffixes = ("_i", "_m", "_l", "_in", "_out")
    for cid, cell in list(cells.items()):
        if cell.get("vertex") != "1":
            continue
        if cid == pool_id or cid in lane_order:
            continue
        if cid in reparented_node_ids:
            continue
        if cell.get("parent") != "1":
            continue  # уже не на корневом слое — дочерняя ячейка, пропустить
        if any(cid.endswith(suf) for suf in known_child_suffixes):
            continue
        g = geom(cell)
        if g is None:
            continue
        orig_x, orig_y = gf(g, "x"), gf(g, "y")
        lane_id = find_lane_by_abs_y(orig_y + gf(g, "height") / 2)
        if not lane_id:
            warnings.append(f"Авто-узел '{cid}' не удалось отнести к дорожке — оставлен как есть")
            continue
        lane_x, lane_y, _lw, _lh = orig_lane_geom[lane_id]
        g.set("x", str(orig_x - lane_x))
        g.set("y", str(orig_y - lane_y))
        cell.set("parent", lane_id)
        reparented_node_ids.add(cid)
        node_lane[cid] = lane_id

    # --- 4. Единые компактные колонки и короткая операционная подпись Task ---
    # Сохраняем относительный порядок автолейаута, но заменяем его неравномерные
    # и чрезмерные интервалы детерминированной сеткой. Узлы с почти одинаковым
    # исходным X остаются в одной колонке независимо от дорожки.
    positioned = []
    for node_id in reparented_node_ids:
        cell = cells.get(node_id)
        lane_id = node_lane.get(node_id)
        if cell is None or lane_id not in orig_lane_geom:
            continue
        g = geom(cell)
        if g is None:
            continue
        lane_x = orig_lane_geom[lane_id][0]
        positioned.append((lane_x + gf(g, "x"), node_id))
    clusters = []
    for x_abs, node_id in sorted(positioned):
        if not clusters or x_abs - clusters[-1][0] > MAX_COLUMN_CLUSTER_DELTA:
            clusters.append([x_abs, [node_id]])
        else:
            clusters[-1][1].append(node_id)
            clusters[-1][0] = sum(
                next(x for x, nid in positioned if nid == member) for member in clusters[-1][1]
            ) / len(clusters[-1][1])
    node_column = {
        node_id: index for index, (_x, ids) in enumerate(clusters) for node_id in ids
    }
    for node_id, column in node_column.items():
        cell = cells[node_id]
        g = geom(cell)
        if node_meta.get(node_id, {}).get("kind") == "task":
            g.set("width", str(TASK_WIDTH))
            g.set("height", str(TASK_HEIGHT))
            style = cell.get("style", "")
            style += "fontSize=9;align=center;verticalAlign=middle;spacing=6;spacingTop=10;whiteSpace=wrap;html=0;"
            cell.set("style", style)
            marker = cells.get(f"{node_id}_i")
            if marker is not None:
                marker_geometry = geom(marker)
                if marker_geometry is not None:
                    marker_geometry.set("x", "5")
                    marker_geometry.set("y", "4")
                    marker_geometry.set("width", str(TASK_TYPE_MARKER_SIZE))
                    marker_geometry.set("height", str(TASK_TYPE_MARKER_SIZE))
        width = gf(g, "width")
        height = gf(g, "height")
        band_y = error_band_y if node_meta.get(node_id, {}).get("layout_band") == "error" else happy_band_y
        g.set("y", str(band_y + (TASK_HEIGHT - height) / 2))
        # Центрируем события/шлюзы относительно ширины Task в колонке.
        g.set("x", str(CONTENT_LEFT + column * COLUMN_STEP + (TASK_WIDTH - width) / 2))

    content_width = CONTENT_LEFT + max(1, len(clusters)) * COLUMN_STEP + RIGHT_MARGIN
    pg.set("width", str(content_width))
    for lid in lane_order:
        if lid in cells:
            geom(cells[lid]).set("width", str(content_width))

    # --- 5. Подпись системы + артефакты под Task (лейн-относительные координаты) ---
    art_counter = 0
    for node_id, info in node_meta.items():
        if node_id not in reparented_node_ids:
            continue
        if info.get("kind") != "task":
            continue
        cell = cells[node_id]
        g = geom(cell)
        tx, ty, tw, th = gf(g, "x"), gf(g, "y"), gf(g, "width"), gf(g, "height")
        # ВАЖНО: не info["lane_id"] напрямую — для узлов с ROLE != SYSTEM
        # (role="", system-only step, см. normalize_process_model()) render-
        # meta.json может нести lane_id="" (build_registry.py не может
        # назначить дорожку без роли), и такой узел реально попадает в
        # дорожку только через fallback по Y-координате в шаге 3 выше
        # (node_lane). Использование info["lane_id"] здесь создавало
        # mxCell с parent="" (не прикреплённый к дереву графа) для подписи
        # системы/артефактов этого узла — валидный по структуре .drawio, но
        # draw.io CLI считал bounding box экспорта неправильно (PNG
        # схлопывался до нескольких десятков пикселей вместо полной схемы).
        lane_id = node_lane.get(node_id) or info.get("lane_id")

        cursor_y = ty + th + SYSTEM_LABEL_GAP
        system = (info.get("system") or "").strip()
        if system:
            label_id = f"{node_id}__system_label"
            lbl = new_cell(
                label_id, system,
                f"text;html=1;align=center;verticalAlign=top;whiteSpace=wrap;{FONT8}fontColor=#595959;",
                lane_id, tx, cursor_y, tw, SYSTEM_LABEL_HEIGHT,
            )
            root.append(lbl)
            cursor_y += SYSTEM_LABEL_HEIGHT + ARTIFACT_GAP
        else:
            cursor_y += ARTIFACT_GAP

        artifacts = info.get("artifacts") or []
        if artifacts:
            n = len(artifacts)
            columns = min(2, n)
            total_w = columns * ARTIFACT_WIDTH + (columns - 1) * ARTIFACT_H_SPACING
            start_x = tx + tw / 2 - total_w / 2
            for k, art in enumerate(artifacts):
                atype = art.get("kind") or art.get("artifact_type")
                if atype not in ARTIFACT_STYLES:
                    warnings.append(
                        f"ARTIFACT_TYPE_UNKNOWN: '{art.get('name')}' у задачи '{node_id}' "
                        f"без распознанного kind"
                    )
                style = ARTIFACT_STYLES.get(atype, ARTIFACT_STYLES[None])
                art_id = f"{node_id}__art{art_counter}"
                art_counter += 1
                row, column = divmod(k, 2)
                ax = start_x + column * (ARTIFACT_WIDTH + ARTIFACT_H_SPACING)
                ay = cursor_y + row * artifact_row_height
                art_cell = new_cell(
                    art_id, art.get("name", ""), style, lane_id,
                    ax, ay, ARTIFACT_WIDTH, ARTIFACT_HEIGHT,
                    extra_attrs={"style": style + f"verticalLabelPosition=bottom;verticalAlign=top;{FONT8}"},
                )
                root.append(art_cell)

                assoc_id = f"{node_id}__assoc{art_counter}"
                direction = art.get("direction", "output")
                assoc = ET.Element("mxCell", {
                    "id": assoc_id, "value": "", "edge": "1", "parent": pool_id,
                    "source": art_id if direction == "input" else node_id,
                    "target": node_id if direction == "input" else art_id,
                    "style": "endArrow=none;dashed=1;html=1;strokeColor=#9A9A9A;strokeWidth=1;rounded=0;",
                })
                ET.SubElement(assoc, "mxGeometry", {"relative": "1", "as": "geometry"})
                root.append(assoc)

    # --- 6. Явные порты + waypoints на Sequence Flow (раздел 13, 14, 41 дельта-ТЗ) ---
    # Абсолютные координаты узлов ПОСЛЕ реструктуризации дорожек (для расчёта
    # обхода препятствий на cross-lane и "перепрыгивающих" связях).
    lane_index = {lid: i for i, lid in enumerate(lane_order)}
    lane_abs_x = {}
    for lid in lane_order:
        if lid not in orig_lane_geom:
            continue
        orig_x, _oy, _ow, _oh = orig_lane_geom[lid]
        lane_abs_x[lid] = pool_x + (orig_x - pool_x)  # x дорожки не меняется

    node_abs = {}  # node_id -> (x0, x1, y0, y1, lane_id)
    for node_id in reparented_node_ids:
        cell = cells.get(node_id)
        lane_id = node_lane.get(node_id)
        if cell is None or lane_id not in lane_index:
            continue
        g = geom(cell)
        if g is None:
            continue
        rel_x, rel_y = gf(g, "x"), gf(g, "y")
        w, h = gf(g, "width"), gf(g, "height")
        abs_x0 = lane_abs_x[lane_id] + rel_x
        abs_x1 = abs_x0 + w
        abs_y0 = pool_y + lane_index[lane_id] * lane_height_new + rel_y
        node_abs[node_id] = (abs_x0, abs_x1, abs_y0, abs_y0 + h, lane_id)

    def obstacles_between(lane_id, x_from, x_to, exclude, layout_band):
        """Узлы той же дорожки, чей X-диапазон целиком лежит между x_from и x_to."""
        found = []
        for nid, (x0, x1, _y0, _y1, lid) in node_abs.items():
            if lid != lane_id or nid in exclude:
                continue
            if node_meta.get(nid, {}).get("layout_band", "happy") != layout_band:
                continue
            if x0 >= x_from and x1 <= x_to:
                found.append(nid)
        return found

    sequence_cells = [
        cell for cell in root.findall("mxCell")
        if cell.get("edge") == "1"
        and cell.get("source") in node_abs and cell.get("target") in node_abs
    ]
    forward_cells = [
        cell for cell in sequence_cells
        if node_abs[cell.get("target")][0] >= node_abs[cell.get("source")][0]
    ]
    backward_cells = [cell for cell in sequence_cells if cell not in forward_cells]
    forward_out = {}
    forward_in = {}
    backward_out = {}
    backward_in = {}
    for cell in forward_cells:
        forward_out.setdefault(cell.get("source"), []).append(cell)
        forward_in.setdefault(cell.get("target"), []).append(cell)
    for cell in backward_cells:
        backward_out.setdefault(cell.get("source"), []).append(cell)
        backward_in.setdefault(cell.get("target"), []).append(cell)
    for mapping in (forward_out, forward_in, backward_out, backward_in):
        for values in mapping.values():
            values.sort(key=lambda edge: edge.get("id") or "")

    backward_index = 0
    bypass_index = 0
    for cell in sequence_cells:
        src, tgt = cell.get("source"), cell.get("target")
        style = cell.get("style", "")
        src_x0, src_x1, src_y0, src_y1, src_lane = node_abs[src]
        tgt_x0, tgt_x1, tgt_y0, tgt_y1, tgt_lane = node_abs[tgt]
        forward = tgt_x0 >= src_x0

        if forward:
            outgoing = forward_out[src]
            incoming = forward_in[tgt]
            exit_y = (outgoing.index(cell) + 1) / (len(outgoing) + 1)
            entry_y = (incoming.index(cell) + 1) / (len(incoming) + 1)
            set_ports(
                cell,
                f"exitX=1;exitY={exit_y:.4f};exitDx=0;exitDy=0;"
                f"entryX=0;entryY={entry_y:.4f};entryDx=0;entryDy=0;",
            )
            src_port = (src_x1, src_y0 + (src_y1 - src_y0) * exit_y)
            tgt_port = (tgt_x0, tgt_y0 + (tgt_y1 - tgt_y0) * entry_y)
        else:
            outgoing = backward_out[src]
            incoming = backward_in[tgt]
            exit_x = (outgoing.index(cell) + 1) / (len(outgoing) + 1)
            entry_x = (incoming.index(cell) + 1) / (len(incoming) + 1)
            set_ports(
                cell,
                f"exitX={exit_x:.4f};exitY=1;exitDx=0;exitDy=0;"
                f"entryX={entry_x:.4f};entryY=1;entryDx=0;entryDy=0;",
            )
            src_port = (src_x0 + (src_x1 - src_x0) * exit_x, src_y1)
            tgt_port = (tgt_x0 + (tgt_x1 - tgt_x0) * entry_x, tgt_y1)

        # --- Обход препятствий: cross-lane ИЛИ same-lane "перепрыгивание" ---
        points = []
        if not forward:
            # Каждый возврат идёт в отдельном внешнем коридоре НИЖЕ пула и
            # входит в цель через собственный нижний порт. Общие участки
            # разных Sequence Flow конструктивно невозможны.
            backward_index += 1
            corridor_y = pool_y + lane_height_new * len(lane_order) + 24 + (backward_index - 1) * 16
            points = [
                (src_port[0], corridor_y),
                (tgt_port[0], corridor_y),
            ]
        elif src_lane != tgt_lane:
            # Cross-lane: идём через ВЕРХНИЙ зазор целевой дорожки (там нет
            # задач — они всегда начинаются на LANE_PADDING ниже верха), а не
            # по центру дорожки, где стоят другие узлы (раздел 14, 41 дельта-ТЗ).
            if tgt_x0 > src_x1:
                mid_x = (src_x1 + tgt_x0) / 2
                points = [(mid_x, src_port[1]), (mid_x, tgt_port[1])]
            else:
                bypass_index += 1
                top_margin_y = pool_y + lane_index[tgt_lane] * lane_height_new + 8 + (bypass_index % 3) * 6
                points = [(src_x1 + 15, src_port[1]), (src_x1 + 15, top_margin_y),
                          (tgt_x0 - 15, top_margin_y), (tgt_x0 - 15, tgt_port[1])]
        elif src_lane == tgt_lane:
            source_band = node_meta.get(src, {}).get("layout_band", "happy")
            blockers = obstacles_between(src_lane, src_x1, tgt_x0, exclude={src, tgt}, layout_band=source_band)
            band_changed = source_band != node_meta.get(tgt, {}).get("layout_band", "happy")
            if band_changed:
                # Drop in the gap immediately after the source, then continue
                # horizontally inside the target band. A mid-column drop can
                # cut through a happy-path Activity that occupies that column.
                drop_x = src_x1 + 15
                points = [(drop_x, src_port[1]), (drop_x, tgt_port[1])]
            elif blockers:
                # Выход/вход сверху: при плотной сетке 5px между колонками
                # недостаточно для jog справа от задачи, зато верхний зазор
                # дорожки свободен от Activity.
                bypass_index += 1
                lane_top_y = pool_y + lane_index[src_lane] * lane_height_new + 8 + (bypass_index % 3) * 6
                set_ports(
                    cell,
                    "exitX=0.5;exitY=0;exitDx=0;exitDy=0;entryX=0.5;entryY=0;entryDx=0;entryDy=0;",
                )
                points = [
                    ((src_x0 + src_x1) / 2, lane_top_y),
                    ((tgt_x0 + tgt_x1) / 2, lane_top_y),
                ]

        if points:
            if "jumpStyle=" not in cell.get("style", ""):
                cell.set("style", cell.get("style", "") + "jumpStyle=arc;jumpSize=6;")
            geom_el = cell.find("mxGeometry")
            if geom_el is None:
                geom_el = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
            for previous in list(geom_el.findall("Array")):
                geom_el.remove(previous)
            arr = ET.SubElement(geom_el, "Array", {"as": "points"})
            for px, py in points:
                ET.SubElement(arr, "mxPoint", {"x": str(px), "y": str(py)})

        # --- 6. Шрифт 8 для подписей условий на связях (раздел 18, 23 дельта-ТЗ) ---
        if (cell.get("value") or "").strip() and "fontSize=8" not in style:
            cell.set("style", cell.get("style", "") + FONT8)

    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drawio_in")
    parser.add_argument("render_meta")
    parser.add_argument("drawio_out")
    args = parser.parse_args()

    warnings = []
    apply(Path(args.drawio_in), Path(args.render_meta), Path(args.drawio_out), warnings)
    for w in warnings:
        print(f"WARNING: {w}")
    print(f"Layout post-processing done: {args.drawio_out}")


if __name__ == "__main__":
    main()
