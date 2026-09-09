#!/usr/bin/env python3
"""
Валидация process_model.json до рендера (раздел 33 ТЗ).

Проверяет:
- top-level обязательные поля (process_id/process_name/process_type/start) присутствуют;
- ID узлов уникальны;
- next ссылаются на существующие узлы;
- есть start;
- есть finish;
- нет случайных тупиков (любой не-конечный узел имеет next);
- ветви (next с несколькими элементами) объявлены как gateway с типом;
- каждый task имеет название и роль;
- assumption не выведен как confirmed (source_status согласован).

Defensive validation (задача "Newton -> process_model contract"): вход может
быть плохо сформированным JSON от LLM (не dict, steps/gateways не список,
узлы без "id" и т.д.) — validate() никогда не падает KeyError/TypeError на
такой вход, вместо этого возвращает обычную ошибку вида
"MISSING_FIELD: steps[0].id" / "MISSING_FIELD: process_id".

Печатает список проблем и завершает с кодом 1, если есть блокирующие ошибки.
При отсутствии ошибок печатает "OK" и завершается с кодом 0.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_contract import (  # noqa: E402
    ARTIFACT_KINDS,
    EVENT_TYPES,
    KNOWLEDGE_STATUSES,
    SCHEMA_VERSION,
    TASK_TYPES,
    has_legacy_fields,
    normalize_to_v2,
)

ALLOWED_SOURCE_STATUS = {"confirmed_protocol", "confirmed_erp", "open_question", "assumption"}
REQUIRED_TOP_LEVEL_FIELDS = ("process_id", "process_name", "process_type", "start")

# Раздел 5 задачи "Strengthen deterministic contract": каждый обычный step
# обязан иметь все эти поля (system — единственное исключение, ему явно
# разрешено быть "" / null). "next" считается присутствующим даже как []
# (см. STEP_LIST_FIELDS ниже) — отсутствие самого ключа всё равно ошибка.
REQUIRED_STEP_FIELDS = ("id", "name", "role", "task_type", "inputs", "outputs", "next", "source_status")
STEP_LIST_FIELDS = ("inputs", "outputs", "next")
V2_REQUIRED_STEP_FIELDS = (
    "id", "parent_id", "name", "role", "performer_kind", "system", "action",
    "business_object", "task_type", "inputs", "outputs", "control",
    "condition", "exception_refs", "knowledge_status", "source_refs", "next",
    "basis", "observable_result", "verification_criterion", "display_codes",
    "display_code_origin", "presentation", "artifact_assessment",
)
V2_LIST_FIELDS = ("inputs", "outputs", "control", "exception_refs", "source_refs", "next", "display_codes")
ALLOWED_TASK_TYPES = {
    "start_event", "end_event", "user_task", "manual_task",
    "service_task", "script_task", "business_rule_task", "send_task", "receive_task",
    "call_activity", "subprocess", "intermediate_catch_event", "intermediate_throw_event",
}
TASK_LABEL_MAX_CHARS = 72
TASK_LABEL_MAX_LINES = 3
FORBIDDEN_PLACEHOLDER_PATTERNS = (
    re.compile(r"Результат предыдущего шага", re.IGNORECASE),
    re.compile(r"Состояние объекта(?:\s+«[^»]+»)?\s+обновлено", re.IGNORECASE),
    re.compile(r"Шаг(?:\s+«[^»]+»)?\s+заверш[её]н", re.IGNORECASE),
)
HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>|&(?:lt|gt|nbsp|amp);", re.IGNORECASE)
MUTATING_ACTION_RE = re.compile(
    r"^(?:созда(?:ть|вать)|провес?ти|записа(?:ть|ывать)|установи(?:ть|вать)|"
    r"обнови(?:ть|лять)|измени(?:ть|ять)|закры(?:ть|вать)|зарегистрирова(?:ть|ывать)|"
    r"сформирова(?:ть|ывать)|назначи(?:ть|ать)|удали(?:ть|ять)|create|post|write|update|set|close)\b",
    re.IGNORECASE,
)


def validate(model) -> list[str]:
    errors = []
    warnings = []

    if not isinstance(model, dict):
        return ["MISSING_FIELD: <root> (process_model должен быть JSON-объектом)"]

    if model.get("schema_version") == "2.0":
        model = normalize_to_v2(model)

    if "visual_review" in model:
        errors.append(
            "MODEL_PRE_RENDER_REVIEW_FORBIDDEN: visual_review нельзя задавать в process_model до рендера"
        )

    for field in REQUIRED_TOP_LEVEL_FIELDS:
        value = model.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"MISSING_FIELD: {field}")
    start_id = model.get("start")
    is_v2 = model.get("schema_version") == SCHEMA_VERSION

    steps_raw = model.get("steps")
    if steps_raw is None:
        steps_raw = []
    elif not isinstance(steps_raw, list):
        errors.append("MISSING_FIELD: steps (должен быть списком)")
        steps_raw = []

    gateways_raw = model.get("gateways")
    if gateways_raw is None:
        gateways_raw = []
    elif not isinstance(gateways_raw, list):
        errors.append("MISSING_FIELD: gateways (должен быть списком)")
        gateways_raw = []

    if not steps_raw:
        errors.append("Нет ни одного шага (steps пуст)")
        return errors

    # Отфильтровываем узлы без валидного "id" — дальше по коду s["id"]/g["id"]
    # используется без .get(), поэтому такие узлы не должны попасть в steps/gateways.
    steps = []
    for i, s in enumerate(steps_raw):
        if not isinstance(s, dict):
            errors.append(f"MISSING_FIELD: steps[{i}] (должен быть объектом)")
            continue
        step_id = s.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            errors.append(f"MISSING_FIELD: steps[{i}].id")
            continue
        steps.append(s)

    gateways = []
    for i, g in enumerate(gateways_raw):
        if not isinstance(g, dict):
            errors.append(f"MISSING_FIELD: gateways[{i}] (должен быть объектом)")
            continue
        gateway_id = g.get("id")
        if not isinstance(gateway_id, str) or not gateway_id.strip():
            errors.append(f"MISSING_FIELD: gateways[{i}].id")
            continue
        gateways.append(g)

    step_ids = [s["id"] for s in steps]
    gateway_ids = [g["id"] for g in gateways]
    all_ids = step_ids + gateway_ids

    # ID uniqueness
    seen = set()
    for i in all_ids:
        if i in seen:
            errors.append(f"Дублирующийся ID: {i}")
        seen.add(i)

    # start present
    if not start_id:
        pass  # уже добавлено в errors выше (MISSING_FIELD: start)
    elif start_id not in all_ids:
        errors.append(f"start '{start_id}' не найден среди узлов")

    id_set = set(all_ids)

    # collect all nodes (steps + gateways) by id for next/type lookups
    nodes = {s["id"]: s for s in steps}
    for g in gateways:
        nodes[g["id"]] = g

    def safe_next(node) -> list:
        nxt = node.get("next")
        if nxt is None:
            return []
        if not isinstance(nxt, list):
            errors.append(f"MISSING_FIELD: '{node.get('id', '?')}'.next (должен быть списком)")
            return []
        return nxt

    has_end = False
    reachable = set()

    def visit(node_id):
        if node_id in reachable or node_id not in nodes:
            return
        reachable.add(node_id)
        node = nodes[node_id]
        for n in safe_next(node):
            if n not in id_set:
                errors.append(f"'{node_id}'.next ссылается на несуществующий узел '{n}'")
                continue
            visit(n)

    if start_id in id_set:
        visit(start_id)

    for node_id, node in nodes.items():
        nxt = safe_next(node)
        task_type = node.get("task_type") or node.get("gateway_type") or ""
        is_end = task_type == "end_event" or bool(node.get("is_end", False))
        if is_end:
            has_end = True
        if not nxt and not is_end:
            errors.append(f"Тупик: узел '{node_id}' не имеет next и не помечен как конец процесса")
        if len(nxt) > 1 and node_id not in gateway_ids:
            errors.append(
                f"Узел '{node_id}' имеет {len(nxt)} веток в next, но не объявлен как gateway "
                f"(добавьте его в gateways с типом xor/and/or)"
            )

    if not has_end:
        warnings.append("Не найден явный конечный узел (task_type=end_event) — проверьте завершение процесса")

    for i in id_set - reachable:
        warnings.append(f"Узел '{i}' недостижим от start")

    for s in steps:
        sid = s["id"]  # steps уже отфильтрован — id гарантированно валидный

        name = s.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"MISSING_FIELD: steps[id={sid}].name")
        elif HTML_RE.search(name):
            errors.append(f"TASK_NAME_HTML_FORBIDDEN: '{sid}' содержит HTML в BPMN name")
        elif s.get("task_type") not in ("start_event", "end_event"):
            if len(name) > TASK_LABEL_MAX_CHARS:
                errors.append(
                    f"TASK_LABEL_TOO_LONG: '{sid}' — {len(name)} символов, максимум {TASK_LABEL_MAX_CHARS}"
                )
            if len(name.splitlines()) > TASK_LABEL_MAX_LINES:
                errors.append(
                    f"TASK_LABEL_TOO_MANY_LINES: '{sid}' — максимум {TASK_LABEL_MAX_LINES} строки"
                )

        # task_type: обязателен И должен быть из фиксированного enum (раздел
        # 5 задачи — "не разрешать модели случайно пройти validator без
        # task_type"). Раньше отсутствие task_type тихо пропускало и проверку
        # роли ниже — теперь это всегда явная ошибка.
        task_type = s.get("task_type")
        if "task_type" not in s or task_type is None:
            errors.append(f"MISSING_FIELD: steps[id={sid}].task_type")
            task_type = None
        elif task_type not in ALLOWED_TASK_TYPES:
            errors.append(f"Узел '{sid}': недопустимый task_type '{task_type}' "
                           f"(разрешены: {', '.join(sorted(ALLOWED_TASK_TYPES))})")

        if is_v2:
            status = s.get("knowledge_status")
            if not status:
                errors.append(f"MISSING_FIELD: steps[id={sid}].knowledge_status")
                status = None
            elif status not in KNOWLEDGE_STATUSES:
                errors.append(f"Узел '{sid}': недопустимый knowledge_status '{status}'")
            if status in {"ASSUMPTION", "OPEN", "PROJECT_RECOMMENDATION"} and s.get("shown_as_confirmed"):
                errors.append(f"Узел '{sid}': {status} помечен как подтверждённый факт")
        else:
            status = s.get("source_status")
            if "source_status" not in s or not status:
                errors.append(f"MISSING_FIELD: steps[id={sid}].source_status")
                status = None
            elif status not in ALLOWED_SOURCE_STATUS:
                errors.append(f"Узел '{sid}': недопустимый source_status '{status}'")
            if status == "assumption" and s.get("shown_as_confirmed"):
                errors.append(f"Узел '{sid}': assumption помечен как confirmed — запрещено (раздел 17 ТЗ)")

        role = s.get("role")
        if is_v2 and "role" not in s:
            errors.append(f"MISSING_FIELD: steps[id={sid}].role")
        elif not is_v2 and (not isinstance(role, str) or not role.strip()) \
                and task_type not in (None, "start_event", "end_event"):
            errors.append(f"Узел '{sid}' ('{name or ''}') без роли (lane)")

        required_fields = V2_REQUIRED_STEP_FIELDS if is_v2 else REQUIRED_STEP_FIELDS
        for field in required_fields:
            if field not in s:
                errors.append(f"MISSING_FIELD: steps[id={sid}].{field}")
        for field in (V2_LIST_FIELDS if is_v2 else STEP_LIST_FIELDS):
            if field not in s or not isinstance(s.get(field), list):
                errors.append(f"MISSING_FIELD: steps[id={sid}].{field}")

    for g in gateways:
        gw_type = g.get("gateway_type")
        if gw_type not in ("xor", "and", "or", "event_based"):
            errors.append(f"Gateway '{g['id']}' без допустимого gateway_type (xor/and/or/event_based)")
        elif gw_type in ("xor", "or"):
            # Реальный прод-баг (Встреча №5): build_registry.py требует
            # 'branches' для xor/or-шлюза с >=2 ветками (без него не может
            # подписать условие каждой ветки, раздел 22-23 дельта-ТЗ) — но
            # validate_model.py этого не проверял, поэтому модель проходила
            # "VALID" здесь и падала на 2 шага позже, в build_registry.py, уже
            # без шанса на correction request. Ловим это здесь же, вместе с
            # остальными canonical-schema требованиями.
            gw_next = g.get("next")
            gw_next = gw_next if isinstance(gw_next, list) else []
            if len(gw_next) >= 2 and not g.get("branches"):
                errors.append(f"Gateway '{g['id']}' (xor/or) имеет {len(gw_next)} веток, но нет "
                               f"'branches' с условиями — заполните process_model.json")

    if is_v2:
        semantic_errors, semantic_warnings = validate_semantics(model)
        errors.extend(semantic_errors)
        warnings.extend(semantic_warnings)

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)

    return list(dict.fromkeys(errors))


def _warning_types(model: dict, step_id: str) -> set[str]:
    return {
        str(item.get("type"))
        for item in (model.get("warnings") or [])
        if isinstance(item, dict) and str(item.get("step")) in {step_id, "process"}
    }


def _is_suspiciously_non_atomic(step: dict) -> bool:
    text = " ".join(str(step.get(field) or "") for field in ("name", "action"))
    if re.search(r"\bи\b", text, flags=re.IGNORECASE):
        verbs = re.findall(
            r"\b(?:открыть|ознакомиться|сканировать|проверить|подтвердить|зафиксировать|"
            r"передать|создать|назначить|распечатать|наклеить|разместить|завершить)\w*\b",
            text,
            flags=re.IGNORECASE,
        )
        return len(verbs) >= 2
    return False


def validate_semantics(model: dict) -> tuple[list[str], list[str]]:
    """Validate process_model 2.1 BPMN semantics and corporate source rules."""
    errors: list[str] = []
    warnings: list[str] = []
    if model.get("schema_version") != SCHEMA_VERSION:
        return [f"SCHEMA_VERSION_UNSUPPORTED: expected {SCHEMA_VERSION}"], warnings
    if has_legacy_fields(model):
        errors.append("LEGACY_FIELD_PRESENT: schema 2.1 не может содержать source_status/artifact_type")
    errors.extend(str(item) for item in (model.get("migration_errors") or []))

    systems = [str(value).strip() for value in (model.get("systems") or []) if str(value).strip()]
    known_systems = systems + ["1С:ERP", "1С:УТ", "Cleverence", "Mobile SMARTS", "СБИС", "ГИС МТ", "CryptoPro"]
    participants = {
        str(item.get("id")): item for item in (model.get("participants") or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    message_flows = [item for item in (model.get("message_flows") or []) if isinstance(item, dict)]
    steps_by_id = {
        str(item.get("id")): item for item in (model.get("steps") or [])
        if isinstance(item, dict) and item.get("id")
    }
    step_pools = {
        str(item.get("id")): str(item.get("pool_id") or "").strip()
        for item in (model.get("steps") or []) if isinstance(item, dict) and item.get("id")
    }
    gaps = [gap for gap in (model.get("information_gaps") or []) if isinstance(gap, dict)]
    gap_ids = {str(gap.get("id")) for gap in gaps if gap.get("id")}
    gap_step_ids = {
        token.strip()
        for gap in (model.get("information_gaps") or []) if isinstance(gap, dict)
        and str(gap.get("status") or "").upper() in {"OPEN", "ASSUMPTION"}
        for token in re.split(r"\s*/\s*|\s*,\s*", str(gap.get("step_id") or ""))
        if token.strip()
    }
    metadata_gap_step_ids = {
        str(gap.get("step_id") or "") for gap in gaps
        if not str(gap.get("id") or "").startswith("GAP.ARTIFACT.")
        and not str(gap.get("missing_information") or "").lower().startswith("оценка ")
    }
    owner_gap_step_ids = {
        token.strip()
        for gap in gaps
        if "владел" in str(gap.get("missing_information") or "").lower()
        and str(gap.get("status") or "").upper() in {"OPEN", "ASSUMPTION"}
        for token in re.split(r"\s*/\s*|\s*,\s*", str(gap.get("step_id") or ""))
        if token.strip()
    }
    for step in model.get("steps") if isinstance(model.get("steps"), list) else []:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str):
            continue
        sid = step["id"]
        task_type = step.get("task_type")
        status = step.get("knowledge_status")
        role = step.get("role")
        system = step.get("system")
        refs = step.get("source_refs")

        if task_type in TASK_TYPES:
            performer = step.get("performer_kind")
            if performer not in {"human", "automated", "process"}:
                errors.append(f"PERFORMER_KIND_REQUIRED: '{sid}'")
            if performer == "human" and (not isinstance(role, str) or not role.strip()):
                errors.append(f"ROLE_REQUIRED_FOR_HUMAN_ACTIVITY: '{sid}'")
            if performer == "automated" and isinstance(role, str) and role.strip():
                errors.append(f"AUTOMATED_ACTIVITY_ROLE_FORBIDDEN: '{sid}'")
            if task_type in {"user_task", "manual_task"}:
                if performer != "human":
                    errors.append(f"HUMAN_TASK_PERFORMER_INVALID: '{sid}'")
            if task_type in {"service_task", "script_task", "business_rule_task"}:
                if performer != "automated":
                    errors.append(f"AUTOMATED_TASK_PERFORMER_INVALID: '{sid}'")
            if task_type == "call_activity":
                if performer != "process" or isinstance(role, str) and role.strip():
                    errors.append(f"CALL_ACTIVITY_PERFORMER_INVALID: '{sid}'")
                if not str(step.get("called_process_id") or "").strip():
                    errors.append(f"CALL_ACTIVITY_CALLED_PROCESS_REQUIRED: '{sid}'")
            if model.get("render_profile") == "corporate_role_overlays_v2" \
                    and performer in {"automated", "process"} \
                    and not str(step.get("responsible_owner") or "").strip() \
                    and sid not in owner_gap_step_ids:
                errors.append(f"AUTOMATION_OWNER_INFORMATION_GAP_REQUIRED: '{sid}'")
            if not isinstance(step.get("action"), str) or not step["action"].strip():
                errors.append(f"MISSING_FIELD: steps[id={sid}].action")
            obj = step.get("business_object")
            if not isinstance(obj, dict) or not str(obj.get("name") or "").strip() \
                    or obj.get("kind") not in ARTIFACT_KINDS:
                errors.append(f"MISSING_FIELD: steps[id={sid}].business_object")
            if not isinstance(system, str) or not system.strip():
                errors.append(f"SYSTEM_REQUIRED: '{sid}'")
            if task_type == "manual_task" and system != "Вне ИС":
                errors.append(f"MANUAL_TASK_SYSTEM_INVALID: '{sid}' должен иметь system='Вне ИС'")
            if task_type != "manual_task" and system == "Вне ИС":
                warnings.append(f"NON_MANUAL_OUTSIDE_IS: '{sid}' — проверьте task_type")
            if not str(step.get("basis") or "").strip() \
                    and not (status in {"OPEN", "ASSUMPTION"} and sid in gap_step_ids):
                errors.append(f"RENDER_CONTRACT_MISSING_BASIS: '{sid}'")
            if not str(step.get("observable_result") or "").strip() \
                    and not str(step.get("verification_criterion") or "").strip() \
                    and not (status in {"OPEN", "ASSUMPTION"} and sid in gap_step_ids):
                errors.append(f"RENDER_CONTRACT_MISSING_RESULT_OR_CHECK: '{sid}'")
            if task_type == "send_task":
                pool_id = str(step.get("pool_id") or "").strip()
                matching = []
                for flow in message_flows:
                    if flow.get("source") != sid:
                        continue
                    target = str(flow.get("target") or "").strip()
                    target_pool = target if target in participants else step_pools.get(target, "")
                    if target_pool in participants and target_pool != pool_id:
                        matching.append(flow)
                if len(participants) < 2 or pool_id not in participants or not matching:
                    errors.append(
                        f"SEND_TASK_REQUIRES_MESSAGE_FLOW: '{sid}' требует отдельные participants/pool_id "
                        "и исходящий message_flow"
                    )
            if MUTATING_ACTION_RE.search(str(step.get("action") or "").strip()) \
                    and not (isinstance(step.get("outputs"), list) and step.get("outputs")):
                errors.append(
                    f"MUTATING_ACTIVITY_OUTPUT_REQUIRED: '{sid}' изменяет бизнес-объект, но outputs пуст"
                )
            if status == "REQUIRES_1C_METADATA_VALIDATION" and sid not in metadata_gap_step_ids:
                errors.append(
                    f"METADATA_VALIDATION_INFORMATION_GAP_REQUIRED: '{sid}' требует проверки метаданных 1С, "
                    "но не связан с OPEN information gap"
                )
            codes = step.get("display_codes")
            if not isinstance(codes, list) or not codes or any(not str(code).strip() for code in codes):
                errors.append(f"DISPLAY_CODES_REQUIRED: '{sid}'")
            assessment = step.get("artifact_assessment")
            if not isinstance(assessment, dict):
                errors.append(f"ARTIFACT_ASSESSMENT_REQUIRED: '{sid}'")
            else:
                for direction in ("input", "output"):
                    values = step.get(f"{direction}s") if isinstance(step.get(f"{direction}s"), list) else []
                    side = assessment.get(direction) if isinstance(assessment.get(direction), dict) else {}
                    assessment_status = side.get("status")
                    gap_ref = side.get("information_gap_ref")
                    if assessment_status == "identified" and not values:
                        errors.append(f"ARTIFACT_ASSESSMENT_IDENTIFIED_EMPTY: '{sid}'.{direction}")
                    elif assessment_status == "not_applicable" and values:
                        errors.append(f"ARTIFACT_ASSESSMENT_NOT_APPLICABLE_WITH_VALUES: '{sid}'.{direction}")
                    elif assessment_status == "open" and (not gap_ref or str(gap_ref) not in gap_ids):
                        errors.append(f"ARTIFACT_ASSESSMENT_OPEN_GAP_REQUIRED: '{sid}'.{direction}")
                    elif assessment_status not in {"identified", "not_applicable", "open"}:
                        errors.append(f"ARTIFACT_ASSESSMENT_STATUS_INVALID: '{sid}'.{direction}")
        elif task_type in EVENT_TYPES:
            pass

        for field in ("basis", "observable_result", "verification_criterion"):
            value = str(step.get(field) or "")
            if any(pattern.search(value) for pattern in FORBIDDEN_PLACEHOLDER_PATTERNS):
                errors.append(f"GENERIC_RENDER_PLACEHOLDER_FORBIDDEN: '{sid}'.{field}")

        if status == "CONFIRMED" and (not isinstance(refs, list) or not refs):
            errors.append(f"CONFIRMED_WITHOUT_SOURCE_REFS: '{sid}'")
        if status == "CONFIRMED" and isinstance(refs, list) and refs:
            primary_refs = [
                ref for ref in refs if isinstance(ref, dict)
                and "/fixtures/" not in str(ref.get("source_file") or "").replace("\\", "/").lower()
                and not str(ref.get("source_file") or "").replace("\\", "/").lower().startswith("tests/fixtures/")
            ]
            if not primary_refs:
                errors.append(f"CONFIRMED_FIXTURE_ONLY_SOURCE: '{sid}'")
        for ref_index, ref in enumerate(refs if isinstance(refs, list) else []):
            if not isinstance(ref, dict) or not str(ref.get("source_file") or "").strip():
                errors.append(f"INVALID_SOURCE_REF: '{sid}' source_refs[{ref_index}].source_file")
                continue
            if status == "CONFIRMED" and not str(ref.get("locator") or "").strip() \
                    and "LEGACY_SOURCE_NOT_TRACED" not in _warning_types(model, sid):
                errors.append(f"SOURCE_LOCATOR_REQUIRED: '{sid}' source_refs[{ref_index}]")

        if isinstance(role, str):
            lowered_role = role.lower()
            for known in known_systems:
                if known.lower() in lowered_role:
                    errors.append(f"ROLE_IS_SYSTEM: '{sid}' role содержит систему '{known}'")
                    break
        if isinstance(system, str) and re.search(r"\s(?:/|→|\+|;)\s", system):
            warnings.append(f"COMPOSITE_SYSTEM_LABEL: '{sid}' system='{system}' — возможны несколько Activity")
        for field in ("inputs", "outputs"):
            values = step.get(field)
            if not isinstance(values, list):
                continue
            for index, item in enumerate(values):
                if not isinstance(item, dict) or not str(item.get("name") or "").strip() \
                        or item.get("kind") not in ARTIFACT_KINDS \
                        or not isinstance(item.get("source_refs"), list) \
                        or not str(item.get("logical_object_id") or "").strip() \
                        or item.get("bpmn_representation") not in {"data_object", "data_store"}:
                    errors.append(f"INVALID_ARTIFACT: '{sid}'.{field}[{index}]")
                if item.get("bpmn_representation") == "data_store" \
                        and not any(isinstance(ref, dict) for ref in item.get("source_refs") or []):
                    errors.append(f"DATA_STORE_PROVENANCE_REQUIRED: '{sid}'.{field}[{index}]")
        if _is_suspiciously_non_atomic(step):
            warnings.append(f"POSSIBLE_NON_ATOMIC_ACTIVITY: '{sid}' — '{step.get('name', '')}'")
    for participant in participants.values():
        linked = participant.get("linked_process")
        if isinstance(linked, dict) and linked.get("uri") and not linked.get("source_refs"):
            errors.append(f"PROCESS_LINK_URI_PROVENANCE_REQUIRED: '{participant.get('id')}'")
    participant_ids = set(participants)
    for flow in message_flows:
        source, target = str(flow.get("source") or ""), str(flow.get("target") or "")
        source_pool = source if source in participant_ids else step_pools.get(source)
        target_pool = target if target in participant_ids else step_pools.get(target)
        if not source_pool or not target_pool or source_pool == target_pool:
            errors.append(f"MESSAGE_FLOW_PARTICIPANT_BOUNDARY_REQUIRED: '{flow.get('id', '?')}'")
        target_step = steps_by_id.get(target)
        if target_step and target_step.get("task_type") == "start_event" \
                and target_step.get("event_definition", "none") != "message":
            errors.append(
                f"MESSAGE_FLOW_START_EVENT_DEFINITION_REQUIRED: '{flow.get('id', '?')}' -> '{target}'"
            )

    gateways = {str(g.get("id")): g for g in (model.get("gateways") or []) if isinstance(g, dict) and g.get("id")}
    nodes = {str(s.get("id")): s for s in (model.get("steps") or []) if isinstance(s, dict) and s.get("id")}
    nodes.update(gateways)

    # Path-sensitive logical-object state continuity. A state consumed by an
    # Activity is valid only when every path to that Activity has crossed an
    # earlier Activity that explicitly emitted the same logical_object_id +
    # state pair. A pair never emitted anywhere is treated as process input;
    # once the logical object has emitted states, silently introducing another
    # state is no longer allowed.
    producers: dict[tuple[str, str], set[str]] = {}
    logical_producers: dict[str, set[str]] = {}
    for step in model.get("steps") or []:
        if not isinstance(step, dict):
            continue
        sid = str(step.get("id") or "")
        for item in step.get("outputs") or []:
            if not isinstance(item, dict):
                continue
            logical_id = str(item.get("logical_object_id") or "").strip()
            state = str(item.get("state") or "").strip()
            if logical_id:
                logical_producers.setdefault(logical_id, set()).add(sid)
            if logical_id and state:
                producers.setdefault((logical_id, state), set()).add(sid)

    graph = {nid: [str(target) for target in node.get("next") or [] if str(target) in nodes]
             for nid, node in nodes.items()}
    start_id = str(model.get("start") or "")

    def reachable_without(target_id: str, blocked: set[str]) -> bool:
        if not start_id or start_id not in nodes:
            return False
        pending, seen = [start_id], set()
        while pending:
            current = pending.pop()
            if current in seen or current in blocked:
                continue
            if current == target_id:
                return True
            seen.add(current)
            pending.extend(graph.get(current, []))
        return False

    def can_reach(source_id: str, target_id: str) -> bool:
        pending, seen = [source_id], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            if current == target_id:
                return True
            seen.add(current)
            pending.extend(graph.get(current, []))
        return False

    for step in model.get("steps") or []:
        if not isinstance(step, dict):
            continue
        sid = str(step.get("id") or "")
        for item in step.get("inputs") or []:
            if not isinstance(item, dict):
                continue
            logical_id = str(item.get("logical_object_id") or "").strip()
            state = str(item.get("state") or "").strip()
            if not logical_id or not state:
                continue
            earlier_producers = set(producers.get((logical_id, state), set())) - {sid}
            if not earlier_producers:
                upstream_states = {
                    producer for producer in logical_producers.get(logical_id, set()) - {sid}
                    if can_reach(producer, sid)
                }
                if upstream_states:
                    errors.append(
                        f"ARTIFACT_STATE_PRODUCER_REQUIRED: '{sid}' consumes "
                        f"'{logical_id}' [{state}] without an earlier explicit output"
                    )
                continue
            if reachable_without(sid, earlier_producers):
                errors.append(
                    f"ARTIFACT_STATE_NOT_PRODUCED_ON_ALL_PATHS: '{sid}' consumes "
                    f"'{logical_id}' [{state}] on a path without its producing Activity"
                )

    incoming_by_node: dict[str, list[str]] = {}
    for source_id, node in nodes.items():
        for target_id in node.get("next") or []:
            incoming_by_node.setdefault(str(target_id), []).append(source_id)
    for gid, gateway in gateways.items():
        role_kind = gateway.get("gateway_role")
        pair_id = gateway.get("paired_gateway_id")
        if role_kind == "merge" and (gateway.get("name") or gateway.get("branches")):
            errors.append(f"MERGE_GATEWAY_MUST_BE_UNLABELED: '{gid}'")
        if role_kind == "merge" and len(incoming_by_node.get(gid, [])) < 2:
            errors.append(f"MERGE_GATEWAY_FAN_IN_REQUIRED: '{gid}'")
        if pair_id:
            pair = gateways.get(str(pair_id))
            if not pair or pair.get("gateway_type") != gateway.get("gateway_type") \
                    or pair.get("gateway_role") == role_kind:
                errors.append(f"PAIRED_GATEWAY_INVALID: '{gid}' -> '{pair_id}'")
        if gateway.get("gateway_type") == "event_based" and role_kind == "split":
            for target in gateway.get("next") or []:
                target_type = (nodes.get(str(target)) or {}).get("task_type")
                if target_type not in {"receive_task", "intermediate_catch_event"}:
                    errors.append(f"EVENT_BASED_GATEWAY_TARGET_INVALID: '{gid}' -> '{target}'")
        defaults = [branch for branch in gateway.get("branches") or []
                    if isinstance(branch, dict) and branch.get("default")]
        if len(defaults) > 1:
            errors.append(f"MULTIPLE_DEFAULT_FLOWS_FORBIDDEN: '{gid}'")
        if defaults and (gateway.get("gateway_type") not in {"xor", "or"} or role_kind != "split"):
            errors.append(f"DEFAULT_FLOW_SOURCE_INVALID: '{gid}'")
    for step in model.get("steps") or []:
        if not isinstance(step, dict):
            continue
        incoming = [source_id for source_id, node in nodes.items() if step.get("id") in (node.get("next") or [])]
        if len(incoming) > 1 and any((nodes.get(source_id) or {}).get("gateway_role") != "merge" for source_id in incoming):
            errors.append(f"EXPLICIT_MERGE_REQUIRED_BEFORE_ACTIVITY: '{step.get('id')}'")
    return errors, warnings


def validate_model_source_rules(model: dict) -> tuple[list[str], list[str]]:
    """Public gate hook used by validation reports and tests."""
    return validate_semantics(model)


def validate_bpmn_semantics(model: dict) -> tuple[list[str], list[str]]:
    """Public semantic gate; kept separate from draw.io corporate notation."""
    return validate_semantics(model)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_path", help="Путь к process_model.json")
    args = parser.parse_args()

    path = Path(args.model_path)
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print("Ошибок графа: 1")
        print(f"  - MISSING_FIELD: <root> (не удалось прочитать/распарсить JSON: {exc})")
        sys.exit(1)

    try:
        errors = validate(model)
    except Exception as exc:  # noqa: BLE001 — defensive last resort (раздел 4 задачи):
        # плохо сформированный LLM JSON не должен ронять validate_model.py
        # трейсбеком — только обычной ошибкой с exit code 1.
        print("Ошибок графа: 1")
        print(f"  - MISSING_FIELD: <root> (validate() упал на неожиданной структуре: {exc})")
        sys.exit(1)

    if errors:
        print(f"Ошибок графа: {len(errors)}")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("OK: граф валиден, ошибок нет")
        sys.exit(0)


if __name__ == "__main__":
    main()
