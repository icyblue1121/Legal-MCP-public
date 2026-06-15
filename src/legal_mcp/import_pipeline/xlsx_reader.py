"""Small stdlib XLSX reader for first-sheet tabular imports."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from legal_mcp.import_pipeline.models import SourceRow

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_xlsx(path: Path) -> tuple[list[str], list[SourceRow]]:
    with zipfile.ZipFile(path) as archive:
        sheet_path = _first_sheet_path(archive)
        shared_strings = _read_shared_strings(archive)
        root = ElementTree.fromstring(archive.read(sheet_path))

    table: list[list[str]] = []
    for row_element in root.findall(f".//{MAIN_NS}row"):
        row_index = int(row_element.attrib.get("r", len(table) + 1))
        while len(table) < row_index:
            table.append([])
        row_values: list[str] = []
        for cell in row_element.findall(f"{MAIN_NS}c"):
            column_index = _column_index(cell.attrib.get("r", "A1"))
            while len(row_values) < column_index:
                row_values.append("")
            row_values[column_index - 1] = _cell_text(cell, shared_strings)
        table[row_index - 1] = row_values

    if not table:
        return [], []

    headers = [value.strip() for value in table[0]]
    rows = [
        SourceRow(
            row_number=index,
            values={
                header: row[column_index] if column_index < len(row) else ""
                for column_index, header in enumerate(headers)
            },
        )
        for index, row in enumerate(table[1:], start=2)
    ]
    return headers, rows


def _first_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    first_sheet = workbook.find(f".//{MAIN_NS}sheet")
    if first_sheet is None:
        raise ValueError("XLSX workbook has no sheets")
    relation_id = first_sheet.attrib[f"{OFFICE_REL_NS}id"]

    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relationship in relationships.findall(f"{REL_NS}Relationship"):
        if relationship.attrib.get("Id") == relation_id:
            target = relationship.attrib["Target"]
            return f"xl/{target}" if not target.startswith("/") else target[1:]
    raise ValueError("XLSX workbook first sheet relationship is missing")


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for item in root.findall(f"{MAIN_NS}si"):
        texts = [node.text or "" for node in item.findall(f".//{MAIN_NS}t")]
        values.append("".join(texts))
    return values


def _column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference.upper())
    if not letters:
        return 1
    value = 0
    for letter in letters.group(0):
        value = value * 26 + ord(letter) - 64
    return value


def _cell_text(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text = cell.find(f".//{MAIN_NS}t")
        return text.text if text is not None and text.text is not None else ""

    value = cell.find(f"{MAIN_NS}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared_strings[int(value.text)]
    return value.text
