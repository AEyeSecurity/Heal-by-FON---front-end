#!/usr/bin/env python3
"""HEAL by FON canon sheet intake and preprocessing."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path


RSID_RE = re.compile(r"\brs\d+\b", re.IGNORECASE)
GENE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,24}$")
EMPTY_VALUES = {"", "nan", "none", "null"}
LEGACY_SCHEMA_VERSION = "legacy_rsid_canon"
LEGACY_ADAPTER = "legacy_snp_position_adapter"
GENE_MODULE_SCHEMA_VERSION = "gene_module_v2"
GENE_MODULE_ADAPTER = "gene_module_canon_adapter"
TRANSCRIPT_POLICY = "BROAD_CAPTURE_WITH_PRIORITY"
CANON_V2_HEADERS = [
    "Module ID",
    "Module Name",
    "Tier",
    "Life Stage",
    "Default Epistemic Mode",
    "Harm Risk",
    "Module Status",
    "Module Purpose",
    "Explicit Exclusions",
    "System Within Module",
    "Gene Symbol",
    "Full Gene Name",
    "Evidence Tier",
    "Misinterpretation Risk",
    "Myth Correction Required",
    "Canonical Version",
    "Notes",
]
CANON_V2_SQL = """
CREATE TABLE canon_versions (
    canon_version_id TEXT PRIMARY KEY,
    canon_name TEXT,
    canon_schema_version TEXT,
    source_filename TEXT,
    assembly TEXT,
    annotation_source TEXT,
    annotation_release TEXT,
    transcript_policy TEXT,
    created_at TEXT,
    notes TEXT
);
CREATE TABLE canon_rows (
    canon_row_id TEXT PRIMARY KEY,
    canon_version_id TEXT,
    module_id TEXT,
    module_name TEXT,
    tier TEXT,
    life_stage TEXT,
    epistemic_mode TEXT,
    harm_risk TEXT,
    module_status TEXT,
    module_purpose TEXT,
    explicit_exclusions TEXT,
    system_within_module TEXT,
    gene_symbol_original TEXT,
    full_gene_name TEXT,
    evidence_tier TEXT,
    misinterpretation_risk TEXT,
    myth_correction_required TEXT,
    canonical_version TEXT,
    notes TEXT,
    row_status TEXT,
    is_draft INTEGER,
    gene_symbol_normalized TEXT,
    FOREIGN KEY (canon_version_id) REFERENCES canon_versions(canon_version_id)
);
CREATE TABLE genes (
    gene_id TEXT PRIMARY KEY,
    canon_version_id TEXT,
    gene_symbol_original TEXT,
    approved_symbol TEXT,
    ensembl_gene_id TEXT,
    full_gene_name TEXT,
    biotype TEXT,
    resolution_status TEXT,
    resolution_notes TEXT,
    FOREIGN KEY (canon_version_id) REFERENCES canon_versions(canon_version_id)
);
CREATE TABLE gene_modules (
    gene_module_id TEXT PRIMARY KEY,
    gene_id TEXT,
    canon_row_id TEXT,
    module_id TEXT,
    module_name TEXT,
    system_within_module TEXT,
    tier TEXT,
    evidence_tier TEXT,
    harm_risk TEXT,
    misinterpretation_risk TEXT,
    module_status TEXT,
    FOREIGN KEY (gene_id) REFERENCES genes(gene_id),
    FOREIGN KEY (canon_row_id) REFERENCES canon_rows(canon_row_id)
);
CREATE TABLE gene_envelopes (
    gene_id TEXT PRIMARY KEY,
    approved_symbol TEXT,
    chrom TEXT,
    start INTEGER,
    end INTEGER,
    strand INTEGER,
    assembly TEXT,
    coordinate_source TEXT,
    coordinate_release TEXT,
    coordinate_status TEXT,
    FOREIGN KEY (gene_id) REFERENCES genes(gene_id)
);
CREATE INDEX idx_gene_envelopes_chrom_start_end
ON gene_envelopes (chrom, start, end);
CREATE TABLE transcripts (
    transcript_db_id TEXT PRIMARY KEY,
    gene_id TEXT,
    ensembl_transcript_id TEXT,
    refseq_transcript_id TEXT,
    transcript_name TEXT,
    chrom TEXT,
    start INTEGER,
    end INTEGER,
    strand INTEGER,
    biotype TEXT,
    transcript_support_level TEXT,
    is_mane_select INTEGER,
    is_mane_plus_clinical INTEGER,
    is_ensembl_canonical INTEGER,
    is_primary INTEGER,
    inclusion_tier TEXT,
    inclusion_reason TEXT,
    include_for_matching INTEGER,
    FOREIGN KEY (gene_id) REFERENCES genes(gene_id)
);
CREATE INDEX idx_transcripts_gene
ON transcripts (gene_id);
CREATE INDEX idx_transcripts_chrom_start_end
ON transcripts (chrom, start, end);
CREATE TABLE feature_intervals (
    feature_id TEXT PRIMARY KEY,
    gene_id TEXT,
    transcript_db_id TEXT,
    approved_symbol TEXT,
    chrom TEXT,
    start INTEGER,
    end INTEGER,
    strand INTEGER,
    feature_type TEXT,
    feature_number TEXT,
    feature_source TEXT,
    feature_release TEXT,
    derived_feature INTEGER,
    notes TEXT,
    FOREIGN KEY (gene_id) REFERENCES genes(gene_id),
    FOREIGN KEY (transcript_db_id) REFERENCES transcripts(transcript_db_id)
);
CREATE INDEX idx_features_chrom_start_end
ON feature_intervals (chrom, start, end);
CREATE INDEX idx_features_gene_type
ON feature_intervals (gene_id, feature_type);
CREATE TABLE merged_feature_intervals (
    merged_feature_id TEXT PRIMARY KEY,
    gene_id TEXT,
    approved_symbol TEXT,
    chrom TEXT,
    start INTEGER,
    end INTEGER,
    strand INTEGER,
    merged_feature_type TEXT,
    transcript_set_scope TEXT,
    source_feature_count INTEGER,
    source_transcript_count INTEGER,
    notes TEXT,
    FOREIGN KEY (gene_id) REFERENCES genes(gene_id)
);
CREATE INDEX idx_merged_features_chrom_start_end
ON merged_feature_intervals (chrom, start, end);
CREATE INDEX idx_merged_features_gene_type
ON merged_feature_intervals (gene_id, merged_feature_type);
CREATE TABLE local_priority_rules (
    rule_id TEXT PRIMARY KEY,
    rule_order INTEGER,
    rule_name TEXT,
    feature_condition TEXT,
    transcript_condition TEXT,
    local_region_class TEXT,
    local_feature_priority TEXT,
    annotation_needed INTEGER,
    background_only INTEGER,
    explanation TEXT
);
CREATE TABLE preprocessing_warnings (
    warning_id TEXT PRIMARY KEY,
    canon_version_id TEXT,
    gene_id TEXT,
    canon_row_id TEXT,
    warning_type TEXT,
    severity TEXT,
    message TEXT,
    suggested_action TEXT,
    FOREIGN KEY (canon_version_id) REFERENCES canon_versions(canon_version_id)
);
"""

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


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", clean_cell(value).lower()).strip("_")
    return slug or "unknown"


def ensure_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def first_nonempty_row(raw_rows: list[list[str]]) -> tuple[int, list[str]]:
    for index, row in enumerate(raw_rows):
        cleaned = [clean_cell(cell) for cell in row]
        if any(cleaned):
            return index, cleaned
    return -1, []


def detect_schema_from_rows(raw_rows: list[list[str]]) -> str:
    _, header = first_nonempty_row(raw_rows)
    if header == CANON_V2_HEADERS:
        return GENE_MODULE_SCHEMA_VERSION
    return LEGACY_SCHEMA_VERSION


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
    if rsids and extract_gene(next_cell):
        return text
    return text if category_score(text) else ""


def normalize_rows_legacy(raw_rows: list[list[str]]) -> tuple[list[dict], dict]:
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
        "duplicate_rsids": sum(1 for count in rsid_counts.values() if count > 1),
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
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def process_legacy(input_path: Path, output_dir: Path, source_file_name: str) -> dict:
    started_at = utc_now()
    errors: list[str] = []
    warnings: list[str] = []
    raw_rows, load_meta = load_table(input_path)
    rows, meta = normalize_rows_legacy(raw_rows)
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
    exact_duplicates = sum(1 for count in exact_key_counts.values() if count > 1)

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

    preview = {
        "generatedAt": utc_now(),
        "rows": rows[:200],
        "columns": clean_fields,
    }
    (output_dir / "canon_preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "schemaVersion": LEGACY_SCHEMA_VERSION,
        "adapter": LEGACY_ADAPTER,
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
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    (output_dir / "canon_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def normalize_gene_symbol(value: str) -> str:
    symbol = clean_cell(value).upper()
    symbol = symbol.replace(" ", "")
    symbol = symbol.replace("Β", "BETA").replace("Œ≤", "BETA")
    return re.sub(r"[^A-Z0-9._-]", "", symbol)


def normalize_module_rows(raw_rows: list[list[str]]) -> tuple[list[dict], dict]:
    header_index, header = first_nonempty_row(raw_rows)
    if header != CANON_V2_HEADERS:
        raise ValueError("Unsupported canon schema.")
    data_rows = raw_rows[header_index + 1 :]
    rows = []
    for index, row in enumerate(data_rows, start=1):
        values = [clean_cell(row[col]) if col < len(row) else "" for col in range(len(CANON_V2_HEADERS))]
        if not any(values):
            continue
        row_map = dict(zip(CANON_V2_HEADERS, values, strict=False))
        gene_symbol_original = row_map["Gene Symbol"]
        normalized_symbol = normalize_gene_symbol(gene_symbol_original)
        module_status = row_map["Module Status"]
        has_gene = bool(normalized_symbol)
        row_status = "active_gene_row" if has_gene else "non_gene_module"
        rows.append(
            {
                "canon_row_id": f"canon-row-{index:04d}",
                "module_id": row_map["Module ID"],
                "module_name": row_map["Module Name"],
                "tier": row_map["Tier"],
                "life_stage": row_map["Life Stage"],
                "epistemic_mode": row_map["Default Epistemic Mode"],
                "harm_risk": row_map["Harm Risk"],
                "module_status": module_status,
                "module_purpose": row_map["Module Purpose"],
                "explicit_exclusions": row_map["Explicit Exclusions"],
                "system_within_module": row_map["System Within Module"],
                "gene_symbol_original": gene_symbol_original,
                "full_gene_name": row_map["Full Gene Name"],
                "evidence_tier": row_map["Evidence Tier"],
                "misinterpretation_risk": row_map["Misinterpretation Risk"],
                "myth_correction_required": row_map["Myth Correction Required"],
                "canonical_version": row_map["Canonical Version"],
                "notes": row_map["Notes"],
                "row_status": row_status,
                "is_draft": 1 if module_status.lower() == "draft" else 0,
                "gene_symbol_normalized": normalized_symbol,
            }
        )

    duplicate_gene_symbols = sum(1 for count in Counter(row["gene_symbol_normalized"] for row in rows if row["gene_symbol_normalized"]).values() if count > 1)
    metadata = {
        "rows_total": len(rows),
        "rows_with_gene": sum(1 for row in rows if row["gene_symbol_normalized"]),
        "rows_non_gene_module": sum(1 for row in rows if row["row_status"] == "non_gene_module"),
        "draft_rows": sum(1 for row in rows if row["is_draft"] == 1),
        "module_count": len({row["module_id"] for row in rows if row["module_id"]}),
        "unique_gene_symbols": len({row["gene_symbol_normalized"] for row in rows if row["gene_symbol_normalized"]}),
        "duplicate_gene_symbols": duplicate_gene_symbols,
        "header_columns": len(header),
    }
    return rows, metadata


def ensembl_server_for_assembly(assembly: str) -> str:
    if clean_cell(assembly).upper() == "GRCH37":
        return "https://grch37.rest.ensembl.org"
    return "https://rest.ensembl.org"


def http_json_cache(cache_dir: Path, key: str, url: str, headers: dict[str, str] | None = None, retries: int = 2) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{safe_slug(key)}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    request = urllib.request.Request(url, headers=headers or {"Accept": "application/json"})
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
                cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                return payload
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"HTTP request failed for {url}: {last_error}")


def hgnc_docs(cache_root: Path, field: str, value: str) -> list[dict]:
    url = f"https://rest.genenames.org/fetch/{field}/{urllib.parse.quote(value)}"
    payload = http_json_cache(cache_root / "hgnc", f"{field}_{value}", url)
    return ((payload.get("response") or {}).get("docs")) or []


def resolve_hgnc(symbol: str, cache_root: Path) -> tuple[dict | None, str]:
    docs = hgnc_docs(cache_root, "symbol", symbol)
    if docs:
        return docs[0], "approved_symbol"
    docs = hgnc_docs(cache_root, "alias_symbol", symbol)
    if len(docs) == 1:
        return docs[0], "alias_symbol"
    if len(docs) > 1:
        return None, "multiple_candidates"
    docs = hgnc_docs(cache_root, "prev_symbol", symbol)
    if len(docs) == 1:
        return docs[0], "deprecated_symbol"
    if len(docs) > 1:
        return None, "multiple_candidates"
    return None, "not_found"


def ensembl_lookup_by_id(ensembl_gene_id: str, assembly: str, cache_root: Path) -> dict:
    server = ensembl_server_for_assembly(assembly)
    url = f"{server}/lookup/id/{urllib.parse.quote(ensembl_gene_id)}?expand=1"
    return http_json_cache(cache_root / "ensembl", f"{assembly}_lookup_id_{ensembl_gene_id}", url)


def ensembl_lookup_by_symbol(symbol: str, assembly: str, cache_root: Path) -> dict:
    server = ensembl_server_for_assembly(assembly)
    url = f"{server}/lookup/symbol/homo_sapiens/{urllib.parse.quote(symbol)}?expand=1"
    return http_json_cache(cache_root / "ensembl", f"{assembly}_lookup_symbol_{symbol}", url)


def ensembl_xrefs_by_symbol(symbol: str, assembly: str, cache_root: Path) -> list[dict]:
    server = ensembl_server_for_assembly(assembly)
    url = f"{server}/xrefs/symbol/homo_sapiens/{urllib.parse.quote(symbol)}?object_type=gene"
    payload = http_json_cache(cache_root / "ensembl", f"{assembly}_xrefs_symbol_{symbol}", url)
    return payload if isinstance(payload, list) else []


def ensembl_overlap_transcripts(gene_id: str, assembly: str, cache_root: Path) -> list[dict]:
    server = ensembl_server_for_assembly(assembly)
    url = f"{server}/overlap/id/{urllib.parse.quote(gene_id)}?feature=transcript"
    payload = http_json_cache(cache_root / "ensembl", f"{assembly}_overlap_transcript_{gene_id}", url)
    return payload if isinstance(payload, list) else []


def normalize_chromosome(value: str) -> str:
    chrom = clean_cell(value).upper()
    if not chrom:
        return ""
    if chrom.startswith("CHR"):
        chrom = chrom[3:]
    if chrom == "MT":
        chrom = "M"
    return f"chr{chrom}"


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    ordered = sorted((min(start, end), max(start, end)) for start, end in intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def add_warning(warnings: list[dict], canon_version_id: str, warning_type: str, severity: str, message: str, suggested_action: str, gene_id: str = "", canon_row_id: str = "") -> None:
    warnings.append(
        {
            "warning_id": f"warning-{len(warnings) + 1:04d}",
            "canon_version_id": canon_version_id,
            "gene_id": gene_id,
            "canon_row_id": canon_row_id,
            "warning_type": warning_type,
            "severity": severity,
            "message": message,
            "suggested_action": suggested_action,
        }
    )


def candidate_rows_for_symbol(rows: list[dict], symbol: str) -> list[dict]:
    return [row for row in rows if row["gene_symbol_normalized"] == symbol]


def classify_resolution_status(hgnc_mode: str, original_symbol: str, approved_symbol: str) -> str:
    if hgnc_mode == "alias_symbol":
        return "resolved_alias"
    if hgnc_mode == "deprecated_symbol":
        return "deprecated_symbol"
    if approved_symbol and approved_symbol != original_symbol:
        return "resolved_alias"
    return "resolved_exact_symbol"


def classify_transcript_record(transcript: dict, overlap_meta: dict, hgnc_record: dict | None) -> dict:
    tags = overlap_meta.get("tag") or []
    biotype = clean_cell(transcript.get("biotype") or overlap_meta.get("biotype"))
    transcript_id = clean_cell(transcript.get("id") or overlap_meta.get("transcript_id"))
    mane_select_ids = set()
    if hgnc_record:
        mane_select_ids = {clean_cell(value).split(".")[0] for value in (hgnc_record.get("mane_select") or [])}

    is_mane_select = int("MANE_Select" in tags or transcript_id in mane_select_ids)
    is_mane_plus_clinical = int("MANE_Plus_Clinical" in tags)
    is_ensembl_canonical = int(bool(transcript.get("is_canonical") or overlap_meta.get("is_canonical")))
    is_primary = int(is_mane_select or is_mane_plus_clinical or is_ensembl_canonical)

    if is_mane_select:
        inclusion_tier = "mane_select"
    elif is_mane_plus_clinical:
        inclusion_tier = "mane_plus_clinical"
    elif is_ensembl_canonical:
        inclusion_tier = "ensembl_canonical"
    elif biotype == "protein_coding":
        inclusion_tier = "protein_coding"
    elif biotype == "retained_intron":
        inclusion_tier = "retained_intron_low_priority"
    else:
        inclusion_tier = "noncoding_relevant"

    return {
        "transcript_db_id": transcript_id,
        "ensembl_transcript_id": transcript_id,
        "refseq_transcript_id": "",
        "transcript_name": clean_cell(transcript.get("display_name") or overlap_meta.get("external_name")),
        "chrom": normalize_chromosome(transcript.get("seq_region_name") or overlap_meta.get("seq_region_name")),
        "start": ensure_int(transcript.get("start") or overlap_meta.get("start")),
        "end": ensure_int(transcript.get("end") or overlap_meta.get("end")),
        "strand": ensure_int(transcript.get("strand") or overlap_meta.get("strand")),
        "biotype": biotype,
        "transcript_support_level": clean_cell(overlap_meta.get("transcript_support_level")),
        "is_mane_select": is_mane_select,
        "is_mane_plus_clinical": is_mane_plus_clinical,
        "is_ensembl_canonical": is_ensembl_canonical,
        "is_primary": is_primary,
        "inclusion_tier": inclusion_tier,
        "inclusion_reason": inclusion_tier,
        "include_for_matching": 1,
        "tags": tags,
        "exons": transcript.get("Exon") or [],
        "translation": transcript.get("Translation") or {},
    }


def exon_segments(exons: list[dict]) -> list[tuple[int, int, int]]:
    segments = []
    for index, exon in enumerate(sorted(exons, key=lambda item: ensure_int(item.get("start"))), start=1):
        start = ensure_int(exon.get("start"))
        end = ensure_int(exon.get("end"))
        if start and end:
            segments.append((min(start, end), max(start, end), index))
    return segments


def compute_cds_and_utrs(transcript_record: dict) -> tuple[list[dict], list[dict], list[dict]]:
    features_cds: list[dict] = []
    features_utr5: list[dict] = []
    features_utr3: list[dict] = []
    translation = transcript_record.get("translation") or {}
    cds_start = ensure_int(translation.get("start"))
    cds_end = ensure_int(translation.get("end"))
    strand = ensure_int(transcript_record.get("strand"), 1)
    if not cds_start or not cds_end:
        return features_cds, features_utr5, features_utr3

    for exon_start, exon_end, exon_number in exon_segments(transcript_record.get("exons") or []):
        overlap_start = max(exon_start, min(cds_start, cds_end))
        overlap_end = min(exon_end, max(cds_start, cds_end))
        if overlap_start <= overlap_end:
            features_cds.append(
                {
                    "feature_type": "cds_interval",
                    "start": overlap_start,
                    "end": overlap_end,
                    "feature_number": str(exon_number),
                }
            )

        utr_segments = []
        if exon_start < overlap_start:
            utr_segments.append((exon_start, overlap_start - 1))
        if overlap_end < exon_end:
            utr_segments.append((overlap_end + 1, exon_end))
        if not features_cds and exon_start <= exon_end:
            utr_segments = [(exon_start, exon_end)]

        for utr_start, utr_end in utr_segments:
            if utr_start > utr_end:
                continue
            if strand >= 0:
                feature_type = "utr_5_interval" if utr_end < cds_start else "utr_3_interval"
            else:
                feature_type = "utr_3_interval" if utr_end < cds_start else "utr_5_interval"
            target = features_utr5 if feature_type == "utr_5_interval" else features_utr3
            target.append(
                {
                    "feature_type": feature_type,
                    "start": utr_start,
                    "end": utr_end,
                    "feature_number": str(exon_number),
                }
            )

    return features_cds, features_utr5, features_utr3


def compute_splice_windows(transcript_record: dict) -> list[dict]:
    exons = exon_segments(transcript_record.get("exons") or [])
    if len(exons) < 2:
        return []
    strand = ensure_int(transcript_record.get("strand"), 1)
    windows = []
    for left, right in zip(exons, exons[1:]):
        left_start, left_end, left_number = left
        right_start, right_end, right_number = right
        donor_label = f"{left_number}->{right_number}"
        acceptor_label = donor_label
        if strand >= 0:
            donor_center = left_end
            acceptor_center = right_start
        else:
            donor_center = right_start
            acceptor_center = left_end
        donor_start = max(1, donor_center - 2)
        donor_end = donor_center + 2
        acceptor_start = max(1, acceptor_center - 2)
        acceptor_end = acceptor_center + 2
        windows.extend(
            [
                {"feature_type": "splice_donor_window", "start": donor_start, "end": donor_end, "feature_number": donor_label},
                {"feature_type": "splice_acceptor_window", "start": acceptor_start, "end": acceptor_end, "feature_number": acceptor_label},
                {
                    "feature_type": "splice_boundary_window",
                    "start": donor_start,
                    "end": donor_end,
                    "feature_number": donor_label,
                },
                {
                    "feature_type": "splice_boundary_window",
                    "start": acceptor_start,
                    "end": acceptor_end,
                    "feature_number": acceptor_label,
                },
            ]
        )
    return windows


def build_feature_rows(gene_id: str, approved_symbol: str, transcript_record: dict, annotation_release: str) -> list[dict]:
    base = {
        "gene_id": gene_id,
        "transcript_db_id": transcript_record["transcript_db_id"],
        "approved_symbol": approved_symbol,
        "chrom": transcript_record["chrom"],
        "strand": transcript_record["strand"],
        "feature_source": "Ensembl REST",
        "feature_release": annotation_release,
    }
    rows = [
        {
            **base,
            "feature_type": "transcript_body",
            "feature_number": "",
            "start": transcript_record["start"],
            "end": transcript_record["end"],
            "derived_feature": 0,
            "notes": transcript_record["inclusion_tier"],
        }
    ]
    for exon_start, exon_end, exon_number in exon_segments(transcript_record.get("exons") or []):
        rows.append(
            {
                **base,
                "feature_type": "exon_interval",
                "feature_number": str(exon_number),
                "start": exon_start,
                "end": exon_end,
                "derived_feature": 0,
                "notes": transcript_record["biotype"],
            }
        )
    cds_rows, utr5_rows, utr3_rows = compute_cds_and_utrs(transcript_record)
    for item in cds_rows + utr5_rows + utr3_rows + compute_splice_windows(transcript_record):
        rows.append(
            {
                **base,
                "feature_type": item["feature_type"],
                "feature_number": item.get("feature_number", ""),
                "start": item["start"],
                "end": item["end"],
                "derived_feature": 1 if item["feature_type"].startswith("splice_") else 0,
                "notes": transcript_record["biotype"],
            }
        )
    for index, row in enumerate(rows, start=1):
        row["feature_id"] = f"{gene_id}:{transcript_record['transcript_db_id']}:{row['feature_type']}:{index:04d}"
    return rows


def merged_feature_rows(gene_id: str, approved_symbol: str, transcripts: list[dict], feature_rows: list[dict]) -> list[dict]:
    transcript_by_id = {row["transcript_db_id"]: row for row in transcripts}
    grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
    transcript_scopes: dict[str, set[str]] = defaultdict(set)
    transcript_counts: dict[str, set[str]] = defaultdict(set)

    for row in feature_rows:
        transcript = transcript_by_id.get(row["transcript_db_id"], {})
        biotype = transcript.get("biotype", "")
        key_candidates = []
        if row["feature_type"] == "transcript_body":
            key_candidates.append("transcript_body_union")
            if biotype == "retained_intron":
                key_candidates.append("retained_intron_body_union")
        elif row["feature_type"] == "exon_interval":
            if transcript.get("is_mane_select"):
                key_candidates.append("mane_exon_union")
            if biotype == "protein_coding":
                key_candidates.append("protein_coding_exon_union")
            else:
                key_candidates.append("noncoding_exon_union")
        elif row["feature_type"] == "cds_interval":
            if transcript.get("is_mane_select"):
                key_candidates.append("mane_cds_union")
            if biotype == "protein_coding":
                key_candidates.append("protein_coding_cds_union")
        elif row["feature_type"] in {"utr_5_interval", "utr_3_interval"}:
            key_candidates.append("utr_union")
        elif row["feature_type"].startswith("splice_"):
            key_candidates.append("splice_window_union")

        for key in key_candidates:
            grouped[key].append((ensure_int(row["start"]), ensure_int(row["end"])))
            transcript_counts[key].add(row["transcript_db_id"])
            transcript_scopes[key].add(transcript.get("inclusion_tier", ""))

    merged_rows = []
    for merged_type, intervals in grouped.items():
        for index, (start, end) in enumerate(merge_intervals(intervals), start=1):
            merged_rows.append(
                {
                    "merged_feature_id": f"{gene_id}:{merged_type}:{index:04d}",
                    "gene_id": gene_id,
                    "approved_symbol": approved_symbol,
                    "chrom": transcripts[0]["chrom"] if transcripts else "",
                    "start": start,
                    "end": end,
                    "strand": transcripts[0]["strand"] if transcripts else 0,
                    "merged_feature_type": merged_type,
                    "transcript_set_scope": " | ".join(sorted(scope for scope in transcript_scopes[merged_type] if scope)),
                    "source_feature_count": len(intervals),
                    "source_transcript_count": len(transcript_counts[merged_type]),
                    "notes": merged_type,
                }
            )
    return merged_rows


def seed_local_priority_rules() -> list[dict]:
    return [
        {
            "rule_id": "R1",
            "rule_order": 1,
            "rule_name": "MANE CDS overlap",
            "feature_condition": "mane_cds_union",
            "transcript_condition": "mane_select",
            "local_region_class": "mane_cds_overlap",
            "local_feature_priority": "high",
            "annotation_needed": 1,
            "background_only": 0,
            "explanation": "Overlap with MANE CDS union.",
        },
        {
            "rule_id": "R2",
            "rule_order": 2,
            "rule_name": "Splice region candidate",
            "feature_condition": "splice_window_union",
            "transcript_condition": "any",
            "local_region_class": "splice_region_candidate",
            "local_feature_priority": "high",
            "annotation_needed": 1,
            "background_only": 0,
            "explanation": "Overlap with precomputed splice windows.",
        },
        {
            "rule_id": "R3",
            "rule_order": 3,
            "rule_name": "Alternative protein coding CDS",
            "feature_condition": "protein_coding_cds_union",
            "transcript_condition": "non_mane",
            "local_region_class": "alternative_protein_coding_cds_overlap",
            "local_feature_priority": "medium_high",
            "annotation_needed": 1,
            "background_only": 0,
            "explanation": "Overlap with non-MANE protein-coding CDS.",
        },
        {
            "rule_id": "R4",
            "rule_order": 4,
            "rule_name": "Protein coding exon non-CDS",
            "feature_condition": "protein_coding_exon_union",
            "transcript_condition": "non_cds",
            "local_region_class": "protein_coding_exon_non_cds_overlap",
            "local_feature_priority": "medium",
            "annotation_needed": 1,
            "background_only": 0,
            "explanation": "Overlap with protein-coding exon but not CDS.",
        },
        {
            "rule_id": "R5",
            "rule_order": 5,
            "rule_name": "UTR overlap",
            "feature_condition": "utr_union",
            "transcript_condition": "any",
            "local_region_class": "utr_overlap",
            "local_feature_priority": "medium",
            "annotation_needed": 1,
            "background_only": 0,
            "explanation": "Overlap with UTR union.",
        },
        {
            "rule_id": "R6",
            "rule_order": 6,
            "rule_name": "Noncoding exon overlap",
            "feature_condition": "noncoding_exon_union",
            "transcript_condition": "noncoding",
            "local_region_class": "noncoding_exon_overlap",
            "local_feature_priority": "exploratory",
            "annotation_needed": 0,
            "background_only": 0,
            "explanation": "Overlap with noncoding exon union.",
        },
        {
            "rule_id": "R7",
            "rule_order": 7,
            "rule_name": "Intronic transcript overlap",
            "feature_condition": "transcript_body_union",
            "transcript_condition": "intronic_only",
            "local_region_class": "intronic_transcript_overlap",
            "local_feature_priority": "low",
            "annotation_needed": 0,
            "background_only": 1,
            "explanation": "Inside transcript body but outside exon/CDS/UTR/splice.",
        },
        {
            "rule_id": "R8",
            "rule_order": 8,
            "rule_name": "Gene envelope only",
            "feature_condition": "gene_envelope",
            "transcript_condition": "envelope_only",
            "local_region_class": "gene_envelope_only",
            "local_feature_priority": "background",
            "annotation_needed": 0,
            "background_only": 1,
            "explanation": "Inside gene envelope but outside transcript unions.",
        },
    ]


def resolve_gene_identity(symbol: str, full_gene_name: str, assembly: str, cache_root: Path) -> tuple[dict | None, str, str]:
    hgnc_record, hgnc_mode = resolve_hgnc(symbol, cache_root)
    approved_symbol = clean_cell((hgnc_record or {}).get("symbol")) or symbol
    ensembl_gene_id = clean_cell((hgnc_record or {}).get("ensembl_gene_id"))

    try:
        if ensembl_gene_id:
            gene_lookup = ensembl_lookup_by_id(ensembl_gene_id, assembly, cache_root)
        else:
            gene_lookup = ensembl_lookup_by_symbol(approved_symbol, assembly, cache_root)
            ensembl_gene_id = clean_cell(gene_lookup.get("id"))
    except Exception:
        try:
            gene_lookup = ensembl_lookup_by_symbol(symbol, assembly, cache_root)
            ensembl_gene_id = clean_cell(gene_lookup.get("id"))
        except Exception:
            xrefs = ensembl_xrefs_by_symbol(symbol, assembly, cache_root)
            if len(xrefs) > 1:
                return None, "resolved_multiple_candidates", f"Multiple Ensembl gene candidates for {symbol}."
            if len(xrefs) == 1:
                gene_lookup = ensembl_lookup_by_id(clean_cell(xrefs[0].get("id")), assembly, cache_root)
                ensembl_gene_id = clean_cell(gene_lookup.get("id"))
            else:
                return None, "unresolved_symbol", f"Could not resolve {symbol} through HGNC + Ensembl."

    status = classify_resolution_status(hgnc_mode, symbol, approved_symbol)
    notes = clean_cell(gene_lookup.get("description")) or full_gene_name
    return {
        "gene_symbol_original": symbol,
        "approved_symbol": approved_symbol,
        "ensembl_gene_id": ensembl_gene_id or clean_cell(gene_lookup.get("id")),
        "full_gene_name": clean_cell((hgnc_record or {}).get("name")) or full_gene_name or clean_cell(gene_lookup.get("description")),
        "biotype": clean_cell(gene_lookup.get("biotype")),
        "chrom": normalize_chromosome(gene_lookup.get("seq_region_name")),
        "start": ensure_int(gene_lookup.get("start")),
        "end": ensure_int(gene_lookup.get("end")),
        "strand": ensure_int(gene_lookup.get("strand")),
        "assembly_name": clean_cell(gene_lookup.get("assembly_name")) or assembly,
        "gene_lookup": gene_lookup,
        "hgnc_record": hgnc_record,
    }, status, notes


def create_gene_master_row(gene_row: dict, related_rows: list[dict], transcript_rows: list[dict], feature_rows: list[dict], merged_rows: list[dict]) -> dict:
    return {
        "gene_id": gene_row["gene_id"],
        "approved_symbol": gene_row["approved_symbol"],
        "gene_symbol_original": gene_row["gene_symbol_original"],
        "ensembl_gene_id": gene_row["ensembl_gene_id"],
        "full_gene_name": gene_row["full_gene_name"],
        "biotype": gene_row["biotype"],
        "resolution_status": gene_row["resolution_status"],
        "resolution_notes": gene_row["resolution_notes"],
        "chrom": gene_row["chrom"],
        "start": gene_row["start"],
        "end": gene_row["end"],
        "strand": gene_row["strand"],
        "assembly": gene_row["assembly"],
        "module_ids": " | ".join(sorted({row["module_id"] for row in related_rows if row["module_id"]})),
        "module_names": " | ".join(sorted({row["module_name"] for row in related_rows if row["module_name"]})),
        "systems_within_module": " | ".join(sorted({row["system_within_module"] for row in related_rows if row["system_within_module"]})),
        "row_ids": ",".join(row["canon_row_id"] for row in related_rows),
        "transcript_count": len(transcript_rows),
        "feature_count": len(feature_rows),
        "merged_feature_count": len(merged_rows),
        "has_mane_select": "true" if any(row["is_mane_select"] for row in transcript_rows) else "false",
        "draft_module_count": sum(1 for row in related_rows if row["is_draft"] == 1),
    }


def export_gene_envelope_index(canon_version_id: str, assembly: str, gene_master_rows: list[dict], path: Path) -> None:
    chromosomes: dict[str, list[dict]] = defaultdict(list)
    for row in gene_master_rows:
        chrom = clean_cell(row.get("chrom"))
        start = ensure_int(row.get("start"))
        end = ensure_int(row.get("end"))
        if not chrom or not start or not end:
            continue
        chromosomes[chrom].append(
            {
                "gene_id": row["gene_id"],
                "symbol": row["approved_symbol"],
                "start": start,
                "end": end,
                "strand": ensure_int(row["strand"]),
                "module_ids": [item.strip() for item in clean_cell(row["module_ids"]).split("|") if item.strip()],
                "module_names": [item.strip() for item in clean_cell(row["module_names"]).split("|") if item.strip()],
                "module_statuses": [],
                "gene_biotype": row["biotype"],
            }
        )
    for chrom in chromosomes:
        chromosomes[chrom].sort(key=lambda item: (item["start"], item["end"], item["symbol"]))
    payload = {"canon_version_id": canon_version_id, "assembly": assembly, "chromosomes": chromosomes}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_merged_feature_index(canon_version_id: str, assembly: str, merged_rows: list[dict], path: Path) -> None:
    features_by_gene: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    gene_metadata: dict[str, dict] = {}
    for row in merged_rows:
        gene_id = row["gene_id"]
        feature_type = row["merged_feature_type"]
        features_by_gene[gene_id][feature_type].append(
            {"start": ensure_int(row["start"]), "end": ensure_int(row["end"]), "strand": ensure_int(row["strand"])}
        )
        gene_metadata.setdefault(
            gene_id,
            {
                "approved_symbol": row["approved_symbol"],
                "chrom": row["chrom"],
                "strand": ensure_int(row["strand"]),
            },
        )
    payload = {
        "canon_version_id": canon_version_id,
        "assembly": assembly,
        "features_by_gene": features_by_gene,
        "gene_metadata": gene_metadata,
        "priority_rules": seed_local_priority_rules(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def create_sqlite(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.executescript(CANON_V2_SQL)
    return connection


def insert_many(connection: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    connection.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def process_gene_module_v2(input_path: Path, output_dir: Path, source_file_name: str, assembly: str) -> dict:
    started_at = utc_now()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_root = output_dir / "cache"
    raw_rows, load_meta = load_table(input_path)
    rows, meta = normalize_module_rows(raw_rows)
    canon_version_id = f"{safe_slug(Path(source_file_name).stem)}_{safe_slug(assembly)}_ensembl"
    warnings: list[dict] = []

    sqlite_path = output_dir / "heal_canon_v2.sqlite"
    connection = create_sqlite(sqlite_path)

    canon_version_row = {
        "canon_version_id": canon_version_id,
        "canon_name": Path(source_file_name).stem,
        "canon_schema_version": GENE_MODULE_SCHEMA_VERSION,
        "source_filename": source_file_name,
        "assembly": assembly,
        "annotation_source": "HGNC + Ensembl REST",
        "annotation_release": "live",
        "transcript_policy": TRANSCRIPT_POLICY,
        "created_at": utc_now(),
        "notes": "Generated by HEAL gene-module canon adapter.",
    }
    connection.execute(
        """
        INSERT INTO canon_versions (
            canon_version_id, canon_name, canon_schema_version, source_filename,
            assembly, annotation_source, annotation_release, transcript_policy,
            created_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(canon_version_row.values()),
    )

    canon_rows_for_db = [{**row, "canon_version_id": canon_version_id} for row in rows]
    insert_many(connection, "canon_rows", canon_rows_for_db)

    gene_rows = [row for row in rows if row["gene_symbol_normalized"]]
    non_gene_rows = [row for row in rows if not row["gene_symbol_normalized"]]
    for row in non_gene_rows:
        add_warning(
            warnings,
            canon_version_id,
            "non_gene_module",
            "info",
            f"Row {row['canon_row_id']} has no gene symbol and is kept as a non-gene module.",
            "No VCF matching will be performed for this row.",
            canon_row_id=row["canon_row_id"],
        )

    unique_symbols = sorted({row["gene_symbol_normalized"] for row in gene_rows})
    genes_for_db = []
    envelopes_for_db = []
    transcript_rows_all = []
    feature_rows_all = []
    merged_rows_all = []
    gene_module_rows = []
    gene_master_rows = []
    genes_resolved = 0
    genes_ambiguous = 0
    genes_unresolved = 0
    annotation_release = "live"

    for symbol in unique_symbols:
        related_rows = candidate_rows_for_symbol(gene_rows, symbol)
        full_gene_name = clean_cell(related_rows[0]["full_gene_name"]) if related_rows else ""
        resolved, resolution_status, resolution_notes = resolve_gene_identity(symbol, full_gene_name, assembly, cache_root)
        if not resolved:
            genes_unresolved += 1
            severity = "warning" if resolution_status == "unresolved_symbol" else "warning"
            warning_type = "multiple_gene_candidates" if resolution_status == "resolved_multiple_candidates" else "unresolved_symbol"
            if resolution_status == "resolved_multiple_candidates":
                genes_ambiguous += 1
            for row in related_rows:
                add_warning(
                    warnings,
                    canon_version_id,
                    warning_type,
                    severity,
                    resolution_notes,
                    "Review the gene symbol or curate the canon row.",
                    canon_row_id=row["canon_row_id"],
                )
            continue

        genes_resolved += 1
        gene_lookup = resolved["gene_lookup"]
        overlap_rows = ensembl_overlap_transcripts(resolved["ensembl_gene_id"], assembly, cache_root)
        overlap_by_id = {
            clean_cell(row.get("id") or row.get("transcript_id")): row
            for row in overlap_rows
            if clean_cell(row.get("feature_type")) == "transcript"
        }
        hgnc_record = resolved.get("hgnc_record")
        transcript_source_rows = gene_lookup.get("Transcript") or []
        transcript_rows = []
        for transcript in transcript_source_rows:
            transcript_id = clean_cell(transcript.get("id"))
            transcript_rows.append(classify_transcript_record(transcript, overlap_by_id.get(transcript_id, {}), hgnc_record))

        gene_id = f"GENE_{safe_slug(resolved['approved_symbol']).upper()}_{safe_slug(assembly).upper()}"
        gene_row = {
            "gene_id": gene_id,
            "canon_version_id": canon_version_id,
            "gene_symbol_original": resolved["gene_symbol_original"],
            "approved_symbol": resolved["approved_symbol"],
            "ensembl_gene_id": resolved["ensembl_gene_id"],
            "full_gene_name": resolved["full_gene_name"],
            "biotype": resolved["biotype"],
            "resolution_status": resolution_status,
            "resolution_notes": resolution_notes,
            "chrom": resolved["chrom"],
            "start": resolved["start"],
            "end": resolved["end"],
            "strand": resolved["strand"],
            "assembly": resolved["assembly_name"],
        }
        genes_for_db.append(
            {
                "gene_id": gene_id,
                "canon_version_id": canon_version_id,
                "gene_symbol_original": gene_row["gene_symbol_original"],
                "approved_symbol": gene_row["approved_symbol"],
                "ensembl_gene_id": gene_row["ensembl_gene_id"],
                "full_gene_name": gene_row["full_gene_name"],
                "biotype": gene_row["biotype"],
                "resolution_status": gene_row["resolution_status"],
                "resolution_notes": gene_row["resolution_notes"],
            }
        )
        envelopes_for_db.append(
            {
                "gene_id": gene_id,
                "approved_symbol": gene_row["approved_symbol"],
                "chrom": gene_row["chrom"],
                "start": gene_row["start"],
                "end": gene_row["end"],
                "strand": gene_row["strand"],
                "assembly": gene_row["assembly"],
                "coordinate_source": "Ensembl REST",
                "coordinate_release": annotation_release,
                "coordinate_status": "resolved" if gene_row["chrom"] and gene_row["start"] and gene_row["end"] else "missing",
            }
        )

        transcript_rows_for_gene = []
        for transcript_row in transcript_rows:
            transcript_row_db = {
                "gene_id": gene_id,
                "transcript_db_id": transcript_row["transcript_db_id"],
                "ensembl_transcript_id": transcript_row["ensembl_transcript_id"],
                "refseq_transcript_id": transcript_row["refseq_transcript_id"],
                "transcript_name": transcript_row["transcript_name"],
                "chrom": transcript_row["chrom"],
                "start": transcript_row["start"],
                "end": transcript_row["end"],
                "strand": transcript_row["strand"],
                "biotype": transcript_row["biotype"],
                "transcript_support_level": transcript_row["transcript_support_level"],
                "is_mane_select": transcript_row["is_mane_select"],
                "is_mane_plus_clinical": transcript_row["is_mane_plus_clinical"],
                "is_ensembl_canonical": transcript_row["is_ensembl_canonical"],
                "is_primary": transcript_row["is_primary"],
                "inclusion_tier": transcript_row["inclusion_tier"],
                "inclusion_reason": transcript_row["inclusion_reason"],
                "include_for_matching": transcript_row["include_for_matching"],
            }
            transcript_rows_for_gene.append(transcript_row_db)
            transcript_rows_all.append(transcript_row_db)

        feature_rows = []
        for transcript_row in transcript_rows:
            feature_rows.extend(build_feature_rows(gene_id, gene_row["approved_symbol"], transcript_row, annotation_release))
        feature_rows_all.extend(feature_rows)
        merged_rows = merged_feature_rows(gene_id, gene_row["approved_symbol"], transcript_rows_for_gene, feature_rows)
        merged_rows_all.extend(merged_rows)

        for related_index, related_row in enumerate(related_rows, start=1):
            gene_module_rows.append(
                {
                    "gene_module_id": f"{gene_id}:{related_row['canon_row_id']}:{related_index:03d}",
                    "gene_id": gene_id,
                    "canon_row_id": related_row["canon_row_id"],
                    "module_id": related_row["module_id"],
                    "module_name": related_row["module_name"],
                    "system_within_module": related_row["system_within_module"],
                    "tier": related_row["tier"],
                    "evidence_tier": related_row["evidence_tier"],
                    "harm_risk": related_row["harm_risk"],
                    "misinterpretation_risk": related_row["misinterpretation_risk"],
                    "module_status": related_row["module_status"],
                }
            )

        if not any(row["is_mane_select"] for row in transcript_rows_for_gene):
            add_warning(
                warnings,
                canon_version_id,
                "no_mane_select",
                "warning",
                f"{gene_row['approved_symbol']} resolved without MANE Select transcript.",
                "Review if transcript coverage is acceptable for this gene.",
                gene_id=gene_id,
            )
        if not any(row["biotype"] == "protein_coding" for row in transcript_rows_for_gene):
            add_warning(
                warnings,
                canon_version_id,
                "no_protein_coding_transcript",
                "warning",
                f"{gene_row['approved_symbol']} resolved without protein-coding transcripts.",
                "Review whether this gene should participate in this canon.",
                gene_id=gene_id,
            )
        if len(transcript_rows_for_gene) > 20:
            add_warning(
                warnings,
                canon_version_id,
                "many_transcripts",
                "info",
                f"{gene_row['approved_symbol']} has {len(transcript_rows_for_gene)} included transcripts.",
                "Review transcript spread during QA if needed.",
                gene_id=gene_id,
            )
        if gene_row["approved_symbol"] in {"GSTM1", "GSTT1"}:
            add_warning(
                warnings,
                canon_version_id,
                "cnv_sensitive_gene",
                "warning",
                f"{gene_row['approved_symbol']} may be deletion/CNV-sensitive.",
                "Do not infer normality from absence of SNVs in a standard VCF.",
                gene_id=gene_id,
            )

        gene_master_rows.append(create_gene_master_row(gene_row, related_rows, transcript_rows_for_gene, feature_rows, merged_rows))

    priority_rules = seed_local_priority_rules()
    insert_many(connection, "genes", genes_for_db)
    insert_many(connection, "gene_modules", gene_module_rows)
    insert_many(connection, "gene_envelopes", envelopes_for_db)
    insert_many(connection, "transcripts", transcript_rows_all)
    insert_many(connection, "feature_intervals", feature_rows_all)
    insert_many(connection, "merged_feature_intervals", merged_rows_all)
    insert_many(connection, "local_priority_rules", priority_rules)
    insert_many(connection, "preprocessing_warnings", warnings)
    connection.commit()
    connection.close()

    clean_rows_path = output_dir / "heal-canon-v2-clean-rows.csv"
    gene_master_path = output_dir / "heal-canon-v2-gene-master.csv"
    warning_path = output_dir / "heal-canon-v2-preprocessing-warnings.csv"
    report_path = output_dir / "heal-canon-v2-preprocessing-report.json"
    envelope_index_path = output_dir / "canon_gene_envelope_index.json"
    merged_index_path = output_dir / "canon_merged_feature_index.json"

    clean_fields = [
        "canon_row_id",
        "module_id",
        "module_name",
        "tier",
        "life_stage",
        "epistemic_mode",
        "harm_risk",
        "module_status",
        "module_purpose",
        "explicit_exclusions",
        "system_within_module",
        "gene_symbol_original",
        "full_gene_name",
        "evidence_tier",
        "misinterpretation_risk",
        "myth_correction_required",
        "canonical_version",
        "notes",
        "row_status",
        "is_draft",
        "gene_symbol_normalized",
    ]
    write_csv(clean_rows_path, rows, clean_fields)
    write_csv(warning_path, warnings, list(warnings[0].keys()) if warnings else [
        "warning_id",
        "canon_version_id",
        "gene_id",
        "canon_row_id",
        "warning_type",
        "severity",
        "message",
        "suggested_action",
    ])
    write_csv(gene_master_path, gene_master_rows)
    export_gene_envelope_index(canon_version_id, assembly, gene_master_rows, envelope_index_path)
    export_merged_feature_index(canon_version_id, assembly, merged_rows_all, merged_index_path)

    preview_columns = [
        "canon_row_id",
        "module_id",
        "module_name",
        "module_status",
        "system_within_module",
        "gene_symbol_original",
        "evidence_tier",
        "row_status",
        "is_draft",
    ]
    preview = {
        "generatedAt": utc_now(),
        "rows": [{column: row.get(column, "") for column in preview_columns} for row in rows[:200]],
        "columns": preview_columns,
    }
    (output_dir / "canon_preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")

    preprocessing_report = {
        "canon_version_id": canon_version_id,
        "schemaVersion": GENE_MODULE_SCHEMA_VERSION,
        "adapter": GENE_MODULE_ADAPTER,
        "assembly": assembly,
        "annotation_source": "HGNC + Ensembl REST",
        "annotation_release": annotation_release,
        "transcript_policy": TRANSCRIPT_POLICY,
        "rows_total": meta["rows_total"],
        "rows_with_gene": meta["rows_with_gene"],
        "rows_non_gene_module": meta["rows_non_gene_module"],
        "draft_rows": meta["draft_rows"],
        "module_count": meta["module_count"],
        "unique_gene_symbols": meta["unique_gene_symbols"],
        "genes_resolved": genes_resolved,
        "genes_unresolved": genes_unresolved,
        "genes_ambiguous": genes_ambiguous,
        "transcript_rows": len(transcript_rows_all),
        "feature_rows": len(feature_rows_all),
        "merged_feature_rows": len(merged_rows_all),
        "warnings_count": len(warnings),
        "generated_at": utc_now(),
    }
    report_path.write_text(json.dumps(preprocessing_report, ensure_ascii=False, indent=2), encoding="utf-8")

    status = "warning" if warnings else "valid"
    summary = {
        "status": status,
        "errors": [],
        "warnings": [row["message"] for row in warnings],
        "warningsSummary": dict(Counter(row["warning_type"] for row in warnings)),
        "schemaVersion": GENE_MODULE_SCHEMA_VERSION,
        "adapter": GENE_MODULE_ADAPTER,
        "assembly": assembly,
        "activationStatus": "ready_for_activation" if genes_resolved > 0 else "blocked",
        "sourceFileName": source_file_name,
        "inputPath": str(input_path),
        "outputDir": str(output_dir),
        "metadata": {
            **load_meta,
            **meta,
            **preprocessing_report,
            "size_bytes": input_path.stat().st_size,
        },
        "outputs": {
            "cleanRowsCsv": str(clean_rows_path),
            "geneMasterCsv": str(gene_master_path),
            "preprocessingWarningsCsv": str(warning_path),
            "preprocessingReportJson": str(report_path),
            "sqliteDb": str(sqlite_path),
            "geneEnvelopeIndexJson": str(envelope_index_path),
            "mergedFeatureIndexJson": str(merged_index_path),
            "previewJson": str(output_dir / "canon_preview.json"),
        },
        "timestamps": {"startedAt": started_at, "completedAt": utc_now()},
    }
    (output_dir / "canon_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def process(input_path: Path, output_dir: Path, source_file_name: str | None = None, assembly: str = "GRCh38") -> dict:
    source_file_name = source_file_name or input_path.name
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if input_path.stat().st_size <= 0:
        raise ValueError("Input file is empty.")

    raw_rows, _ = load_table(input_path)
    schema_version = detect_schema_from_rows(raw_rows)
    if schema_version == GENE_MODULE_SCHEMA_VERSION:
        return process_gene_module_v2(input_path, output_dir, source_file_name, assembly)
    if schema_version == LEGACY_SCHEMA_VERSION:
        return process_legacy(input_path, output_dir, source_file_name)
    raise ValueError("Unsupported canon schema.")


def detect_schema(input_path: Path) -> dict:
    raw_rows, _ = load_table(input_path)
    return {"schemaVersion": detect_schema_from_rows(raw_rows)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process HEAL canon CSV/XLSX intake.")
    parser.add_argument("--input", help="Input .csv or .xlsx path.")
    parser.add_argument("--output-dir", help="Directory for generated outputs.")
    parser.add_argument("--source-file-name", default="", help="Original file name shown to users.")
    parser.add_argument("--assembly", default="GRCh38", help="Requested genome assembly.")
    parser.add_argument("--detect-schema", action="store_true", help="Only detect canon schema and exit.")
    parser.add_argument("--input-json-base64", default="", help="Base64 JSON payload for wrapper use.")
    args = parser.parse_args()
    if args.input_json_base64:
        payload = json.loads(base64.b64decode(args.input_json_base64).decode("utf-8"))
        args.input = payload.get("inputPath") or payload.get("input")
        args.output_dir = payload.get("outputDir") or payload.get("output_dir")
        args.source_file_name = payload.get("sourceFileName") or payload.get("fileName") or ""
        args.assembly = payload.get("assembly") or args.assembly
        args.detect_schema = bool(payload.get("detectSchema")) or args.detect_schema
    if not args.input:
        parser.error("--input is required.")
    if not args.detect_schema and not args.output_dir:
        parser.error("--output-dir is required unless --detect-schema is used.")
    return args


def main() -> int:
    try:
        args = parse_args()
        if args.detect_schema:
            print(json.dumps(detect_schema(Path(args.input)), ensure_ascii=False))
            return 0
        summary = process(Path(args.input), Path(args.output_dir), args.source_file_name or None, args.assembly)
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
