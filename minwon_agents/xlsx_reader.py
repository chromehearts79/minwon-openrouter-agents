from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}


@dataclass(frozen=True)
class Minwon:
    request_id: str
    title: str
    body: str


def load_minwons(path: str | Path) -> list[Minwon]:
    path = Path(path)
    with zipfile.ZipFile(path) as zf:
        shared_strings = _read_shared_strings(zf)
        sheet_path = _first_sheet_path(zf)
        rows = _read_rows(zf, sheet_path, shared_strings)

    if not rows:
        return []

    header = [str(v or "").strip() for v in rows[0]]
    idx = {name: i for i, name in enumerate(header)}
    required = ["민원신청번호", "제목", "본문"]
    missing = [name for name in required if name not in idx]
    if missing:
        raise ValueError(f"Missing required XLSX columns: {', '.join(missing)}")

    items: list[Minwon] = []
    for row in rows[1:]:
        request_id = _cell(row, idx["민원신청번호"])
        title = html.unescape(_cell(row, idx["제목"]))
        body = html.unescape(_cell(row, idx["본문"]))
        if request_id or title or body:
            items.append(Minwon(request_id=request_id, title=title, body=body))
    return items


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for si in root.findall("a:si", NS):
        strings.append("".join(t.text or "" for t in si.findall(".//a:t", NS)))
    return strings


def _first_sheet_path(zf: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("r:Relationship", REL_NS)}
    sheet = workbook.find("a:sheets/a:sheet", NS)
    if sheet is None:
        raise ValueError("Workbook has no sheets")
    rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
    target = relmap[rid]
    candidate = "xl/" + target if not target.startswith("/") else target[1:]
    if candidate in zf.namelist():
        return candidate
    return "xl/worksheets/" + target.split("/")[-1]


def _read_rows(zf: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read(sheet_path))
    rows: list[list[str]] = []
    for row in root.findall("a:sheetData/a:row", NS):
        values: list[str] = []
        for cell in row.findall("a:c", NS):
            idx = _col_idx(cell.attrib.get("r", "A1"))
            while len(values) < idx:
                values.append("")
            values.append(_cell_value(cell, shared_strings))
        rows.append(values)
    return rows


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//a:t", NS))
    value = cell.find("a:v", NS)
    if value is None or value.text is None:
        return ""
    raw = value.text
    if kind == "s":
        return shared_strings[int(raw)]
    return raw


def _col_idx(ref: str) -> int:
    col = re.sub(r"[^A-Z]", "", ref.upper())
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _cell(row: list[str], idx: int) -> str:
    if idx >= len(row):
        return ""
    return str(row[idx] or "").strip()

