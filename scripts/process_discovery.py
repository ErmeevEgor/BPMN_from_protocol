#!/usr/bin/env python3
"""
Process Discovery (раздел 3, 5, 21 TZ_BATCH_PROTOCOL_BPMN.md;
раздел 17-19 TZ_PRODUCTION_BPMN_RUNNER.md).

Находит самостоятельные процессы внутри ОДНОГО протокола механически, по
приоритету сигналов (раздел 17 production-ТЗ):

  1. Явный заголовок/название сценария (строка-разделитель — только первая
     колонка заполнена, напр. "Закупки у поставщиков РФ").
  2. Явные префиксы номеров шагов ("РФ.1" -> "РФ") — сильный сигнал, но
     НЕ единственный.
  3. Независимые Start/End (нет входящих/исходящих ссылок за пределы группы).
  4. Связи в колонке "Передача данных" (граф шагов).
  5. Непересекающиеся компоненты графа — используются, только если 1-4 не
     дали результата; помечаются низкой уверенностью (needs_review).
  6. Семантическая группировка — остаётся за текущим агентом (не за этим скриптом).

Если граница не очевидна ни по одному сигналу — `UNCLEAR_PROCESS_BOUNDARY`
(не блокирует discovery, но помечает секцию как требующую семантического
решения агента; process_id/name НЕ выдумывается).

Семантическое решение (process_id, process_name, статус) остаётся за
Агент — скрипт формирует факты (границы, счётчики, метод определения,
дубликаты), которые передаются через --names.

Вход:  JSON от scripts/extract_protocol.py
Выход: output/manifests/<protocol-id>-process-manifest.json
       + отчёт "Найдено процессов: N" в stdout (раздел 21 batch-ТЗ)

Проверки:
  PROCESS_BOUNDARY_OVERLAP (ERROR) — один и тот же step id найден в
    нескольких группах.
  UNCLEAR_PROCESS_BOUNDARY (WARNING) — ни один сигнал не дал уверенной
    границы; секция возвращена как один кандидат с low confidence.
"""
import argparse
import json
import re
import sys
from pathlib import Path

STEP_COL = 0            # "Номер шага" всегда первая колонка
TRANSFER_COL_NAMES = ("Передача данных", "Следующий шаг")


def find_scenario_tables(extracted: dict) -> list[dict]:
    """Таблицы с колонкой 'Номер шага' — источник графа процесса."""
    result = []
    for idx, table in enumerate(extracted.get("tables", [])):
        if not table:
            continue
        header = table[0]
        if not header or "Номер шага" not in header[0]:
            continue
        result.append({"table_index": idx, "header": header, "rows": table[1:]})
    return result


def segment_by_titles(table: dict) -> list[dict]:
    """
    Разбивает строки таблицы на секции по строкам-разделителям (только
    первая колонка заполнена). Одна таблица может содержать НЕСКОЛЬКО
    таких заголовков (раздел 18 production-ТЗ: "Процесс А" / "Процесс Б"
    внутри одной таблицы, без префиксов шагов) — не только один в начале.
    """
    header = table["header"]
    rows = table["rows"]

    sections = []
    current_title = None
    current_rows: list[dict] = []

    def flush():
        if current_rows:
            sections.append({"title": current_title, "rows": current_rows[:]})

    for row in rows:
        step_id = (row[STEP_COL] or "").strip()
        rest_filled = any((c or "").strip() for c in row[1:])
        if not step_id:
            continue
        if not rest_filled:
            flush()
            current_title = step_id
            current_rows = []
            continue
        current_rows.append(dict(zip(header, row)))

    flush()
    return sections


def extract_prefix(step_id: str) -> str | None:
    m = re.match(r"^([^\d.]+)\.", step_id)
    return m.group(1) if m else None


def prefix_signal(rows: list[dict]) -> dict[str, list[dict]] | None:
    """Если >= 90% строк дают извлекаемый префикс и префиксов >= 2 разных
    -> возвращает {prefix: [rows]}. Иначе None (сигнал не сработал)."""
    prefixed = [(extract_prefix(r.get("Номер шага", "").strip()), r) for r in rows]
    with_prefix = [p for p, _ in prefixed if p]
    if not rows or len(with_prefix) / len(rows) < 0.9:
        return None
    groups: dict[str, list[dict]] = {}
    for p, r in prefixed:
        key = p or "?"
        groups.setdefault(key, []).append(r)
    return groups if len(groups) >= 1 else None


def parse_transfer_targets(value: str, known_ids: set[str]) -> list[str]:
    if not value:
        return []
    tokens = re.split(r"[/,;]", value)
    targets = []
    for t in tokens:
        t = t.strip()
        if t in known_ids:
            targets.append(t)
    return targets


