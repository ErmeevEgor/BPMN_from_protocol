#!/usr/bin/env python3
"""
Сборка BPMN Lite registry (markdown под контракт bpmn-diagrams) из
process_model.json + сборка сайдкар-метаданных для corporate-рендера.

Вход:  output/models/<process-id>-model.json
Выход:
  output/registries/<process-id>-registry.md          (контракт bpmn-diagrams)
  output/registries/<process-id>-render-meta.json      (для scripts/diagram_layout.py)

CRITICAL CORPORATE RULE (см. bpmn-from-protocol/SKILL.md,
reference/system-role-policy.md): Lane строится ТОЛЬКО из step.role.
step.system НИКОГДА не попадает в реестр bpmn-diagrams (ни как lane, ни как
"Система"-колонка задачи, ни как "Результат"/"Вход") — иначе bpmn-diagrams
своей штатной логикой создаст системную lane и системную artifact-зону
(см. TZ_DELTA_CORPORATE_BPMN.md, раздел 2, 9). System label и артефакты
рисуются ПОСЛЕ, в scripts/diagram_layout.py, строго под Task, по данным
render-meta.json.

Правила BPMN Lite (раздел 20 ТЗ): только Start/End Event, User/Manual/
Service/Send/Receive Task, XOR/AND/OR Gateway, Lane, Sequence/Message Flow,
опционально Data Object (здесь НЕ используется — артефакты рисует
diagram_layout.py).
"""
import argparse
import json
import sys
from pathlib import Path

TASK_TYPE_MAP = {
    "user_task": ("task", "user"),
    "manual_task": ("task", "manual"),
    "service_task": ("task", "service"),
    "send_task": ("task", "send"),
    "receive_task": ("task", "receive"),
    "script_task": ("task", "script"),
    "business_rule_task": ("task", "businessRule"),
    "call_activity": ("callActivity", ""),
    "subprocess": ("subprocess", ""),
}


def esc(cell: str) -> str:
    return (cell or "").replace("|", "\\|").replace("\n", " ").strip()


def build_lanes(model: dict) -> list[str]:
    """Lane строится ТОЛЬКО из role. system никогда сюда не попадает (ROLE != SYSTEM)."""
    roles = []
    for s in model.get("steps", []):
        role = s.get("role")
        if not role:
            continue
        if role in roles:
            continue
        roles.append(role)
    return roles


def node_row(
    idx, node_id, node_type, subtype, name, lane_id, trigger,
    input_data="", result_data="", documentation="", called="",
):
    cells = [
        idx, node_id, node_type, subtype, esc(name), lane_id, trigger,
        "",  # system intentionally stays out of the renderer registry (ROLE != SYSTEM)
        esc(input_data), esc(result_data), "", "", esc(called), esc(documentation),
    ]
    return "| " + " | ".join(str(value) for value in cells) + " |"


def artifact_entries(items, direction: str) -> list[dict]:
    """Accept schema-2.0 ``kind`` and the legacy ``artifact_type`` alias."""
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"name": it, "artifact_type": None, "direction": direction})
        elif isinstance(it, dict) and it.get("name"):
            out.append({
                "name": it["name"],
                "artifact_type": it.get("kind") or it.get("artifact_type"),
                "direction": direction,
            })
    return out


DATA_STORE_KINDS = {"reference_data", "data", "location"}


def bpmn_artifact_values(items) -> str:
    """Serialize typed artifacts for BPMN XML without exposing them to draw.io lanes.

    ``render_diagram.py`` blanks these two registry columns for the raw draw.io
    pass. ``export_bpmn_xml.py`` keeps them, so the vendor exporter can create
    one typed BPMN data reference and association for every model artifact.
    """
    values = []
    for item in items or []:
        if isinstance(item, str):
            name, kind = item, None
        elif isinstance(item, dict):
            name, kind = str(item.get("name") or "").strip(), item.get("kind")
        else:
            continue
        if not name:
            continue
        prefix = "DS:" if kind in DATA_STORE_KINDS else "DOC:"
        values.append(f"{prefix} {name}")
    return " ;; ".join(values)


