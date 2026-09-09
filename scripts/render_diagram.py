#!/usr/bin/env python3
"""
Рендер .drawio (+ PNG) из BPMN Lite registry с корпоративным постпроцессингом
(TZ_DELTA_CORPORATE_BPMN.md).

Конвейер:
  1. bpmn-diagrams bpmn_gen.py drawio  → черновой .drawio (role-only lanes,
     без системных/артефактных lane — при условии, что build_registry.py не
     передал "Система"/"Результат"/"Вход" в реестр).
  2. scripts/diagram_layout.py         → Pool→Lane→Task hierarchy, system
     label под Task (font 8), артефакты под Task, explicit ports/waypoints.
  3. scripts/validate_drawio.py        → корпоративный validator. ERROR
     блокирует PNG (раздел 40.10-11 дельта-ТЗ).
  4. При PASS — PNG через локальный draw.io CLI (config/tooling.json),
     НЕ через установку/скачивание (раздел 32 дельта-ТЗ).

Не переписывает bpmn-diagrams (раздел 10 ТЗ) — только адаптер путей и
постобработка XML, которую bpmn-diagrams не поддерживает нативно.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
DRAWIO_STABLE_SCALE_FLAGS = ["--force-device-scale-factor=1"]

sys.path.insert(0, str(SCRIPTS_DIR))
from runtime_support import find_drawio_cli, vendor_scripts_dir  # noqa: E402

BPMN_DIAGRAMS_SKILL = vendor_scripts_dir().parent


def external_utf8_env() -> dict:
    """os.environ + forced UTF-8, для subprocess-вызовов ВНЕШНЕГО кода
    (bpmn-diagrams), которому мы не можем поправить print()/encoding изнутри
    (раздел "Не редактируй внешний skill"). На Windows дефолтная кодировка
    stdout дочернего процесса при capture_output=True — это system codepage
    (напр. cp1251), а не UTF-8, независимо от того, чем `subprocess.run(...,
    encoding=...)` декодирует байты НА СТОРОНЕ РОДИТЕЛЯ: сам child ловит
    UnicodeEncodeError ДО того, как что-либо попадёт на сторону родителя,
    если пытается напечатать символ вроде '→' (U+2192) в cp1251. PYTHONUTF8/
    PYTHONIOENCODING форсируют UTF-8 для child'а независимо от Windows
    console codepage. os.environ копируется целиком, а не заменяется — не
    роняем остальной унаследованный env (PATH и т.п.)."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def mark_visual_review_required(process_id: str, reason: str) -> None:
    """Downgrade structural PASS when actual SVG/PNG inspection was unavailable."""
    validation_dir = Path("output/validation")
    json_path = validation_dir / f"{process_id}-drawio-validation.json"
    md_path = validation_dir / f"{process_id}-drawio-validation.md"
    if json_path.is_file():
        report = json.loads(json_path.read_text(encoding="utf-8"))
        report["status"] = "NEEDS_REVIEW"
        warnings = report.setdefault("warnings", [])
        warnings.append({"rule": "VISUAL_VALIDATION_UNAVAILABLE", "node": "diagram", "detail": reason})
        report["warning_count"] = len(warnings)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if md_path.is_file():
        text = md_path.read_text(encoding="utf-8")
        text = text.replace("Статус: **PASS**", "Статус: **NEEDS_REVIEW**", 1)
        text += f"\n\n## Runtime limitation\n\n- `VISUAL_VALIDATION_UNAVAILABLE` — {reason}\n"
        md_path.write_text(text, encoding="utf-8")