def graph_components(rows: list[dict]) -> list[list[dict]] | None:
    """Строит граф по 'Передача данных' и возвращает слабосвязные
    компоненты, если их >= 2 и каждая содержит >= 2 узлов. Иначе None."""
    ids = [r.get("Номер шага", "").strip() for r in rows]
    known_ids = set(ids)
    if len(known_ids) < 2:
        return None

    transfer_col = next((c for c in TRANSFER_COL_NAMES if c in (rows[0] if rows else {})), None)
    adjacency: dict[str, set[str]] = {i: set() for i in ids}
    for r in rows:
        sid = r.get("Номер шага", "").strip()
        targets = parse_transfer_targets(r.get(transfer_col, "") if transfer_col else "", known_ids)
        for t in targets:
            adjacency[sid].add(t)
            adjacency[t].add(sid)

    visited = set()
    components = []
    for i in ids:
        if i in visited:
            continue
        stack = [i]
        comp = set()
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            stack.extend(adjacency.get(n, ()) - comp)
        visited |= comp
        components.append(comp)

    if len(components) < 2 or any(len(c) < 2 for c in components):
        return None

    row_by_id = {r.get("Номер шага", "").strip(): r for r in rows}
    return [[row_by_id[i] for i in comp if i in row_by_id] for comp in components]


def candidate_from_rows(rows: list[dict], method: str, confidence: str) -> dict:
    step_ids = [r.get("Номер шага", "").strip() for r in rows]
    return {
        "first_step": step_ids[0] if step_ids else None,
        "last_step": step_ids[-1] if step_ids else None,
        "number_of_steps": len(step_ids),
        "step_ids": step_ids,
        "boundary_method": method,
        "confidence": confidence,
    }


def discover_section(section_rows: list[dict], table_title: str | None) -> list[dict]:
    """Применяет каскад сигналов 2-5 (раздел 17 production-ТЗ) к строкам
    ОДНОЙ секции (уже разделённой сигналом 1 — заголовками)."""
    if not section_rows:
        return []

    # Сигнал 2: префиксы номеров шагов.
    groups = prefix_signal(section_rows)
    if groups and len(groups) >= 1:
        candidates = []
        for prefix, rows in groups.items():
            c = candidate_from_rows(rows, "step_prefix", "high")
            c["step_prefix"] = prefix
            c["table_title"] = table_title
            candidates.append(c)
        return candidates

    # Секция уже выделена заголовком (сигнал 1) — этого достаточно, если
    # префиксов нет: заголовок сам по себе независимая граница.
    if table_title is not None:
        c = candidate_from_rows(section_rows, "section_title", "high")
        c["step_prefix"] = None
        c["table_title"] = table_title
        return [c]

    # Сигналы 3-5: нет ни префикса, ни заголовка — единственная секция на
    # всю таблицу. Пробуем разложить по компонентам графа "Передача данных".
    comps = graph_components(section_rows)
    if comps:
        candidates = []
        for comp_rows in comps:
            c = candidate_from_rows(comp_rows, "graph_components", "low")
            c["step_prefix"] = None
            c["table_title"] = None
            candidates.append(c)
        return candidates

    # Ничего не дало уверенной границы — один кандидат, но неопределённый.
    c = candidate_from_rows(section_rows, "unclear", "low")
    c["step_prefix"] = None
    c["table_title"] = table_title
    c["unclear"] = True
    return [c]


def discover(extracted: dict) -> dict:
    """
    Проверка PROCESS_BOUNDARY_OVERLAP осмысленна только для boundary_method
    "step_prefix": там step id по конвенции протокола глобально уникален
    (РФ.1 не может законно повториться), и повтор — реальная проблема
    данных. Для "section_title"/"graph_components" локальная нумерация
    внутри каждой секции ("1, 2, 3" в обеих секциях) — норма: секции уже
    физически разделены строкой-заголовком/компонентой графа, ни одна
    строка не может попасть в две группы одновременно (группировка —
    partition, не multi-assign), поэтому сравнивать текстовые id между
    такими группами не нужно и даёт только ложные срабатывания.
    """
    tables = find_scenario_tables(extracted)
    discovery = {"protocol_source": extracted.get("source_file"), "processes": []}
    seen_step_ids: dict[str, int] = {}
    errors = []
    warnings = []

    group_counter = 0
    for table in tables:
        sections = segment_by_titles(table)
        for section in sections:
            candidates = discover_section(section["rows"], section["title"])
            for c in candidates:
                group_counter += 1
                c["table_index"] = table["table_index"]
                c["group_id"] = group_counter

                if c["boundary_method"] == "step_prefix":
                    for sid in c["step_ids"]:
                        if sid in seen_step_ids and seen_step_ids[sid] != group_counter:
                            errors.append({
                                "rule": "PROCESS_BOUNDARY_OVERLAP",
                                "step": sid,
                                "detail": f"'{sid}' встречается в нескольких группах (group_id "
                                          f"{seen_step_ids[sid]} и {group_counter})",
                            })
                        seen_step_ids[sid] = group_counter

                if c.get("unclear"):
                    warnings.append({
                        "rule": "UNCLEAR_PROCESS_BOUNDARY",
                        "group_id": group_counter,
                        "detail": f"Секция {c['first_step']}..{c['last_step']} "
                                  f"({c['number_of_steps']} шагов) — ни один сигнал "
                                  f"(заголовок/префикс/граф) не дал уверенной границы. "
                                  f"Требуется семантическое решение агента.",
                    })

                discovery["processes"].append(c)

    discovery["errors"] = errors
    discovery["warnings"] = warnings
    return discovery