def render_contract(step: dict) -> dict:
    """Return the full semantic contract kept out of the visible Task name."""
    obj = step.get("business_object") if isinstance(step.get("business_object"), dict) else {}
    return {
        "action": str(step.get("action") or step.get("name") or "").strip(),
        "business_object": str(obj.get("name") or "").strip(),
        "interface_method": " / ".join(
        value for value in (
            str(step.get("system") or "").strip(),
            str(step.get("execution_channel") or "").strip(),
        ) if value
        ),
        "basis": str(step.get("basis") or "").strip() or None,
        "observable_result": str(step.get("observable_result") or "").strip() or None,
        "verification_criterion": str(step.get("verification_criterion") or "").strip() or None,
        "knowledge_status": step.get("knowledge_status"),
    }


def render_contract_documentation(step: dict) -> str:
    """Deterministic plain-text representation exported as bpmn:documentation."""
    contract = render_contract(step)
    labels = (
        ("Действие", "action"), ("Объект", "business_object"),
        ("Интерфейс/способ", "interface_method"), ("Основание", "basis"),
        ("Наблюдаемый результат", "observable_result"),
        ("Критерий проверки", "verification_criterion"),
        ("Статус знания", "knowledge_status"),
    )
    return "; ".join(f"{label}: {contract.get(key) or 'OPEN'}" for label, key in labels)


def build_legacy_registry_and_meta(model: dict) -> tuple[str, dict]:
    title = model.get("process_name", model.get("process_id", "Процесс"))
    mode = "to-be" if model.get("process_type", "TO-BE").upper() == "TO-BE" else "as-is"

    steps = model.get("steps", [])
    gateways = model.get("gateways", [])
    start_id = model.get("start")

    roles = build_lanes(model)
    lane_ids = {role: f"L{idx+1}" for idx, role in enumerate(roles)}

    nodes_by_id = {s["id"]: s for s in steps}
    for g in gateways:
        nodes_by_id[g["id"]] = g
    gateway_ids = {g["id"] for g in gateways}

    lines = []
    lines.append("---")
    lines.append(f"title: {title}")
    lines.append("process-graph: true")
    lines.append(f"diagram-mode: {mode}")
    lines.append("profile: strict")
    lines.append("---")
    lines.append("")

    if roles:
        lines.append("## Дорожки (роли)")
        lines.append("| lane_id | Роль / ответственный | pool_id | Родитель |")
        lines.append("|---|---|---|---|")
        for role in roles:
            lines.append(f"| {lane_ids[role]} | {esc(role)} |  |  |")
        lines.append("")

    lines.append("## Узлы")
    lines.append("| № | node_id | Тип узла | Подтип | Шаг / имя | lane_id | Триггер | Система | Вход | Результат | Проблемы | attached_to | called | documentation |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    meta_nodes = {}
    idx = 1
    for s in steps:
        node_id = s["id"]
        task_type = s.get("task_type", "user_task")
        role = s.get("role", "")
        lane_id = lane_ids.get(role, "")

        if node_id == start_id:
            node_type, subtype, trigger, kind = "start", "", "none", "event"
        elif task_type == "end_event":
            node_type, subtype, trigger, kind = "end", "", "none", "event"
        else:
            node_type, subtype = TASK_TYPE_MAP.get(task_type, ("task", "user"))
            trigger, kind = "", "task"

        # Система и артефакты НЕ идут в registry (bpmn-diagrams не должен их
        # видеть) — только в render-meta.json для diagram_layout.py.
        display_name = s.get("name", "")
        documentation = render_contract_documentation(s) if kind == "task" else ""
        lines.append(node_row(
            idx, node_id, node_type, subtype, display_name, lane_id, trigger,
            input_data=bpmn_artifact_values(s.get("inputs")),
            result_data=bpmn_artifact_values(s.get("outputs")),
            documentation=documentation, called=s.get("called_process_id", ""),
        ))
        idx += 1

        meta_nodes[node_id] = {
            "kind": kind,
            "lane_id": lane_id,
            "role": role,
            "system": s.get("system", "") or "",
            "artifacts": (
                artifact_entries(s.get("inputs"), "input")
                + artifact_entries(s.get("outputs"), "output")
            ),
            "functional_gap": s.get("functional_gap"),
            "render_contract": render_contract(s),
            "layout_band": s.get("layout_band", "happy"),
        }

    for g in gateways:
        role = g.get("role", "")
        lane_id = lane_ids.get(role, "")
        gw_role = g.get("gateway_role", "split")
        # Merge-шлюз никогда не получает имя/вопрос (раздел 22 дельта-ТЗ).
        display_name = "" if gw_role == "merge" else g.get("name", "")
        lines.append(node_row(idx, g["id"], "gateway", g.get("gateway_type", "xor"),
                               display_name, lane_id, ""))
        idx += 1
        meta_nodes[g["id"]] = {
            "kind": "gateway",
            "lane_id": lane_id,
            "role": role,
            "system": "",
            "artifacts": [],
            "gateway_role": gw_role,
            "layout_band": g.get("layout_band", "happy"),
        }

    lines.append("")
    lines.append("## Потоки")
    lines.append("| flow_id | Источник | Цель | Вид | Условие | default | Сообщение |")
    lines.append("|---|---|---|---|---|---|---|")

    flow_idx = 1
    errors = []
    for node_id, node in nodes_by_id.items():
        is_gateway = node_id in gateway_ids
        branches = node.get("branches")
        nxt = node.get("next", [])

        if is_gateway and node.get("gateway_type") in ("xor", "or"):
            if not branches:
                if len(nxt) >= 2:
                    errors.append(
                        f"Gateway '{node_id}' (xor/or) имеет {len(nxt)} веток, но нет 'branches' "
                        f"с условиями — заполните process_model.json"
                    )
                branches = [{"to": t, "condition": "", "default": (i == 0)} for i, t in enumerate(nxt)]
            for b in branches:
                # CORPORATE RULE (раздел 23 дельта-ТЗ): читаемая подпись на
                # КАЖДОЙ ветке XOR/OR, включая default — не блокировать
                # условие только потому что default=true.
                cond = esc(b.get("condition", ""))
                default = "да" if b.get("default") else ""
                lines.append(f"| F{flow_idx} | {node_id} | {b['to']} | seq | {cond} | {default} |  |")
                flow_idx += 1
        else:
            for t in nxt:
                lines.append(f"| F{flow_idx} | {node_id} | {t} | seq |  |  |  |")
                flow_idx += 1

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)

    render_meta = {
        "process_id": model.get("process_id"),
        "pool_id": "P_main",
        "lanes": [lane_ids[r] for r in roles],
        "lane_names": {lane_ids[r]: r for r in roles},
        "nodes": meta_nodes,
    }

    return "\n".join(lines) + "\n", render_meta


