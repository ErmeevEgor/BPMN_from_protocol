#!/usr/bin/env python3
"""
Извлечение чистого текста и таблиц из протокола моделирования.

Поддерживаемые форматы: .docx .doc .pdf .md .txt .html .xlsx .xlsm .csv .tsv

Для .doc сначала определяется реальный тип файла: некоторые выгрузки
Confluence имеют расширение .doc, но фактически содержат HTML/MIME текст.
В этом случае файл читается напрямую, без OCR и без LibreOffice.

Результат печатается в stdout как JSON:
{
  "source_file": "...",
  "detected_type": "html_mime | docx | doc_binary | pdf | spreadsheet | csv | tsv | md | txt | html",
  "sections": [{"heading": "...", "text": "..."}],
  "tables": [[["cell", ...], ...]],
  "raw_text": "...",
  "locators": [{"locator": "Лист1!A12", "value": "..."}]
}

Скрипт не пытается понять бизнес-процесс — это делает текущий агент, читая
результат вместе с bpmn-from-protocol. Задача скрипта — дать чистый,
предсказуемый текст на входе.
"""
import argparse
import csv
import email
import json
import re
import subprocess
import sys
from pathlib import Path


def detect_doc_subtype(path: Path) -> str:
    head = path.read_bytes()[:4]
    if head[:2] == b"PK":
        return "docx_zip"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return "doc_binary"
    # Otherwise assume text/HTML/MIME export (e.g. Confluence "Exported From Confluence")
    return "html_mime"


def extract_html_mime(path: Path) -> tuple[str, list[dict]]:
    raw = path.read_bytes()
    msg = email.message_from_bytes(raw)
    html_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                html_parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True) or raw
        html_parts.append(payload.decode("utf-8", errors="replace"))

    html_text = "\n".join(html_parts) if html_parts else raw.decode("utf-8", errors="replace")
    return extract_from_html_string(html_text)