def build_manifest(discovery: dict, names: dict, source_file: str, protocol_name: str) -> dict:
    """
    status по умолчанию — "ready" ("reference_existing" — осознанное решение
    через --names, не автоматический вывод из наличия файлов на диске —
    иначе повторный discovery на уже сгенерированном процессе тихо
    останавливал бы его дальнейшее обновление).

    Кандидаты с confidence="low" (graph_components/unclear) по умолчанию
    получают status="needs_review", если явно не переопределено в --names.
    """
    processes = []
    for p in discovery["processes"]:
        lookup_key = p.get("step_prefix") or p.get("table_title") or str(p["group_id"])
        hint = names.get(lookup_key, {})

        if hint.get("process_id"):
            process_id = hint["process_id"]
        elif p.get("step_prefix"):
            process_id = re.sub(r"\W+", "_", p["step_prefix"]).strip("_").upper()
        elif p.get("table_title"):
            process_id = re.sub(r"\W+", "_", p["table_title"]).strip("_").upper()[:40]
        else:
            process_id = f"PROCESS_{p['group_id']}"

        process_name = hint.get("process_name") or p.get("table_title") or process_id

        default_status = "needs_review" if p.get("confidence") == "low" else "ready"
        status = hint.get("status", default_status)

        processes.append({
            "process_id": process_id,
            "process_name": process_name,
            "step_prefixes": [f"{p['step_prefix']}."] if p.get("step_prefix") else [],
            "first_step": p["first_step"],
            "last_step": p["last_step"],
            "number_of_steps": p["number_of_steps"],
            "boundary_method": p["boundary_method"],
            "confidence": p["confidence"],
            "status": status,
        })

    return {
        "source_file": source_file,
        "protocol_name": protocol_name,
        "discovered_at": "process_discovery.py",
        "processes": processes,
        "boundary_errors": discovery["errors"],
        "boundary_warnings": discovery.get("warnings", []),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("extracted_json", help="Результат extract_protocol.py")
    parser.add_argument("--protocol-id", required=True, help="Идентификатор протокола для имени файлов")
    parser.add_argument("--protocol-name", required=True)
    parser.add_argument("--names", help="JSON: {\"РФ\": {\"process_id\":..,\"process_name\":..,\"status\":..}}")
    parser.add_argument("-o", "--out", help="Путь к manifest (по умолчанию output/manifests/<protocol-id>-process-manifest.json)")
    args = parser.parse_args()

    extracted = json.loads(Path(args.extracted_json).read_text(encoding="utf-8"))
    discovery = discover(extracted)

    print(f"Найдено процессов: {len(discovery['processes'])}")
    for p in discovery["processes"]:
        label = p.get("step_prefix") and f"{p['step_prefix']}.*" or (p.get("table_title") or f"group {p['group_id']}")
        print(f"  - {label} | метод={p['boundary_method']} confidence={p['confidence']} | "
              f"{p['first_step']} .. {p['last_step']} | шагов: {p['number_of_steps']}")
    if discovery["errors"]:
        for e in discovery["errors"]:
            print(f"ERROR: {e['rule']} — {e['detail']}", file=sys.stderr)
    if discovery.get("warnings"):
        for w in discovery["warnings"]:
            print(f"WARNING: {w['rule']} — {w['detail']}", file=sys.stderr)

    names = json.loads(Path(args.names).read_text(encoding="utf-8")) if args.names else {}
    source_file = extracted.get("source_file", "").replace("\\", "/")
    manifest = build_manifest(discovery, names, source_file, args.protocol_name)

    out_path = Path(args.out) if args.out else Path("output/manifests") / f"{args.protocol_id}-process-manifest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nManifest: {out_path}")

    if discovery["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