def _artifact_occurrences(step: dict) -> list[dict]:
    result = []
    for direction in ("input", "output"):
        for index, item in enumerate(step.get(f"{direction}s") or [], 1):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            occurrence = dict(item)
            occurrence.update({
                "artifact_instance_id": f"ART.{step['id']}.{direction.upper()}.{index}",
                "activity_id": step["id"], "direction": direction,
            })
            result.append(occurrence)
    return result


def build_corporate_registry_and_meta(model: dict) -> tuple[str, dict]:
    """Build the lane-less corporate render DTO and a readable audit registry."""
    title = model.get("process_name", model.get("process_id", "Процесс"))
    lines = [
        "---", f"title: {title}", "process-graph: true", "diagram-mode: to-be",
        "profile: corporate_role_overlays_v2", "---", "", "## Узлы",
        "| № | node_id | Тип узла | Подтип | Шаг / имя | lane_id | Триггер | Система | Вход | Результат | Проблемы | attached_to | called | documentation |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    nodes: dict[str, dict] = {}
    groups, role_overlays, system_overlays = [], [], []
    occurrences, associations = [], []
    start_id = model.get("start")
    idx = 1
    for step in model.get("steps") or []:
        sid, task_type = step["id"], step.get("task_type", "user_task")
        if sid == start_id or task_type == "start_event":
            node_type, subtype, trigger, kind = "start", "", "none", "event"
        elif task_type == "end_event":
            node_type, subtype, trigger, kind = "end", "", "none", "event"
        elif task_type == "intermediate_catch_event":
            node_type, subtype, trigger, kind = "intermediate", "catch", "message", "event"
        elif task_type == "intermediate_throw_event":
            node_type, subtype, trigger, kind = "intermediate", "throw", "message", "event"
        else:
            node_type, subtype = TASK_TYPE_MAP.get(task_type, ("task", "user"))
            trigger, kind = "", "activity"
        codes = [str(code) for code in step.get("display_codes") or [] if str(code).strip()]
        label = " / ".join(codes + [str(step.get("name") or "").strip()])
        lines.append(node_row(
            idx, sid, node_type, subtype, label, "", trigger,
            bpmn_artifact_values(step.get("inputs")), bpmn_artifact_values(step.get("outputs")),
            render_contract_documentation(step) if kind == "activity" else "",
            step.get("called_process_id", ""),
        ))
        idx += 1
        nodes[sid] = {
            "kind": kind, "task_type": task_type, "name": step.get("name", ""),
            "event_definition": step.get("event_definition", "none"),
            "display_codes": codes, "performer_kind": step.get("performer_kind"),
            "role": step.get("role"), "system": step.get("system"),
            "called_process_id": step.get("called_process_id"),
            "render_contract": render_contract(step),
        }
        if kind == "activity":
            groups.append({"id": f"GROUP.{sid}", "activity_id": sid})
            if step.get("performer_kind") == "human" and str(step.get("role") or "").strip():
                role_overlays.append({"id": f"ROLE.{sid}", "activity_id": sid, "text": step["role"]})
            system_overlays.append({"id": f"SYSTEM.{sid}", "activity_id": sid, "text": step.get("system") or "OPEN"})
            for occurrence in _artifact_occurrences(step):
                occurrences.append(occurrence)
                associations.append({
                    "id": f"ASSOC.{occurrence['artifact_instance_id']}",
                    "activity_id": sid, "artifact_instance_id": occurrence["artifact_instance_id"],
                    "direction": occurrence["direction"],
                })
    for gateway in model.get("gateways") or []:
        gid = gateway["id"]
        lines.append(node_row(idx, gid, "gateway", gateway.get("gateway_type", "xor"),
                              "" if gateway.get("gateway_role") == "merge" else gateway.get("name", ""), "", ""))
        idx += 1
        nodes[gid] = {
            "kind": "gateway", "gateway_type": gateway.get("gateway_type", "xor"),
            "gateway_role": gateway.get("gateway_role", "split"),
            "paired_gateway_id": gateway.get("paired_gateway_id"),
            "name": gateway.get("name", ""),
        }
    by_id = {**{s["id"]: s for s in model.get("steps") or []},
             **{g["id"]: g for g in model.get("gateways") or []}}
    seq, flow_index = [], 1
    for source, node in by_id.items():
        branches = {str(b.get("to")): b for b in node.get("branches") or [] if isinstance(b, dict)}
        for target in node.get("next") or []:
            branch = branches.get(str(target), {})
            seq.append({"id": f"F{flow_index}", "source": source, "target": target,
                        "condition": branch.get("condition"), "default": bool(branch.get("default"))})
            flow_index += 1
    lines += ["", "## Потоки", "| flow_id | Источник | Цель | Вид | Условие | default | Сообщение |",
              "|---|---|---|---|---|---|---|"]
    for flow in seq:
        lines.append(f"| {flow['id']} | {flow['source']} | {flow['target']} | seq | {esc(flow.get('condition'))} | {'да' if flow.get('default') else ''} |  |")
    meta = {
        "render_dto_version": "2.1", "render_profile": "corporate_role_overlays_v2",
        "process_id": model.get("process_id"), "pool_id": "PART_MAIN",
        "participants": model.get("participants") or [], "activity_groups": groups,
        "role_overlays": role_overlays, "system_overlays": system_overlays,
        "artifact_occurrences": occurrences, "data_associations": associations,
        "sequence_flows": seq, "message_flows": model.get("message_flows") or [],
        "annotations": model.get("annotations") or [], "nodes": nodes,
        "theme": model.get("theme") or {}, "internal_lanes": [],
    }
    return "\n".join(lines) + "\n", meta


def build_registry_and_meta(model: dict) -> tuple[str, dict]:
    if model.get("render_profile", "corporate_role_overlays_v2") == "legacy_role_lanes_v1":
        return build_legacy_registry_and_meta(model)
    return build_corporate_registry_and_meta(model)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_path", help="Путь к process_model.json")
    parser.add_argument("-o", "--out", help="Путь для .md реестра (по умолчанию output/registries/<id>-registry.md)")
    args = parser.parse_args()

    model_path = Path(args.model_path)
    model = json.loads(model_path.read_text(encoding="utf-8"))

    registry_md, render_meta = build_registry_and_meta(model)

    if args.out:
        out_path = Path(args.out)
    else:
        process_id = model.get("process_id", "process")
        out_path = Path("output/registries") / f"{process_id}-registry.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(registry_md, encoding="utf-8")

    meta_path = out_path.with_name(out_path.stem.replace("-registry", "") + "-render-meta.json")
    meta_path.write_text(json.dumps(render_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Реестр сохранён: {out_path}")
    print(f"Render-metadata сохранена: {meta_path}")


if __name__ == "__main__":
    main()
