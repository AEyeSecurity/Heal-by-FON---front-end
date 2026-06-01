#!/usr/bin/env python3
"""HEAL by FON canon sheet intake.

Reads a small CSV/XLSX canon sheet, normalizes the rows, runs structural
checks inspired by the original Colab prototype, and writes machine-readable
outputs for the API and n8n.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path


RSID_RE = re.compile(r"\brs\d+\b", re.IGNORECASE)
GENE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,24}$")
EMPTY_VALUES = {"", "nan", "none", "null"}

CATEGORY_HINTS = {
    "adhd",
    "detox pathways",
    "detox / toxin load / emf sensitivity",
    "foundational systems resilience",
    "gut brain & microbiome resiliance",
    "gut-brain axis",
    "gut microbiome-neuroimmune interaction",
    "histamine & mast cell regulation",
    "immune & autoimmune risks",
    "immune function & autoimmunity",
    "language/cognition",
    "metabolic health & insulin sensitivity",
    "methylation, folate, & nutrient",
    "mitochondria & energy",
    "neurodevelopment foundations",
    "neurotransmitter",
    "nutrient processing",
    "nutrient_absorption & metabolism",
    "sensory processing",
    "sleep",
    "sleep & circadian biology",
    "sleep & circadian rhythm",
    "social processing",
    "stress reactivity & regulation",
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", text)


def is_empty(value: str) -> bool:
    return clean_cell(value).lower() in EMPTY_VALUES


def excel_col_to_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return max(1, index)


def load_csv(path: Path) -> tuple[list[list[str]], dict]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Could not decode CSV file.")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel

    rows = [[clean_cell(cell) for cell in row] for row in csv.reader(text.splitlines(), dialect)]
    return rows, {"format": "csv", "encoding": encoding}


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    strings: list[str] = []
    for si in root.iter():
        if si.tag.endswith("}si") or si.tag == "si":
            parts = []
            for node in si.iter():
                if node.tag.endswith("}t") or node.tag == "t":
                    parts.append(node.text or "")
            strings.append(clean_cell("".join(parts)))
    return strings


def first_sheet_path(zf: zipfile.ZipFile) -> str:
    # The prototype canon uses the first worksheet. Resolve it through workbook
    # relationships when possible; otherwise fall back to sheet1.xml.
    try:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except KeyError:
        return "xl/worksheets/sheet1.xml"

    rel_by_id = {}
    for rel in rels:
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rid and target:
            rel_by_id[rid] = target

    for sheet in workbook.iter():
        if not sheet.tag.endswith("}sheet") and sheet.tag != "sheet":
            continue
        rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        if rid in rel_by_id:
            target = rel_by_id[rid].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            return target.replace("\\", "/")
    return "xl/worksheets/sheet1.xml"


def cell_value(cell: ET.Element, strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        parts = []
        for node in cell.iter():
            if node.tag.endswith("}t") or node.tag == "t":
                parts.append(node.text or "")
        return clean_cell("".join(parts))

    value_node = None
    for child in cell:
        if child.tag.endswith("}v") or child.tag == "v":
            value_node = child
            break
    raw = "" if value_node is None else value_node.text or ""
    if cell_type == "s":
        try:
            return strings[int(raw)]
        except (ValueError, IndexError):
            return ""
    return clean_cell(raw)


def load_xlsx(path: Path) -> tuple[list[list[str]], dict]:
    with zipfile.ZipFile(path) as zf:
        strings = shared_strings(zf)
        sheet_path = first_sheet_path(zf)
        try:
            root = ET.fromstring(zf.read(sheet_path))
        except KeyError as exc:
            raise ValueError("Could not find first worksheet in XLSX file.") from exc

        rows: list[list[str]] = []
        for row in root.iter():
            if not row.tag.endswith("}row") and row.tag != "row":
                continue
            values: dict[int, str] = {}
            max_index = 0
            for cell in row:
                if not cell.tag.endswith("}c") and cell.tag != "c":
                    continue
                ref = cell.attrib.get("r", "")
                col_index = excel_col_to_index(ref) if ref else max_index + 1
                values[col_index] = cell_value(cell, strings)
                max_index = max(max_index, col_index)
            if max_index:
                rows.append([values.get(index, "") for index in range(1, max_index + 1)])
        return rows, {"format": "xlsx", "sheet": Path(sheet_path).name}


def load_table(path: Path) -> tuple[list[list[str]], dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_csv(path)
    if suffix == ".xlsx":
        return load_xlsx(path)
    raise ValueError("Only .csv and .xlsx canon files are accepted.")


def extract_rsids(*cells: str) -> list[str]:
    seen = []
    for cell in cells:
        for match in RSID_RE.findall(cell or ""):
            rsid = match.lower()
            if rsid not in seen:
                seen.append(rsid)
    return seen


def category_score(value: str) -> bool:
    text = clean_cell(value).lower()
    if text in CATEGORY_HINTS:
        return True
    if not text:
        return False
    return any(hint in text or text in hint for hint in CATEGORY_HINTS)


def looks_like_gene(value: str) -> bool:
    text = clean_cell(value).upper()
    if not GENE_RE.match(text):
        return False
    return not text.startswith("RS")


def extract_gene(value: str) -> str:
    text = clean_cell(value).upper()
    text = text.replace("Œ≤", "BETA").replace("Β", "BETA")
    token = re.split(r"[\s(/]", text, maxsplit=1)[0].strip()
    token = re.sub(r"[^A-Z0-9._-]", "", token)
    if looks_like_gene(token):
        return token
    return ""


def extract_category(value: str, next_cell: str, rsids: list[str]) -> str:
    text = clean_cell(value)
    if not text:
        return ""
    if RSID_RE.fullmatch(text):
        return ""
    # The canon is a lightweight four-column sheet. If the next column is a
    # gene-like label and the row has an rsID, keep the first column as category
    # even when the category is not yet in the curated module list.
    if rsids and extract_gene(next_cell):
        return text
    return text if category_score(text) else ""


def normalize_rows(raw_rows: list[list[str]]) -> tuple[list[dict], dict]:
    nonempty_rows = [row for row in raw_rows if any(not is_empty(cell) for cell in row)]
    max_columns = max((len(row) for row in nonempty_rows), default=0)
    normalized = []

    for index, row in enumerate(nonempty_rows, start=1):
        padded = [clean_cell(row[col]) if col < len(row) else "" for col in range(4)]
        col_a, col_b, col_c, col_d = padded
        all_cells = [clean_cell(cell) for cell in row]
        rsids = extract_rsids(*all_cells)

        category = extract_category(col_a, col_b, rsids)
        gene = extract_gene(col_b)
        rsid = rsids[0] if rsids else ""
        effect = col_d or (col_c if col_c.lower() != rsid else "")

        structural_ok = bool(category and gene and rsid)
        if not rsid:
            source_group = "revision_manual"
            check_status = "no_rsid_detected"
        elif structural_ok:
            source_group = "df_ok"
            check_status = "ok"
        else:
            source_group = "revision_manual"
            check_status = "needs_column_review"

        normalized.append(
            {
                "row_id": index,
                "source_group": source_group,
                "check_status": check_status,
                "category": category,
                "gene": gene,
                "rsid": rsid,
                "effect": effect,
                "rsids_found": ";".join(rsids),
                "col_A": col_a,
                "col_B": col_b,
                "col_C": col_c,
                "col_D": col_d,
                "extra_columns": max(0, len(row) - 4),
            }
        )

    rsid_counts = Counter(row["rsid"] for row in normalized if row["rsid"])
    for row in normalized:
        if row["source_group"] == "df_ok" and rsid_counts[row["rsid"]] > 1:
            row["source_group"] = "rsids_repetidos"
            row["check_status"] = "duplicate_rsid"

    category_counts = Counter(row["category"] or "(uncategorized)" for row in normalized)
    source_group_counts = Counter(row["source_group"] for row in normalized)
    metadata = {
        "raw_rows_total": len(raw_rows),
        "rows_total": len(nonempty_rows),
        "rows_nonempty": len(nonempty_rows),
        "max_columns_detected": max_columns,
        "rows_with_rsid": sum(1 for row in normalized if row["rsid"]),
        "unique_rsids": len(rsid_counts),
        "duplicate_rsids": sum(1 for _rsid, count in rsid_counts.items() if count > 1),
        "category_counts": dict(category_counts),
        "source_group_counts": dict(source_group_counts),
    }
    return normalized, metadata


def build_rsid_outputs(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    long_rows = []
    grouped: dict[str, dict] = {}
    for row in rows:
        rsids = [rsid for rsid in row["rsids_found"].split(";") if rsid]
        for rsid in rsids:
            long_rows.append(
                {
                    "row_id": row["row_id"],
                    "rsid": rsid,
                    "source_table": row["source_group"],
                    "category": row["category"],
                    "gene": row["gene"],
                }
            )
            item = grouped.setdefault(
                rsid,
                {
                    "rsid": rsid,
                    "n_source_rows": 0,
                    "source_rows": [],
                    "categories": set(),
                    "genes": set(),
                    "source_tables": set(),
                },
            )
            item["n_source_rows"] += 1
            item["source_rows"].append(str(row["row_id"]))
            if row["category"]:
                item["categories"].add(row["category"])
            if row["gene"]:
                item["genes"].add(row["gene"])
            item["source_tables"].add(row["source_group"])

    master = []
    for rsid in sorted(grouped):
        item = grouped[rsid]
        master.append(
            {
                "rsid": rsid,
                "n_source_rows": item["n_source_rows"],
                "source_rows": ",".join(item["source_rows"]),
                "source_tables": " | ".join(sorted(item["source_tables"])),
                "categories": " | ".join(sorted(item["categories"])),
                "genes": " | ".join(sorted(item["genes"])),
                "assembly_name": "",
                "chrom": "",
                "pos": "",
                "end": "",
                "ref": "",
                "alt": "",
                "allele_string": "",
                "api_status": "",
                "api_note": "",
            }
        )
    return long_rows, master


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def process(input_path: Path, output_dir: Path, source_file_name: str | None = None) -> dict:
    started_at = utc_now()
    errors: list[str] = []
    warnings: list[str] = []
    source_file_name = source_file_name or input_path.name

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if input_path.stat().st_size <= 0:
        raise ValueError("Input file is empty.")

    raw_rows, load_meta = load_table(input_path)
    rows, meta = normalize_rows(raw_rows)
    rsids_long, rsid_master = build_rsid_outputs(rows)

    if meta["rows_nonempty"] == 0:
        errors.append("Canon file has no non-empty rows.")
    if meta["rows_with_rsid"] == 0:
        errors.append("No rsID values were detected.")
    if meta["source_group_counts"].get("revision_manual", 0):
        warnings.append("Some rows require manual column review.")
    if meta["duplicate_rsids"]:
        warnings.append("Some rsIDs are repeated across canon rows.")
    if meta["max_columns_detected"] > 4:
        warnings.append("More than four columns were detected; extra columns were preserved as a count.")

    exact_key_counts = Counter(
        (
            row["category"].lower(),
            row["gene"].lower(),
            row["rsid"].lower(),
            row["effect"].lower(),
        )
        for row in rows
        if row["rsid"]
    )
    exact_duplicates = sum(1 for _key, count in exact_key_counts.items() if count > 1)

    status = "invalid" if errors else "warning" if warnings else "valid"
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_fields = [
        "row_id",
        "source_group",
        "check_status",
        "category",
        "gene",
        "rsid",
        "effect",
        "rsids_found",
        "col_A",
        "col_B",
        "col_C",
        "col_D",
        "extra_columns",
    ]
    write_csv(output_dir / "canon_clean_rows.csv", rows, clean_fields)
    write_csv(output_dir / "targets_ok.csv", [row for row in rows if row["source_group"] == "df_ok"], clean_fields)
    write_csv(
        output_dir / "targets_rsids_repetidos.csv",
        [row for row in rows if row["source_group"] == "rsids_repetidos"],
        clean_fields,
    )
    write_csv(
        output_dir / "targets_revision_manual.csv",
        [row for row in rows if row["source_group"] == "revision_manual"],
        clean_fields,
    )
    write_csv(output_dir / "rsids_long.csv", rsids_long, ["row_id", "category", "gene", "rsid", "source_table"])
    write_csv(
        output_dir / "rsid_master.csv",
        rsid_master,
        [
            "rsid",
            "n_source_rows",
            "source_rows",
            "source_tables",
            "categories",
            "genes",
            "assembly_name",
            "chrom",
            "pos",
            "end",
            "ref",
            "alt",
            "allele_string",
            "api_status",
            "api_note",
        ],
    )

    preview_rows = rows[:200]
    preview = {
        "generatedAt": utc_now(),
        "rows": preview_rows,
        "columns": clean_fields,
    }
    (output_dir / "canon_preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")

    completed_at = utc_now()
    summary = {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "sourceFileName": source_file_name,
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "metadata": {
            **load_meta,
            **meta,
            "size_bytes": input_path.stat().st_size,
            "exact_duplicate_groups": exact_duplicates,
            "rsids_long_rows": len(rsids_long),
            "rsid_master_rows": len(rsid_master),
        },
        "outputs": {
            "cleanRowsCsv": str(output_dir / "canon_clean_rows.csv"),
            "targetsOkCsv": str(output_dir / "targets_ok.csv"),
            "targetsRepeatedRsidsCsv": str(output_dir / "targets_rsids_repetidos.csv"),
            "targetsManualReviewCsv": str(output_dir / "targets_revision_manual.csv"),
            "rsidsLongCsv": str(output_dir / "rsids_long.csv"),
            "rsidMasterCsv": str(output_dir / "rsid_master.csv"),
            "previewJson": str(output_dir / "canon_preview.json"),
        },
        "timestamps": {
            "startedAt": started_at,
            "completedAt": completed_at,
        },
    }
    (output_dir / "canon_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process HEAL canon CSV/XLSX intake.")
    parser.add_argument("--input", help="Input .csv or .xlsx path.")
    parser.add_argument("--output-dir", help="Directory for generated outputs.")
    parser.add_argument("--source-file-name", default="", help="Original file name shown to users.")
    parser.add_argument("--input-json-base64", default="", help="Base64 JSON payload for n8n wrapper use.")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.input = payload.get("inputPath") or payload.get("input")
        args.output_dir = payload.get("outputDir") or payload.get("output_dir")
        args.source_file_name = payload.get("sourceFileName") or payload.get("fileName") or ""
    if not args.input or not args.output_dir:
        parser.error("--input and --output-dir are required.")
    return args


def main() -> int:
    try:
        args = parse_args()
        summary = process(Path(args.input), Path(args.output_dir), args.source_file_name or None)
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["status"] in {"valid", "warning"} else 2
    except Exception as exc:  # noqa: BLE001
        failure = {
            "status": "invalid",
            "errors": [str(exc)],
            "warnings": [],
            "timestamps": {"completedAt": utc_now()},
        }
        print(json.dumps(failure, ensure_ascii=False), file=sys.stdout)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