def drawio_registry_without_data(registry: Path, destination: Path) -> Path:
    """Blank BPMN data columns only for the raw draw.io vendor pass.

    The canonical registry retains Input/Result so BPMN XML can contain typed
    data references. draw.io artifacts are added from render-meta by the
    corporate post-processor; passing the same columns to the legacy vendor
    would incorrectly create system/artifact swimlanes and duplicate shapes.
    """
    lines = registry.read_text(encoding="utf-8").splitlines()
    in_nodes = False
    input_index = result_index = None
    rendered = []
    for line in lines:
        if line.startswith("## "):
            in_nodes = "узл" in line.lower()
            input_index = result_index = None
        if in_nodes and line.lstrip().startswith("|"):
            cells = re.split(r"(?<!\\)\|", line)
            visible = [cell.strip() for cell in cells[1:-1]]
            if "node_id" in visible and "Вход" in visible and "Результат" in visible:
                input_index = visible.index("Вход")
                result_index = visible.index("Результат")
            elif input_index is not None and not all(set(cell.strip()) <= {"-", ":"} for cell in visible):
                cells[1 + input_index] = " "
                cells[1 + result_index] = " "
                line = "|".join(cells)
        rendered.append(line)
    destination.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry_path", help="Путь к <process-id>-registry.md")
    parser.add_argument("process_id", help="Идентификатор процесса для имени выходных файлов")
    parser.add_argument("--png", action="store_true", default=True, help="Экспортировать PNG (по умолчанию включено)")
    parser.add_argument("--no-png", dest="png", action="store_false")
    parser.add_argument("--model", help="Путь к process_model.json (по умолчанию output/models/<id>-model.json)")
    args = parser.parse_args()

    py = sys.executable
    model_path = Path(args.model) if args.model else Path(f"output/models/{args.process_id}-model.json")
    render_meta_path = Path(args.registry_path).with_name(
        Path(args.registry_path).stem.replace("-registry", "") + "-render-meta.json"
    )

    tmp_outdir = Path("output") / "_render_tmp" / args.process_id
    # ВАЖНО: очищать перед каждым запуском — иначе glob("*.drawio") ниже может
    # подхватить файл от предыдущего/ручного прогона (напр. уже прошедший
    # layout post-processing) и повторно его "перепарентить", что портит
    # геометрию (двойное вычитание lane offset → отрицательные координаты).
    shutil.rmtree(tmp_outdir, ignore_errors=True)
    tmp_outdir.mkdir(parents=True, exist_ok=True)

    render_meta = json.loads(render_meta_path.read_text(encoding="utf-8")) if render_meta_path.is_file() else {}
    if render_meta.get("render_profile") == "corporate_role_overlays_v2":
        layouted = tmp_outdir / "layout_applied.drawio"
        result = subprocess.run(
            [py, str(SCRIPTS_DIR / "corporate_renderer.py"), str(model_path), str(render_meta_path), str(layouted)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)
        # Corporate geometry is accepted only after draw.io has materialised
        # the actual SVG paths. This catches router behaviour that cannot be
        # proven from mxCell source/target declarations alone.
        drawio_cli = find_drawio_cli(Path.cwd())
        if not drawio_cli:
            validate_cmd = [py, str(SCRIPTS_DIR / "validate_drawio.py"), str(layouted),
                            str(render_meta_path), str(model_path)]
            result = subprocess.run(validate_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(result.stdout)
            if result.returncode != 0:
                print(result.stderr, file=sys.stderr)
                failed_dir = Path("output/drawio/_failed")
                failed_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(layouted, failed_dir / f"{args.process_id}.drawio")
                shutil.rmtree(tmp_outdir, ignore_errors=True)
                sys.exit(result.returncode)
            Path("output/drawio").mkdir(parents=True, exist_ok=True)
            dest_drawio = Path("output/drawio") / f"{args.process_id}.drawio"
            shutil.copy(layouted, dest_drawio)
            reason = "draw.io CLI не найден; фактические SVG-маршруты и PNG не проверены"
            mark_visual_review_required(args.process_id, reason)
            print(f"drawio: {dest_drawio}")
            print(f"NEEDS_REVIEW: {reason}", file=sys.stderr)
            shutil.rmtree(tmp_outdir, ignore_errors=True)
            return
        rendered_svg = tmp_outdir / "layout_applied.svg"
        svg_result = subprocess.run(
            [drawio_cli, *DRAWIO_STABLE_SCALE_FLAGS, "--export", "--format", "svg", "--embed-diagram",
             "--border", "10", "--size", "page",
             "--output", str(rendered_svg.resolve()), str(layouted.resolve())],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if svg_result.returncode != 0 or not rendered_svg.is_file():
            print(f"SVG_ROUTE_VALIDATION_REQUIRED: export failed ({svg_result.returncode})\n{svg_result.stderr}",
                  file=sys.stderr)
            sys.exit(1)
        validate_cmd = [py, str(SCRIPTS_DIR / "validate_drawio.py"), str(layouted), str(render_meta_path),
                        str(model_path), "--svg", str(rendered_svg)]
        result = subprocess.run(validate_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            failed_dir = Path("output/drawio/_failed")
            failed_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(layouted, failed_dir / f"{args.process_id}.drawio")
            shutil.rmtree(tmp_outdir, ignore_errors=True)
            sys.exit(1)
        Path("output/drawio").mkdir(parents=True, exist_ok=True)
        dest_drawio = Path("output/drawio") / f"{args.process_id}.drawio"
        shutil.copy(layouted, dest_drawio)
        print(f"drawio: {dest_drawio}")
        if args.png:
            if drawio_cli:
                Path("output/preview").mkdir(parents=True, exist_ok=True)
                dest_png = Path("output/preview") / f"{args.process_id}.png"
                result = subprocess.run(
                    [drawio_cli, *DRAWIO_STABLE_SCALE_FLAGS, "--export", "--format", "png", "--embed-diagram",
                     "--border", "10", "--size", "page", "--output", str(dest_png), str(dest_drawio)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                if result.returncode != 0 or not dest_png.is_file():
                    print(f"PNG_EXPORT_UNAVAILABLE: exit={result.returncode} {result.stderr}", file=sys.stderr)
                else:
                    print(f"png: {dest_png}")
            else:
                print("PNG_EXPORT_UNAVAILABLE: draw.io CLI не найден", file=sys.stderr)
        shutil.rmtree(tmp_outdir, ignore_errors=True)
        return

    gen_script = BPMN_DIAGRAMS_SKILL / "scripts" / "bpmn_gen.py"
    if not gen_script.exists():
        print(f"Не найден встроенный vendor-скрипт bpmn_gen.py: {gen_script}", file=sys.stderr)
        sys.exit(1)

    drawio_registry = drawio_registry_without_data(
        Path(args.registry_path), tmp_outdir / "drawio-registry.md",
    )

    # --- 1. bpmn-diagrams: черновой .drawio ---
    cmd = [py, str(gen_script), "drawio", str(drawio_registry), str(tmp_outdir)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             env=external_utf8_env())
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    raw_drawio_files = list(tmp_outdir.glob("*.drawio"))
    if len(raw_drawio_files) != 1:
        print(f"ОШИБКА: ожидался ровно 1 свежий .drawio от bpmn-diagrams, найдено {len(raw_drawio_files)}: "
              f"{raw_drawio_files}", file=sys.stderr)
        sys.exit(1)
    raw_drawio = raw_drawio_files[0]

    # --- 2. Корпоративный layout post-processing ---
    layouted = tmp_outdir / "layout_applied.drawio"
    layout_cmd = [py, str(SCRIPTS_DIR / "diagram_layout.py"), str(raw_drawio), str(render_meta_path), str(layouted)]
    result = subprocess.run(layout_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    # --- 3. Корпоративный validator (ERROR блокирует публикацию) ---
    validate_cmd = [py, str(SCRIPTS_DIR / "validate_drawio.py"), str(layouted), str(render_meta_path), str(model_path)]
    result = subprocess.run(validate_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(result.stdout)
    validator_passed = result.returncode == 0

    # --- Атомарная публикация (раздел 28 TZ_PRODUCTION_BPMN_RUNNER.md):
    # старый рабочий .drawio/PNG НЕ удаляется и НЕ перезаписывается, пока
    # новая версия не прошла corporate validator. При FAIL — сохранить
    # неудачную попытку отдельно для диагностики, опубликованная версия
    # остаётся прежней. ---
    if not validator_passed:
        print("Корпоративный validator: FAIL — публикация ОТМЕНЕНА, прежняя версия (если была) сохранена.",
              file=sys.stderr)
        failed_dir = Path("output/drawio/_failed")
        failed_dir.mkdir(parents=True, exist_ok=True)
        failed_copy = failed_dir / f"{args.process_id}.drawio"
        shutil.copy(layouted, failed_copy)
        print(f"Неудачная попытка сохранена для диагностики: {failed_copy}", file=sys.stderr)
        shutil.rmtree(tmp_outdir, ignore_errors=True)
        sys.exit(1)

    Path("output/drawio").mkdir(parents=True, exist_ok=True)
    dest_drawio = Path("output/drawio") / f"{args.process_id}.drawio"
    shutil.copy(layouted, dest_drawio)
    print(f"drawio: {dest_drawio}")

    # --- 4. PNG через локальный draw.io CLI ---
    if args.png:
        drawio_cli = find_drawio_cli(Path.cwd())
        if not drawio_cli:
            reason = "draw.io CLI не найден; PNG и фактическая SVG-проверка недоступны"
            mark_visual_review_required(args.process_id, reason)
            print(f"NEEDS_REVIEW: {reason}. Автоматическая установка не выполняется.", file=sys.stderr)
        else:
            Path("output/preview").mkdir(parents=True, exist_ok=True)
            dest_png = Path("output/preview") / f"{args.process_id}.png"
            # --size page (не default "diagram"): у draw.io Desktop 31.1.8 обнаружен баг
            # export'а PNG/SVG в режиме "diagram" — контент правее ~3600-3700 единиц
            # координат (типично для процессов от ~15 шагов при стандартной раскладке)
            # молча не попадает в растр, вплоть до целых lane (проверено на синтетических
            # .drawio с 5 lane). "--size page" считает bounding box иначе и не обрезает.
            export_cmd = [drawio_cli, "--export", "--format", "png", "--embed-diagram",
                          "--border", "10", "--size", "page", "--output", str(dest_png), str(dest_drawio)]
            result = subprocess.run(export_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode == 0 and dest_png.exists() and dest_png.stat().st_size > 0:
                print(f"png: {dest_png}")
            else:
                print(f"PNG_EXPORT_UNAVAILABLE: экспорт не удался (exit={result.returncode})\n{result.stderr}",
                      file=sys.stderr)

    shutil.rmtree(tmp_outdir, ignore_errors=True)


if __name__ == "__main__":
    main()
