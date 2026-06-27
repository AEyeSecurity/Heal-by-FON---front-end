#!/usr/bin/env python3
"""Render a user-facing HEAL final report DOCX from global interpretation JSON.

The script intentionally formats the LLM2 JSON without adding new interpretation.
It uses only Python standard library modules so the production server does not
need extra package installation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import posixpath
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


NS_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REPORT_RENDERER_VERSION = "0.2.0"
REPORT_TEMPLATE_VERSION = "0.1.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_file_stem(name: str) -> str:
    stem = re.sub(r"\.(vcf\.gz|vcf|gz)$", "", name or "heal_report", flags=re.I)
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", stem).strip("._-")
    return stem[:120] or "heal_report"


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def compact_list(values: Any, limit: int = 12) -> str:
    items = [text(item) for item in as_list(values) if text(item)]
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" (+{len(items) - limit})"
    return ", ".join(items)


def para(content: str, style: str | None = None) -> str:
    content = text(content)
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
    if not content:
        return f"<w:p><w:pPr>{style_xml}</w:pPr></w:p>"
    lines = content.splitlines() or [content]
    runs = []
    for index, line in enumerate(lines):
        if index:
            runs.append("<w:r><w:br/></w:r>")
        runs.append(f"<w:r><w:t xml:space=\"preserve\">{escape(line)}</w:t></w:r>")
    return f"<w:p><w:pPr>{style_xml}</w:pPr>{''.join(runs)}</w:p>"


def bullet(content: str) -> str:
    return para(f"- {content}", "ListParagraph")


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def key_value_rows(pairs: list[tuple[str, Any]]) -> str:
    body = [
        '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>'
    ]
    for key, value in pairs:
        body.append(
            "<w:tr>"
            f"<w:tc><w:p><w:r><w:t>{escape(text(key))}</w:t></w:r></w:p></w:tc>"
            f"<w:tc><w:p><w:r><w:t>{escape(text(value) or '-')}</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
        )
    body.append("</w:tbl>")
    return "".join(body)


def add_section(parts: list[str], heading: str, body: Any) -> None:
    if not body:
        return
    parts.append(para(heading, "Heading1"))
    if isinstance(body, str):
        parts.append(para(body))
    elif isinstance(body, list):
        for item in body:
            if isinstance(item, dict):
                title = text(item.get("axis_name") or item.get("gene_or_locus") or item.get("gene") or item.get("title"))
                if title:
                    parts.append(para(title, "Heading2"))
                for key, value in item.items():
                    if key in {"axis_name", "gene_or_locus", "gene", "title"}:
                        continue
                    if isinstance(value, list):
                        value = compact_list(value)
                    parts.append(bullet(f"{human_label(key)}: {text(value)}"))
            else:
                parts.append(bullet(text(item)))
    elif isinstance(body, dict):
        for key, value in body.items():
            if isinstance(value, list):
                value = compact_list(value)
            parts.append(bullet(f"{human_label(key)}: {text(value)}"))
    else:
        parts.append(para(text(body)))


def human_label(key: str) -> str:
    return re.sub(r"_+", " ", str(key)).strip().capitalize()


def document_xml(report: dict[str, Any], metadata: dict[str, Any], payload: dict[str, Any]) -> str:
    global_report = report.get("global_report") or {}
    title = (
        text(global_report.get("report_title"))
        or text(payload.get("reportTitle"))
        or "HEAL by FON - Genomic Interpretation Report"
    )
    generated_at = now_iso()
    language_mode = text(metadata.get("language_mode") or payload.get("languageMode") or "es")
    audience_mode = text(metadata.get("audience_mode") or payload.get("audienceMode") or "all")
    source_file = text(payload.get("fileName") or metadata.get("file_name") or "")

    if language_mode == "en":
        disclaimer = (
            "This report is informational and non-diagnostic. It must not be used as standalone medical advice, "
            "and clinically relevant findings require professional review and technical confirmation."
        )
        metadata_heading = "Report metadata"
    else:
        disclaimer = (
            "Este reporte es informativo y no diagnostico. No debe usarse como consejo medico independiente, "
            "y cualquier hallazgo con uso clinico requiere revision profesional y confirmacion tecnica."
        )
        metadata_heading = "Metadatos del reporte"

    confidence = metadata.get("confidence_distribution") or {}
    parts = [
        para(title, "Title"),
        para("HEAL by FON", "Subtitle"),
        para(disclaimer, "IntenseQuote"),
        para(metadata_heading, "Heading1"),
        key_value_rows(
            [
                ("Generated at", generated_at),
                ("Source file", source_file),
                ("Language mode", language_mode),
                ("Audience mode", audience_mode),
                ("Observed variants", metadata.get("variant_count_observed")),
                ("Unique rsIDs", metadata.get("unique_rsid_count")),
                ("Unique genes", metadata.get("unique_gene_count")),
                ("Confidence distribution", ", ".join(f"{key}: {value}" for key, value in confidence.items())),
            ]
        ),
        page_break(),
    ]

    add_section(parts, "Executive Summary", global_report.get("executive_summary"))
    add_section(parts, "Main Interpretation", global_report.get("main_interpretation"))
    add_section(parts, "Important Caution", global_report.get("important_caution"))
    add_section(parts, "Limitations", global_report.get("limitations"))
    add_section(parts, "Next Review Steps", global_report.get("next_review_steps"))
    add_section(parts, "Biological Axes", report.get("biological_axes"))
    add_section(parts, "Notable Gene Patterns", report.get("notable_gene_patterns"))
    add_section(parts, "Top Findings for Review", report.get("top_findings_for_review"))
    add_section(parts, "Conflicting or Sensitive Findings", report.get("conflicting_or_sensitive_findings"))
    add_section(parts, "Low Confidence Findings Summary", report.get("low_confidence_findings_summary"))
    add_section(parts, "Family Friendly Summary", report.get("family_friendly_summary"))
    add_section(parts, "Technical Summary", report.get("technical_summary"))
    add_section(parts, "Final Recommendation", report.get("final_recommendation"))

    body = "".join(parts)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{NS_WORD}"><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1008" w:right="1008" '
        'w:bottom="1008" w:left="1008" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )


def styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="22"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:rPr><w:i/><w:color w:val="3F7A3A"/><w:sz w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:rPr><w:b/><w:color w:val="143A2B"/><w:sz w:val="28"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:rPr><w:b/><w:color w:val="275D38"/><w:sz w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="360"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="IntenseQuote"><w:name w:val="Intense Quote"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="360" w:right="360"/></w:pPr><w:rPr><w:i/><w:color w:val="555555"/></w:rPr></w:style>
  <w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr></w:style>
</w:styles>"""