def extract_from_html_string(html_text: str) -> tuple[str, list[dict]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    sections = []
    tables = extract_tables_from_soup(soup)

    # split by headings h1-h4
    current_heading = "root"
    current_text = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"]):
        if el.name in ("h1", "h2", "h3", "h4"):
            if current_text:
                sections.append({"heading": current_heading, "text": "\n".join(current_text).strip()})
            current_heading = el.get_text(strip=True)
            current_text = []
        elif el.name == "table":
            continue  # handled separately
        else:
            t = el.get_text(strip=True)
            if t:
                current_text.append(t)
    if current_text:
        sections.append({"heading": current_heading, "text": "\n".join(current_text).strip()})

    raw_text = soup.get_text("\n", strip=True)
    return raw_text, sections, tables


def extract_tables_from_soup(soup) -> list:
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def extract_docx(path: Path) -> tuple[str, list[dict], list]:
    import docx

    document = docx.Document(str(path))
    sections = []
    current_heading = "root"
    current_text = []
    for para in document.paragraphs:
        style = (para.style.name or "").lower()
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("heading") or style.startswith("title"):
            if current_text:
                sections.append({"heading": current_heading, "text": "\n".join(current_text).strip()})
            current_heading = text
            current_text = []
        else:
            current_text.append(text)
    if current_text:
        sections.append({"heading": current_heading, "text": "\n".join(current_text).strip()})

    tables = []
    for table in document.tables:
        rows = []
        for row in table.rows:
            rows.append([cell.text.strip() for cell in row.cells])
        if rows:
            tables.append(rows)

    raw_text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
    return raw_text, sections, tables


def convert_doc_binary_to_text(path: Path) -> str:
    """Convert legacy binary .doc via local LibreOffice (soffice). No OCR."""
    soffice_candidates = ["soffice", "soffice.exe",
                           r"C:\Program Files\LibreOffice\program\soffice.exe"]
    soffice = None
    for cand in soffice_candidates:
        try:
            subprocess.run([cand, "--version"], capture_output=True, timeout=15, check=True)
            soffice = cand
            break
        except Exception:
            continue
    if not soffice:
        raise RuntimeError(
            "Бинарный .doc обнаружен, но LibreOffice (soffice) не найден локально. "
            "Установите LibreOffice для конвертации без OCR."
        )
    out_dir = path.parent / ".doc_convert_tmp"
    out_dir.mkdir(exist_ok=True)
    subprocess.run(
        [soffice, "--headless", "--convert-to", "txt:Text", "--outdir", str(out_dir), str(path)],
        capture_output=True, timeout=120, check=True,
    )
    txt_path = out_dir / (path.stem + ".txt")
    text = txt_path.read_text(encoding="utf-8", errors="replace")
    return text


def extract_pdf(path: Path) -> tuple[str, list[dict], list]:
    import pdfplumber

    texts = []
    tables = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                texts.append(page_text)
            for t in page.extract_tables():
                if t:
                    tables.append(t)
    raw_text = "\n".join(texts)
    sections = [{"heading": "root", "text": raw_text}]
    return raw_text, sections, tables


def _text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def extract_spreadsheet(path: Path) -> tuple[str, list[dict], list, list[dict]]:
    """Extract visible values with stable sheet/cell locators."""
    import openpyxl
    from openpyxl.utils.cell import quote_sheetname

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sections: list[dict] = []
    tables: list[list[list[str]]] = []
    locators: list[dict] = []
    raw_parts: list[str] = []
    try:
        for sheet in workbook.worksheets:
            rows: list[list[str]] = []
            lines: list[str] = []
            sheet_name = quote_sheetname(sheet.title)
            for row in sheet.iter_rows():
                values = [_text_value(cell.value) for cell in row]
                while values and not values[-1]:
                    values.pop()
                if not any(values):
                    continue
                rows.append(values)
                for cell in row[:len(values)]:
                    value = _text_value(cell.value)
                    if not value:
                        continue
                    locator = f"{sheet_name}!{cell.coordinate}"
                    locators.append({"locator": locator, "value": value})
                    lines.append(f"{locator}: {value}")
            if rows:
                tables.append(rows)
            text = "\n".join(lines)
            sections.append({"heading": sheet.title, "text": text})
            if text:
                raw_parts.append(text)
    finally:
        workbook.close()
    return "\n\n".join(raw_parts), sections, tables, locators


def _read_delimited(path: Path, delimiter: str) -> tuple[str, list[dict], list, list[dict]]:
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    rows = [[cell.strip() for cell in row] for row in csv.reader(text.splitlines(), delimiter=delimiter)]
    rows = [row for row in rows if any(row)]
    locators: list[dict] = []
    locator_lines: list[str] = []
    try:
        from openpyxl.utils import get_column_letter
    except ImportError:  # pragma: no cover - runtime check reports this first
        def get_column_letter(index: int) -> str:
            result = ""
            while index:
                index, remainder = divmod(index - 1, 26)
                result = chr(65 + remainder) + result
            return result
    for row_index, row in enumerate(rows, start=1):
        for column_index, value in enumerate(row, start=1):
            if not value:
                continue
            locator = f"Sheet1!{get_column_letter(column_index)}{row_index}"
            locators.append({"locator": locator, "value": value})
            locator_lines.append(f"{locator}: {value}")
    locator_text = "\n".join(locator_lines)
    return locator_text, [{"heading": "Sheet1", "text": locator_text}], [rows] if rows else [], locators


def extract(path: Path) -> dict:
    suffix = path.suffix.lower()
    locators: list[dict] = []

    if suffix == ".docx":
        raw_text, sections, tables = extract_docx(path)
        detected = "docx"
    elif suffix == ".doc":
        subtype = detect_doc_subtype(path)
        if subtype == "html_mime":
            raw_text, sections, tables = extract_html_mime(path)
            detected = "html_mime"
        elif subtype == "docx_zip":
            raw_text, sections, tables = extract_docx(path)
            detected = "docx"
        else:
            text = convert_doc_binary_to_text(path)
            sections = [{"heading": "root", "text": text}]
            tables = []
            raw_text = text
            detected = "doc_binary"
    elif suffix == ".pdf":
        raw_text, sections, tables = extract_pdf(path)
        detected = "pdf"
    elif suffix == ".html":
        raw_text, sections, tables = extract_from_html_string(path.read_text(encoding="utf-8", errors="replace"))
        detected = "html"
    elif suffix in (".xlsx", ".xlsm"):
        raw_text, sections, tables, locators = extract_spreadsheet(path)
        detected = "spreadsheet"
    elif suffix in (".csv", ".tsv"):
        raw_text, sections, tables, locators = _read_delimited(path, "," if suffix == ".csv" else "\t")
        detected = suffix.lstrip(".")
    elif suffix in (".md", ".txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        raw_text = text
        sections = [{"heading": "root", "text": text}]
        tables = []
        detected = suffix.lstrip(".")
    else:
        raise ValueError(f"Неподдерживаемый формат протокола: {suffix}")

    return {
        "source_file": str(path),
        "detected_type": detected,
        "sections": sections,
        "tables": tables,
        "raw_text": raw_text,
        "locators": locators,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol_path", help="Путь к файлу протокола")
    parser.add_argument("-o", "--out", help="Путь для сохранения JSON (по умолчанию stdout)")
    args = parser.parse_args()

    path = Path(args.protocol_path)
    if not path.exists():
        print(json.dumps({"error": f"файл не найден: {path}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    result = extract(path)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Сохранено: {args.out}")
    else:
        print(output)


if __name__ == "__main__":
    main()
