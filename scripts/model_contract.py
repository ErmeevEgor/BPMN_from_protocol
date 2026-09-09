#!/usr/bin/env python3
"""Canonical process-model 2.1 helpers and deterministic legacy migration.

This module owns deterministic migration from the legacy process model to the
semantic v2 contract. It never asks an LLM and never invents a source
locator. Values derived from a legacy label are recorded as warnings so the
semantic validator and human registry keep the migration visible.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re


CURRENT_SCHEMA_VERSION = "2.1"
SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
INPUT_SCHEMA_VERSIONS = {None, "1.0", "2.0", "2.1"}
KNOWLEDGE_STATUSES = {
    "CONFIRMED", "PROJECT_RECOMMENDATION", "ASSUMPTION", "OPEN",
    "REQUIRES_1C_METADATA_VALIDATION",
}
LEGACY_STATUS_MAP = {
    "confirmed_protocol": "CONFIRMED",
    "confirmed_erp": "CONFIRMED",
    "open_question": "OPEN",
    "assumption": "ASSUMPTION",
}
ARTIFACT_KINDS = {
    "system_object", "system_report", "print_form", "external_document",
    "physical_object", "message", "other",
}
LEGACY_ARTIFACT_KIND_MAP = {
    "system_document": "system_object",
    "external_document": "external_document",
    "print_form": "print_form",
    "system_report": "system_report",
    "reference_data": "system_object",
    "data": "system_object",
    "location": "system_object",
}
TASK_TYPES = {
    "user_task", "manual_task", "service_task", "script_task", "business_rule_task",
    "send_task", "receive_task", "call_activity", "subprocess",
}
EVENT_TYPES = {"start_event", "end_event", "intermediate_catch_event", "intermediate_throw_event"}
HUMAN_TASK_TYPES = {"user_task", "manual_task"}
AUTOMATED_TASK_TYPES = {"service_task", "script_task", "business_rule_task"}
RENDER_CONTRACT_FIELDS = ("basis", "observable_result", "verification_criterion")


def _append_warning(model: dict, step: str, warning_type: str, detail: str) -> None:
    warnings = model.setdefault("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
        model["warnings"] = warnings
    marker = (step, warning_type)
    for warning in warnings:
        if isinstance(warning, dict) and (warning.get("step"), warning.get("type")) == marker:
            return
    warnings.append({"step": step, "type": warning_type, "detail": detail})


def _append_information_gap(model: dict, step_id: str, missing: str,
                            impact: str, question: str, gap_id: str | None = None,
                            severity: str = "critical") -> None:
    """Record an unknown contract field instead of filling it with boilerplate."""
    gaps = model.setdefault("information_gaps", [])
    if not isinstance(gaps, list):
        gaps = []
        model["information_gaps"] = gaps
    if any(isinstance(gap, dict) and gap.get("step_id") == step_id
           and gap.get("missing_information") == missing for gap in gaps):
        return
    gaps.append({
        "id": gap_id or f"GAP.{_slug(step_id)}.{len(gaps) + 1}",
        "step_id": step_id,
        "missing_information": missing,
        "diagram_impact": impact,
        "customer_question": question,
        "status": "OPEN",
        "severity": severity,
    })


def _source_refs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [dict(ref) for ref in value if isinstance(ref, dict)]


def _slug(value: str) -> str:
    token = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "-", str(value or "").strip().lower()).strip("-")
    return token or "object"


def _normalize_artifacts(items, inherited_refs: list[dict], *, model: dict | None = None,
                         step_id: str = "", system: str = "", direction: str = "") -> list[dict]:
    result = []
    if not isinstance(items, list):
        return result
    for item in items:
        if isinstance(item, str) and item.strip():
            normalized = {"name": item.strip(), "kind": "other", "source_refs": deepcopy(inherited_refs)}
        elif isinstance(item, dict) and str(item.get("name") or "").strip():
            normalized = dict(item)
            legacy_kind = normalized.pop("artifact_type", None)
            raw_kind = normalized.get("kind") or legacy_kind
            kind = LEGACY_ARTIFACT_KIND_MAP.get(raw_kind, raw_kind)
            normalized["name"] = str(normalized["name"]).strip()
            normalized["kind"] = kind if kind in ARTIFACT_KINDS else "other"
            normalized["source_refs"] = _source_refs(normalized.get("source_refs")) or deepcopy(inherited_refs)
            if raw_kind == "system_document":
                normalized.setdefault("metadata_type", "document")
            elif raw_kind in {"reference_data", "data", "location"}:
                normalized.setdefault("metadata_type", raw_kind)
        else:
            continue
        generated_identity = not str(normalized.get("logical_object_id") or "").strip()
        if generated_identity:
            normalized["logical_object_id"] = f"OPEN.{_slug(normalized['name'])}"
            if model is not None and step_id:
                _append_information_gap(
                    model, step_id,
                    f"Логическая идентичность {direction or 'artifact'} «{normalized['name']}»",
                    "Нельзя подтвердить повторное использование Data Object между occurrences.",
                    f"Какой logical_object_id соответствует объекту «{normalized['name']}»?",
                )
        normalized.setdefault("state", None)
        normalized.setdefault("metadata_type", None)
        normalized.setdefault("system", system or None)
        normalized.setdefault("bpmn_representation", "data_object")
        result.append(normalized)
    return result


def _derive_action_and_object(name: str, inputs: list[dict], outputs: list[dict]) -> tuple[str, dict]:
    clean = str(name or "").strip()
    parts = clean.split(maxsplit=1)
    action = re.sub(r"[^0-9A-Za-zА-Яа-яЁё-]", "", parts[0]) if parts else ""
    object_name = parts[1].strip() if len(parts) == 2 else ""
    primary = outputs[0] if outputs else (inputs[0] if inputs else None)
    if not object_name and primary:
        object_name = primary["name"]
    if not object_name:
        object_name = clean
    object_kind = primary.get("kind", "other") if primary else "other"
    return action, {"name": object_name, "kind": object_kind}


def _legacy_source_ref(model: dict, step: dict, source_model_path: str | Path | None,
                       legacy_status: str | None) -> dict | None:
    source_file = str(model.get("source_file") or source_model_path or "").strip()
    if not source_file:
        return None
    sections = model.get("source_sections") if isinstance(model.get("source_sections"), list) else []
    return {
        "source_file": source_file,
        "source_type": "erp" if legacy_status == "confirmed_erp" else "protocol",
        "section": str(sections[0]) if sections else "",
        "locator": "",
        "excerpt": str(step.get("name") or "").strip(),
    }


def normalize_to_v2(raw, *, source_model_path: str | Path | None = None,
                    changes: list[str] | None = None):
    """Return a schema-2.1 model, preserving facts and exposing migration gaps.

    The function is idempotent. Legacy ``source_status`` and
    ``artifact_type`` fields are removed. Missing legacy semantic fields are
    derived only from existing labels/artifacts and are flagged. Missing
    source locators remain empty and produce ``LEGACY_SOURCE_NOT_TRACED``.
    """
    if not isinstance(raw, dict):
        return raw

    model = deepcopy(raw)
    source_version = model.get("schema_version")
    already_v2 = source_version == SCHEMA_VERSION
    model["schema_version"] = SCHEMA_VERSION
    model.setdefault("render_profile", "corporate_role_overlays_v2")
    model.setdefault("warnings", [])
    model.setdefault("open_questions", [])

    steps = model.get("steps")
    if isinstance(steps, list):
        normalized_steps = []
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                normalized_steps.append(raw_step)
                continue
            step = dict(raw_step)
            sid = str(step.get("id") or f"steps[{index}]")
            task_type = step.get("task_type")
            legacy_status = step.pop("source_status", None)
            if legacy_status in {"confirmed_protocol", "confirmed_erp"}:
                # Source confirmation and completeness of the render contract
                # are separate concerns. A Task may become OPEN because the
                # contract has gaps, while correction still must not delete
                # the source-confirmed business step.
                step["source_fact_confirmed"] = True
            status = step.get("knowledge_status") or LEGACY_STATUS_MAP.get(legacy_status)
            step["knowledge_status"] = status or "OPEN"
            step.setdefault("parent_id", None)
            if not str(step.get("system") or "").strip() and task_type == "manual_task":
                step["system"] = "Вне ИС"
                if changes is not None:
                    changes.append(f"{sid}: blank manual-task system -> Вне ИС")
            else:
                step.setdefault("system", "")
            if task_type in HUMAN_TASK_TYPES:
                step.setdefault("performer_kind", "human")
            elif task_type in AUTOMATED_TASK_TYPES:
                step.setdefault("performer_kind", "automated")
                legacy_role = str(step.get("role") or "").strip()
                if legacy_role:
                    step.setdefault("legacy_role_context", legacy_role)
                    step["role"] = None
                    _append_warning(
                        model, sid, "LEGACY_AUTOMATION_ROLE_REMOVED",
                        f"Роль «{legacy_role}» сохранена как legacy_role_context; automated Activity не получает human role.",
                    )
            elif task_type in {"call_activity", "subprocess"}:
                step.setdefault("performer_kind", "process")
            elif task_type in {"send_task", "receive_task"}:
                step.setdefault("performer_kind", "human" if str(step.get("role") or "").strip() else "automated")
                _append_information_gap(
                    model, sid, "Тип исполнителя send/receive Activity",
                    "Нельзя окончательно выбрать role overlay до подтверждения исполнителя.",
                    f"Кто исполняет шаг «{step.get('name', sid)}»: человек, автоматика или процесс?",
                )
            else:
                step.setdefault("performer_kind", "process")
            if task_type in EVENT_TYPES:
                step.setdefault("event_definition", "none")
            if task_type in TASK_TYPES:
                step.setdefault("responsible_owner", None)
                owner_gap_exists = any(
                    isinstance(gap, dict)
                    and sid in {token.strip() for token in re.split(r"\s*/\s*|\s*,\s*", str(gap.get("step_id") or ""))}
                    and "владел" in str(gap.get("missing_information") or "").lower()
                    and str(gap.get("status") or "").upper() in {"OPEN", "ASSUMPTION"}
                    for gap in model.get("information_gaps") or []
                )
                if model.get("render_profile") == "corporate_role_overlays_v2" \
                        and step.get("performer_kind") in {"automated", "process"} \
                        and not str(step.get("responsible_owner") or "").strip() \
                        and not owner_gap_exists:
                    _append_information_gap(
                        model, sid, "Ответственный владелец автоматизации/процесса",
                        "Владелец выполнения не подтверждён; human role overlay не создаётся.",
                        f"Кто является ответственным владельцем шага «{step.get('name', sid)}»?",
                        gap_id=f"GAP.OWNER.{_slug(sid)}", severity="medium",
                    )
            if task_type in TASK_TYPES and not str(step.get("system") or "").strip():
                step["knowledge_status"] = "OPEN"
                _append_information_gap(
                    model, sid, "Система выполнения Activity",
                    "System overlay остаётся OPEN; схема не может получить PASS.",
                    f"В какой системе выполняется шаг «{step.get('name', sid)}»?",
                )
            step.setdefault("execution_channel", None)
            refs = _source_refs(step.get("source_refs"))
            if not refs and step["knowledge_status"] == "CONFIRMED" and not already_v2:
                migrated_ref = _legacy_source_ref(model, step, source_model_path, legacy_status)
                if migrated_ref:
                    refs = [migrated_ref]
                _append_warning(model, sid, "LEGACY_SOURCE_NOT_TRACED",
                                "Точная трассировка legacy-факта не восстановлена; locator оставлен пустым.")
            step["source_refs"] = refs
            step["inputs"] = _normalize_artifacts(
                step.get("inputs"), refs, model=model, step_id=sid,
                system=str(step.get("system") or ""), direction="input",
            )
            step["outputs"] = _normalize_artifacts(
                step.get("outputs"), refs, model=model, step_id=sid,
                system=str(step.get("system") or ""), direction="output",
            )
            obj = step.get("business_object")
            if isinstance(obj, dict):
                raw_kind = obj.get("kind")
                obj["kind"] = LEGACY_ARTIFACT_KIND_MAP.get(raw_kind, raw_kind)
                if obj["kind"] not in ARTIFACT_KINDS:
                    obj["kind"] = "other"

            if task_type in TASK_TYPES:
                if not step.get("action") or not isinstance(step.get("business_object"), dict):
                    action, business_object = _derive_action_and_object(
                        step.get("name", ""), step["inputs"], step["outputs"]
                    )
                    step.setdefault("action", action)
                    step.setdefault("business_object", business_object)
                    _append_warning(model, sid, "LEGACY_SEMANTICS_DERIVED",
                                    "action/business_object выведены детерминированно из legacy name/артефактов; требуется review.")
                    if changes is not None:
                        changes.append(f"{sid}: derived action/business_object from legacy fields")
                input_names = [item["name"] for item in step["inputs"] if item.get("name")]
                output_names = [item["name"] for item in step["outputs"] if item.get("name")]
                controls = [str(item).strip() for item in step.get("control", []) if str(item).strip()]
                if not str(step.get("basis") or "").strip():
                    derived_basis = "; ".join(input_names) or str(step.get("condition") or "").strip()
                    step["basis"] = derived_basis or None
                    if not derived_basis:
                        step["knowledge_status"] = "OPEN"
                        _append_information_gap(
                            model, sid, "Основание выполнения шага",
                            "На схеме нельзя подтвердить предусловие Activity.",
                            f"Какое точное основание или предусловие запускает шаг «{step.get('name', sid)}»?",
                        )
                    else:
                        _append_warning(model, sid, "LEGACY_RENDER_CONTRACT_DERIVED",
                                        "basis выведено детерминированно; требуется review.")
                if not str(step.get("observable_result") or "").strip():
                    step["observable_result"] = "; ".join(output_names)
                if not str(step.get("verification_criterion") or "").strip():
                    step["verification_criterion"] = controls[0] if controls else None
                if not step.get("observable_result") and not step.get("verification_criterion"):
                    step["knowledge_status"] = "OPEN"
                    _append_information_gap(
                        model, sid, "Наблюдаемый результат или критерий проверки",
                        "Нельзя проверить завершение Activity по наблюдаемому факту.",
                        f"Какой результат или критерий подтверждает выполнение шага «{step.get('name', sid)}»?",
                    )
            else:
                step.setdefault("action", None)
                step.setdefault("business_object", None)
                step.setdefault("basis", None)
                step.setdefault("observable_result", None)
                step.setdefault("verification_criterion", None)

            step.setdefault("control", [])
            step.setdefault("condition", None)
            step.setdefault("exception_refs", [])
            if step.pop("shown_as_confirmed", False) and step["knowledge_status"] != "CONFIRMED":
                model.setdefault("migration_errors", []).append(
                    f"KNOWLEDGE_STATUS_MISREPRESENTED: '{sid}' marked shown_as_confirmed"
                )
            codes = [str(value).strip() for value in (step.get("display_codes") or []) if str(value).strip()]
            if not codes and task_type in TASK_TYPES:
                codes = re.findall(r"\b[А-ЯA-Z]{1,8}\.\d+(?:\.\d+)+\.?", str(step.get("name") or ""))
                codes = [code.rstrip(".") for code in codes]
                step["display_code_origin"] = "source" if codes else "generated"
                if not codes:
                    codes = [f"{model.get('process_id', 'PROCESS')}.{index + 1}"]
            step["display_codes"] = list(dict.fromkeys(codes))
            step.setdefault("display_code_origin", "source" if codes else "generated")
            presentation = step.get("presentation") if isinstance(step.get("presentation"), dict) else {}
            step["presentation"] = {
                "style_class": presentation.get("style_class"),
                "fill_override": presentation.get("fill_override"),
                "source_refs": _source_refs(presentation.get("source_refs")),
            }
            if task_type in TASK_TYPES:
                assessment = step.get("artifact_assessment") if isinstance(step.get("artifact_assessment"), dict) else {}
                normalized_assessment = {}
                for direction, values in (("input", step["inputs"]), ("output", step["outputs"])):
                    side = assessment.get(direction) if isinstance(assessment.get(direction), dict) else {}
                    if values:
                        normalized_assessment[direction] = {"status": "identified", "information_gap_ref": None}
                    elif side.get("status") in {"not_applicable", "open"}:
                        normalized_assessment[direction] = {
                            "status": side["status"], "information_gap_ref": side.get("information_gap_ref")
                        }
                    else:
                        gap_id = f"GAP.ARTIFACT.{sid}.{direction.upper()}"
                        _append_information_gap(
                            model, sid, f"Оценка {direction}-артефактов",
                            "Неизвестно, отсутствуют ли артефакты или они не были рассмотрены.",
                            f"Есть ли {direction}-артефакты у шага «{step.get('name', sid)}»?",
                            gap_id=gap_id,
                        )
                        normalized_assessment[direction] = {"status": "open", "information_gap_ref": gap_id}
                step["artifact_assessment"] = normalized_assessment
            else:
                step["artifact_assessment"] = {
                    "input": {"status": "not_applicable", "information_gap_ref": None},
                    "output": {"status": "not_applicable", "information_gap_ref": None},
                }
            normalized_steps.append(step)
        model["steps"] = normalized_steps

    gateways = model.get("gateways")
    if isinstance(gateways, list):
        for gateway in gateways:
            if not isinstance(gateway, dict):
                continue
            legacy_status = gateway.pop("source_status", None)
            if legacy_status and "knowledge_status" not in gateway:
                gateway["knowledge_status"] = LEGACY_STATUS_MAP.get(legacy_status, "OPEN")
            gateway.setdefault("source_refs", [])
            gateway.setdefault("paired_gateway_id", None)
            gateway.setdefault("branches", [])

    participants = model.get("participants") if isinstance(model.get("participants"), list) else []
    if not participants:
        participants = [{
            "id": "PART_MAIN", "name": str(model.get("process_name") or "Процесс"),
            "participant_kind": "current_process", "black_box": False,
            "linked_process": None, "source_refs": [],
        }]
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        participant.setdefault("participant_kind", "external_process" if participant.get("black_box") else "current_process")
        participant.setdefault("black_box", participant.get("participant_kind") != "current_process")
        participant.setdefault("linked_process", None)
        participant.setdefault("source_refs", [])
        linked = participant.get("linked_process")
        if isinstance(linked, dict):
            linked.setdefault("uri", None)
            linked.setdefault("source_refs", [])
    model["participants"] = participants
    model.setdefault("message_flows", [])
    model.setdefault("annotations", [])
    model.setdefault("information_gaps", [])
    for gap_index, gap in enumerate(model["information_gaps"]):
        if isinstance(gap, dict):
            gap.setdefault("id", f"GAP.{_slug(gap.get('step_id') or 'process')}.{gap_index + 1}")

    if not already_v2 and changes is not None:
        changes.append(f"schema_version: {source_version or 'legacy'} -> {SCHEMA_VERSION}")
    return model


def normalize_to_current(raw, *, source_model_path: str | Path | None = None,
                         changes: list[str] | None = None):
    return normalize_to_v2(raw, source_model_path=source_model_path, changes=changes)


def has_legacy_fields(model) -> bool:
    if not isinstance(model, dict):
        return False
    for step in model.get("steps") if isinstance(model.get("steps"), list) else []:
        if isinstance(step, dict) and ("source_status" in step or "shown_as_confirmed" in step):
            return True
        for field in ("inputs", "outputs"):
            values = step.get(field) if isinstance(step, dict) and isinstance(step.get(field), list) else []
            for item in values:
                if isinstance(item, str) or (isinstance(item, dict) and "artifact_type" in item):
                    return True
    for gateway in model.get("gateways") if isinstance(model.get("gateways"), list) else []:
        if isinstance(gateway, dict) and "source_status" in gateway:
            return True
    return False