def write_docx(output_path: Path, report: dict[str, Any], payload: dict[str, Any]) -> None:
    metadata = report.get("metadata") or {}
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        "word/_rels/document.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        "word/styles.xml": styles_xml(),
        "word/document.xml": document_xml(report, metadata, payload),
        "docProps/core.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(text((report.get('global_report') or {}).get('report_title')) or 'HEAL by FON Final Report')}</dc:title>
  <dc:creator>HEAL by FON pipeline</dc:creator>
  <cp:lastModifiedBy>HEAL by FON pipeline</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now_iso()}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now_iso()}</dcterms:modified>
</cp:coreProperties>""",
        "docProps/app.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>HEAL by FON</Application></Properties>""",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        for name, content in files.items():
            docx.writestr(posixpath.normpath(name), content)


def load_payload() -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json-base64", required=True)
    args = parser.parse_args()
    raw = base64.b64decode(args.input_json_base64).decode("utf-8")
    return json.loads(raw)


def main() -> int:
    started_at = now_iso()
    try:
        payload = load_payload()
        input_path = Path(payload["inputPath"]).resolve()
        output_dir = Path(payload["outputDir"]).resolve()
        report = json.loads(input_path.read_text(encoding="utf-8"))
        file_stem = safe_file_stem(text(payload.get("fileName") or "heal_final_report"))
        output_path = output_dir / f"{file_stem}_final_report.docx"
        write_docx(output_path, report, payload)
        size = output_path.stat().st_size
        if size <= 0:
            raise RuntimeError("DOCX output is empty.")
        summary_path = output_dir / "final_report_summary.json"
        source_hash = sha256_file(input_path)
        docx_hash = sha256_file(output_path)
        upstream_audit = report.get("audit_metadata") or {}
        metadata = {
            "format": "docx",
            "source": "global_interpretation_json",
            "file_name": payload.get("fileName"),
            "language_mode": payload.get("languageMode") or (report.get("metadata") or {}).get("language_mode"),
            "audience_mode": payload.get("audienceMode") or (report.get("metadata") or {}).get("audience_mode"),
            "variant_count_observed": (report.get("metadata") or {}).get("variant_count_observed"),
            "unique_gene_count": (report.get("metadata") or {}).get("unique_gene_count"),
            "unique_rsid_count": (report.get("metadata") or {}).get("unique_rsid_count"),
            "docx_size_bytes": size,
            "report_renderer_version": REPORT_RENDERER_VERSION,
            "report_template_version": REPORT_TEMPLATE_VERSION,
            "input_global_interpretation_hash": source_hash,
            "final_report_docx_hash": docx_hash,
            "upstream_audit_metadata": upstream_audit,
        }
        summary = {
            "status": "valid",
            "errors": [],
            "warnings": [],
            "metadata": metadata,
            "outputs": {
                "finalReportDocx": str(output_path),
                "finalReportSummaryJson": str(summary_path),
            },
            "timestamps": {"startedAt": started_at, "completedAt": now_iso()},
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "errors": [str(exc)],
                    "warnings": [],
                    "metadata": {},
                    "outputs": {},
                    "timestamps": {"startedAt": started_at, "completedAt": now_iso()},
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
