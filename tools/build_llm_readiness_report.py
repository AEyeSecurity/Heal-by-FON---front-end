#!/usr/bin/env python3
"""Create the executive DOCX/PDF deliverables for the HEAL v2 readiness audit."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


INK = "15324B"
BLUE = "2E74B5"
LIGHT = "E8EEF5"
PALE_GREEN = "E7F3EC"
PALE_RED = "FCE8E6"
MUTED = "5F6B76"


def load_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{margin}"))
        if node is None:
            node = OxmlElement(f"w:{margin}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_font(run, size=11, bold=False, color="000000", name="Calibri"):
    run.font.name = name
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), name)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), name)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)


def add_text(doc: Document, text: str, bold=False, color="000000", after=6, align=None):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = 1.1
    if align is not None:
        paragraph.alignment = align
    set_font(paragraph.add_run(text), bold=bold, color=color)
    return paragraph


def add_bullet(doc: Document, text: str):
    paragraph = doc.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.space_after = Pt(5)
    paragraph.paragraph_format.line_spacing = 1.1
    set_font(paragraph.add_run(text))
    return paragraph


def add_heading(doc: Document, text: str, level=1):
    paragraph = doc.add_paragraph(style=f"Heading {level}")
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(16 if level == 1 else 10)
    paragraph.paragraph_format.space_after = Pt(8 if level == 1 else 5)
    run = paragraph.add_run(text)
    set_font(run, size={1: 16, 2: 13, 3: 12}.get(level, 11), bold=True, color=BLUE if level < 3 else INK)
    return paragraph


def add_table(doc: Document, headers: list[str], rows: list[list[str]], widths: list[float] | None = None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table.style = "Table Grid"
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = ""
        set_cell_shading(cell, LIGHT)
        set_cell_margins(cell)
        set_font(cell.paragraphs[0].add_run(header), size=9.5, bold=True, color=INK)
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = ""
            cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cells[index])
            set_font(cells[index].paragraphs[0].add_run(str(value)), size=9.2)
    if widths:
        for row in table.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = Inches(width)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def configure_docx(doc: Document):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.82)
    section.bottom_margin = Inches(0.78)
    section.left_margin = Inches(0.85)
    section.right_margin = Inches(0.85)
    section.header_distance = Inches(0.35)
    section.footer_distance = Inches(0.35)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.1
    for style_name, size, color in (("Heading 1", 16, BLUE), ("Heading 2", 13, BLUE), ("Heading 3", 12, INK)):
        style = doc.styles[style_name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.LEFT
    set_font(header.add_run("HEAL by FON | Auditoria de readiness LLM1 v2"), size=8.5, color=MUTED)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(footer.add_run("Run e07a4f94 | Informe tecnico y biologico preliminar"), size=8, color=MUTED)


def status_callout(doc: Document, title: str, body: str, positive=False):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    cell = table.cell(0, 0)
    set_cell_shading(cell, PALE_GREEN if positive else PALE_RED)
    set_cell_margins(cell, top=150, start=180, bottom=150, end=180)
    cell.text = ""
    paragraph = cell.paragraphs[0]
    set_font(paragraph.add_run(f"{title}\n"), size=11, bold=True, color="1E6B45" if positive else "9B1C1C")
    set_font(paragraph.add_run(body), size=10.5, color=INK)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def build_docx(summary: dict, samples: list[dict], groups: list[dict], completeness: list[dict], output: Path):
    doc = Document()
    configure_docx(doc)
    add_text(doc, "AUDITORIA DE READINESS", bold=True, color=BLUE, after=4)
    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(4)
    set_font(title.add_run("HEAL Genomics v2"), size=27, bold=True, color=INK)
    subtitle = doc.add_paragraph()
    subtitle.paragraph_format.space_after = Pt(16)
    set_font(subtitle.add_run("Estado del enrichment y rediseño del input de LLM1"), size=15, color=MUTED)
    add_table(doc, ["Corrida", "Fecha de auditoria", "Decision"], [[summary["run_id"], summary["created_at"][:10], "NO-GO productivo / GO dry-run"]], [2.5, 1.4, 2.6])
    status_callout(
        doc,
        "Decision ejecutiva",
        "La extraccion y el enrichment son suficientemente consistentes para QA y para construir payloads de prueba, pero todavia no deben alimentar una LLM productiva. La mayor limitacion no es el volumen: es la calidad desigual de identidad, transcrito y fuentes externas.",
    )

    add_heading(doc, "1. Que se audito", 1)
    add_text(doc, "Se reviso el 100% de las 5.910 variantes fisicas enriquecidas y de las 7.486 asociaciones variante-gen-modulo. El analisis separa hechos geneticos, evidencia cientifica, mecanismos curados e interpretacion para evitar que una capa contamine a la siguiente.")
    counts = summary["counts"]
    add_table(doc, ["Etapa", "Unidad", "Cantidad"], [
        ["Normalizacion", "Variantes fisicas candidatas", f"{counts['normalized_variants']:,}"],
        ["Match", "Variante-gen", f"{counts['variant_gene_matches']:,}"],
        ["Match expandido", "Variante-gen-modulo", f"{counts['matched_module_rows']:,}"],
        ["Triage", "Asociaciones elegibles", f"{counts['triage_rows']:,}"],
        ["Enrichment", "Variantes fisicas unicas", f"{counts['physical_variants']:,}"],
        ["Agrupamiento", "Gen-modulo", f"{counts['gene_module_groups']:,}"],
    ], [1.35, 3.55, 1.1])

    add_heading(doc, "2. Como encajan las etapas", 1)
    for text in (
        "Normalizacion: separa alelos observados, valida GRCh38 y crea una identidad fisica estable.",
        "Match: cruza cada variante con envelopes y features del canon y la asigna a genes.",
        "Preparation y triage: excluyen background y conservan clases potencialmente relevantes sin interpretar.",
        "Enrichment fisico: consulta VEP y fuentes secundarias una sola vez por variante fisica.",
        "Proyeccion gen-modulo: reutiliza la evidencia fisica en cada contexto del canon sin volver a consultar APIs.",
        "Payload LLM1 v3: resume por gen-modulo y mantiene separados hechos, evidencia, mecanismos y limitaciones.",
    ):
        add_bullet(doc, text)

    add_heading(doc, "3. Hallazgos principales", 1)
    findings = summary["findings"]
    add_table(doc, ["Hallazgo", "Cantidad", "Por que importa"], [
        ["UTR local que VEP ve como intronica", f"{findings['utr_intron_discordant_rows']:,}", "No puede rankearse como UTR sin resolver el transcrito."],
        ["SpliceAI informado pero <0,10", f"{findings['spliceai_zero_signal']:,}", "Campo poblado no equivale a señal de splice."],
        ["Errores VEP", f"{findings['vep_errors']:,}", "La consecuencia funcional queda bloqueada."],
        ["Identidad no resuelta", f"{findings['identity_unresolved']:,}", "La evidencia secundaria no puede atribuirse con seguridad."],
        ["Error en alguna fuente secundaria", f"{findings['source_error_variants']:,}", "Error operativo no equivale a ausencia de evidencia."],
    ], [2.15, 0.85, 3.0])
    comparison = summary.get("previous_run_comparison", {})
    add_heading(doc, "Comparacion con la corrida anterior", 2)
    add_text(doc, "Las cardinalidades se mantuvieron estables. Ensembl Variation sumo 78 respuestas exitosas y MyVariant 2; ClinVar sumo 84 exitos, pero tambien 180 errores adicionales. La remediacion por coordenadas consulto las 1.645 identidades pendientes sin resolver ninguna identidad exacta nueva, por lo que su costo no se justifica en el analisis superficial.")

    add_heading(doc, "4. Que informacion existe", 1)
    add_text(doc, "El dataset no es un bloque homogeneo. Cada variante puede tener coordenada y alelos confirmados, consecuencia VEP, scores funcionales, frecuencia poblacional, ClinVar, GWAS o PharmGKB. La utilidad depende de la combinacion y de que la identidad este confirmada.")
    add_table(doc, ["Eje", "Subgrupo", "Cantidad"],
              [["Identidad", key, f"{value:,}"] for key, value in summary["identity_counts"].items()] +
              [["Readiness", key, f"{value:,}"] for key, value in summary["readiness_counts"].items()],
              [1.2, 3.6, 1.2])

    add_heading(doc, "5. Lectura biologica preliminar", 1)
    add_text(doc, "La corrida permite reconocer tipos de señal que un bioinformatico puede priorizar, pero no permite concluir diagnosticos ni recomendaciones. La evidencia mas interpretable aparece cuando coinciden identidad alelica, consecuencia transcript-aware y una fuente independiente.")
    for text in (
        "Variantes codificantes LoF, frameshift, stop gained o splice canonico: revisar transcrito MANE/canonical, calidad, frecuencia y ClinVar antes de considerarlas señales fuertes.",
        "Missense: HGVS proteinico, CADD, REVEL y AlphaMissense son apoyo tecnico; no sustituyen evidencia clinica ni penetrancia.",
        "UTR y no codificantes: requieren concordancia de transcrito y evidencia regulatoria; densidad por gen no implica efecto.",
        "GWAS: describe asociaciones poblacionales y puede aportar contexto de rasgos, nunca causalidad individual.",
        "PharmGKB: puede señalar relaciones gen-farmaco, pero debe conservar nivel, alelo y aplicabilidad; no habilita una recomendacion terapeutica.",
        "ClinVar: aporta clasificacion y review status solo cuando assembly, posicion y alelo coinciden; VUS y conflictos deben permanecer explicitamente inciertos.",
    ):
        add_bullet(doc, text)

    add_heading(doc, "6. Los 50 ejemplos", 1)
    add_text(doc, "La seleccion es deterministica y cubre ocho estratos. El workbook adjunto contiene las 50 variantes fisicas y sus 61 proyecciones gen-modulo. A continuacion se muestran ocho casos representativos para orientar la lectura.")
    sample_rows = []
    for row in samples[:8]:
        coord = f"{row['chrom_vcf']}:{row['pos_vcf']} {row['ref_vcf']}>{row['alt_vcf']}"
        evidence = row.get("vep_most_severe_consequence") or "sin consecuencia VEP"
        if row.get("clinvar_normalized_classification") not in ("", "not_reported"):
            evidence += f"; ClinVar {row['clinvar_normalized_classification']}"
        sample_rows.append([row["sample_category"], row.get("resolved_rsid") or coord, row["gene_symbols"], evidence])
    add_table(doc, ["Estrato", "Variante", "Gen", "Evidencia visible"], sample_rows, [1.45, 1.45, 0.8, 2.3])

    add_heading(doc, "7. Payload LLM1 v3", 1)
    add_text(doc, "El payload anterior mezclaba conteos y campos parciales. El nuevo contrato mantiene secciones auditables y limita el prompt a un maximo de 20 variantes foco. El resto se resume sin convertir densidad en efecto.")
    for text in (
        "group_context: gen, modulo, tier, proposito, exclusiones y versiones.",
        "canonical_status: observed_alt, not_observed, callability desconocida y CNV/VNTR no evaluados.",
        "genetic_facts y scientific_evidence: solo variantes foco con identidad, transcrito y estados de fuente.",
        "curated_mechanisms: vacio hasta que el registro sea aprobado profesionalmente.",
        "context_variants: conteos comprimidos, sin narrativa biologica automatica.",
        "unresolved_and_failed: identidad, VEP, discordancias y errores que obligan a abstencion.",
        "provenance: hashes, version del pipeline, canon, referencia y timestamps.",
    ):
        add_bullet(doc, text)
    status_callout(doc, "Control activo", "Se generaron 180 payloads dry-run y cero llamadas LLM. Ningun grupo supera hoy todos los gates porque el registro de mecanismos todavia requiere curacion profesional.", positive=True)

    add_heading(doc, "8. Trabajo del bioinformatico", 1)
    add_text(doc, "La revision profesional debe concentrarse donde el control automatico no puede resolver contexto biologico o representacion. No es necesario leer manualmente las 5.910 filas desde cero.")
    checks = [
        "Revisar las 50 variantes de la hoja Sample 50 Physical y completar Bioinfo Review.",
        "Confirmar todos los casos ClinVar no benignos, VUS y conflictivos, incluyendo allele y review status.",
        "Revisar identidades ambiguas/no resueltas, indels y diferencias de representacion.",
        "Validar la discordancia UTR/intron contra MANE Select, canonical y transcritos biologicamente relevantes.",
        "Revisar señales SpliceAI >=0,10 y confirmar que scores cero no se traten como positivos.",
        "Evaluar si traits GWAS y resúmenes PharmGKB guardan relacion real con el gen, alelo y modulo.",
        "Curar el registro de mecanismos por gen-modulo con funcion, pathway, directionality, evidencia y fuente.",
        "Registrar aprobado, corregir, excluir o escalar para cada anomalia revisada.",
    ]
    for item in checks:
        add_bullet(doc, item)

    add_heading(doc, "9. Gates y pasos siguientes", 1)
    gate_rows = [[name, value["status"], value["reason"]] for name, value in summary["gates"].items()]
    add_table(doc, ["Gate", "Estado", "Motivo"], gate_rows, [1.7, 0.8, 3.7])
    for index, text in enumerate((
        "Aplicar triage transcript-aware y excluir del ranking UTR/intron no resuelto.",
        "Reintentar o cerrar deuda de fuentes sin convertir errores en not_found.",
        "Completar el registro de mecanismos y aprobarlo con el bioinformatico.",
        "Regenerar los 180 payloads v3 y verificar hashes, tamaños y abstenciones.",
        "Ejecutar un piloto pequeno y ciego de LLM1 solo despues de aprobar los cuatro gates.",
    ), 1):
        add_text(doc, f"{index}. {text}", after=4)

    add_heading(doc, "10. Conclusion", 1)
    add_text(doc, "El sistema ya produce una base tecnica valiosa y reproducible, pero el enrichment no debe describirse como uniformemente completo. La siguiente etapa correcta no es activar la IA, sino cerrar identidad/transcritos, curar mecanismos y validar la muestra con el bioinformatico. Con esas condiciones, el agrupamiento gen-modulo puede reducir 7.486 filas a 180 unidades interpretables sin perder trazabilidad.")
    add_heading(doc, "Fuentes y trazabilidad", 2)
    for source in (
        "HEAL Business Brief v.1 - requerimientos de producto y guardrails.",
        "HEAL Genetics Product Specification - arquitectura canon-first y separacion de capas.",
        "Master Prompt Karen - principios de voz, incertidumbre y limites; no se reutiliza su dominio de perimenopausia.",
        f"Run {summary['run_id']} - artifacts de normalizacion, match, triage y enrichment con hashes registrados.",
    ):
        add_bullet(doc, source)
    doc.save(output)


def pdf_styles():
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("TitleHEAL", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=25, leading=29, textColor=colors.HexColor(f"#{INK}"), spaceAfter=8),
        "subtitle": ParagraphStyle("SubtitleHEAL", parent=styles["Normal"], fontName="Helvetica", fontSize=13, leading=17, textColor=colors.HexColor(f"#{MUTED}"), spaceAfter=18),
        "h1": ParagraphStyle("H1HEAL", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=colors.HexColor(f"#{BLUE}"), spaceBefore=12, spaceAfter=7),
        "h2": ParagraphStyle("H2HEAL", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor(f"#{INK}"), spaceBefore=9, spaceAfter=5),
        "body": ParagraphStyle("BodyHEAL", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.6, leading=13, textColor=colors.HexColor(f"#{INK}"), spaceAfter=6),
        "bullet": ParagraphStyle("BulletHEAL", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.3, leading=12.5, leftIndent=14, firstLineIndent=-8, bulletIndent=4, spaceAfter=4),
        "small": ParagraphStyle("SmallHEAL", parent=styles["BodyText"], fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor(f"#{MUTED}")),
        "callout": ParagraphStyle("CalloutHEAL", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=10, leading=14, textColor=colors.HexColor(f"#{INK}")),
    }


def pdf_table(data, widths, header=True):
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5DF")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(f"#{LIGHT}")),
        ("FONTSIZE", (0, 0), (-1, -1), 7.8),
        ("LEADING", (0, 0), (-1, -1), 9.5),
    ]
    table.setStyle(TableStyle(commands))
    return table


def build_pdf(summary: dict, samples: list[dict], output: Path):
    styles = pdf_styles()
    doc = SimpleDocTemplate(str(output), pagesize=letter, rightMargin=0.65 * inch, leftMargin=0.65 * inch, topMargin=0.6 * inch, bottomMargin=0.58 * inch, title="HEAL Genomics v2 - Auditoria de readiness")
    story = [Paragraph("HEAL Genomics v2", styles["title"]), Paragraph("Auditoria de readiness y rediseño del input de LLM1", styles["subtitle"])]
    counts = summary["counts"]
    story.append(pdf_table([
        ["Run", "Fecha", "Decision"],
        [summary["run_id"], summary["created_at"][:10], "NO-GO productivo / GO dry-run"],
    ], [2.5 * inch, 1.0 * inch, 2.7 * inch]))
    story += [Spacer(1, 10), Table([[Paragraph("DECISION EJECUTIVA<br/><font name='Helvetica'>La extraccion permite QA y payloads de prueba, pero identidad, transcriptos, mecanismos y deuda de fuentes todavia bloquean LLM1 productiva.</font>", styles["callout"])]], colWidths=[6.2 * inch], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(f"#{PALE_RED}")), ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D77A72")), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)]))]
    story += [Paragraph("1. Alcance y arquitectura", styles["h1"]), Paragraph("Se audito el 100% de las 5.910 variantes fisicas y las 7.486 asociaciones variante-gen-modulo, manteniendo separadas las capas de hechos, evidencia, mecanismos e interpretacion.", styles["body"])]
    story.append(pdf_table([["Etapa", "Unidad", "Cantidad"], ["Normalizacion", "Fisicas candidatas", f"{counts['normalized_variants']:,}"], ["Match", "Variante-gen", f"{counts['variant_gene_matches']:,}"], ["Match expandido", "Variante-gen-modulo", f"{counts['matched_module_rows']:,}"], ["Triage", "Elegibles", f"{counts['triage_rows']:,}"], ["Enrichment", "Fisicas unicas", f"{counts['physical_variants']:,}"], ["Agrupamiento", "Gen-modulo", f"{counts['gene_module_groups']:,}"]], [1.4 * inch, 3.5 * inch, 1.1 * inch]))
    story += [Paragraph("2. Hallazgos", styles["h1"])]
    findings = summary["findings"]
    for text in (
        f"{findings['utr_intron_discordant_rows']:,} filas UTR locales son intronicas para el transcripto VEP objetivo.",
        f"{findings['spliceai_zero_signal']:,} variantes tienen SpliceAI poblado pero score maximo <0,10.",
        f"{findings['vep_errors']:,} variantes tienen error VEP y {findings['identity_unresolved']:,} no poseen identidad exacta confirmada.",
        f"{findings['source_error_variants']:,} variantes tienen al menos un error de fuente secundaria.",
        "La ausencia en un VCF sparse no demuestra homocigosis de referencia ni callability; CNV y VNTR siguen no evaluados.",
        "Frente al baseline, las cardinalidades son iguales: hubo +78 exitos Ensembl y +84 ClinVar, pero la remediacion por coordenadas resolvio 0 identidades exactas y agrego deuda de errores ClinVar.",
    ):
        story.append(Paragraph(f"- {text}", styles["bullet"]))
    story += [Paragraph("3. Lectura biologica prudente", styles["h1"]), Paragraph("La informacion permite priorizar revision, no diagnosticar. Las señales mas utiles combinan identidad alelica, transcripto relevante y evidencia independiente.", styles["body"])]
    for text in (
        "LoF, frameshift, stop gained y splice canonico requieren validacion de transcripto, frecuencia y evidencia clinica.",
        "Missense puede apoyarse en HGVS proteinico, CADD, REVEL y AlphaMissense, sin convertir scores en causalidad.",
        "UTR/no codificante requiere concordancia transcript-aware; densidad de variantes no es efecto.",
        "GWAS es asociacion poblacional. PharmGKB es contexto gen-farmaco. ClinVar exige coincidencia de alelo y review status.",
    ):
        story.append(Paragraph(f"- {text}", styles["bullet"]))
    story += [PageBreak(), Paragraph("4. Readiness", styles["h1"])]
    story.append(pdf_table([["Gate", "Estado", "Motivo"]] + [[name, value["status"], Paragraph(value["reason"], styles["small"])] for name, value in summary["gates"].items()], [1.65 * inch, 0.65 * inch, 3.8 * inch]))
    story += [Paragraph("5. Muestra representativa", styles["h1"]), Paragraph("El workbook contiene las 50 variantes fisicas y sus 61 proyecciones gen-modulo. Estos cuatro casos ilustran el rango de informacion disponible.", styles["body"])]
    story.append(Paragraph("La muestra cubre coding/protein, splice, ClinVar no benigno, PharmGKB, GWAS, discordancia UTR/intron, identidad no resuelta y errores de fuente. Cada caso incluye informacion disponible, limitaciones y campos de aprobacion profesional.", styles["body"]))
    story += [Paragraph("6. Payload LLM1 v3", styles["h1"]), Paragraph("El nuevo payload limita a 20 variantes foco, resume contexto, aisla errores y no expone mecanismos sin curacion profesional. Se generaron 180 payloads dry-run y cero llamadas LLM.", styles["body"])]
    for text in ("Contexto de grupo y estado canonico completo.", "Hechos y evidencia solo para variantes foco.", "UTR/intron discordante e identidad no resuelta en limitaciones.", "SpliceAI usa maximo DS_*; score cero no suma prioridad.", "GWAS y PharmGKB no suman puntos por mera presencia.", "Provenance y hashes permiten reproducibilidad."):
        story.append(Paragraph(f"- {text}", styles["bullet"]))
    story += [Paragraph("7. Checklist profesional", styles["h1"])]
    for text in ("Revisar las 50 variantes y completar la plantilla Bioinfo Review.", "Validar ClinVar no benigno, VUS/conflictos, indels e identidades ambiguas.", "Resolver UTR/intron contra MANE/canonical y transcritos relevantes.", "Revisar SpliceAI >=0,10 y separar source_error de not_found.", "Curar funcion, pathway, directionality, evidencia y fuentes por gen-modulo.", "Aprobar o rechazar el piloto solamente despues de que pasen los cuatro gates."):
        story.append(Paragraph(f"- {text}", styles["bullet"]))
    story += [Paragraph("8. Conclusion", styles["h1"]), Paragraph("La plataforma esta lista para QA deterministico y diseño de payloads, no para interpretacion productiva. Cerrar identidad/transcriptos y curar mecanismos es el camino mas corto para reducir 7.486 filas a 180 grupos auditables sin introducir conclusiones biologicas no sustentadas.", styles["body"])]

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor(f"#{MUTED}"))
        canvas.drawString(0.65 * inch, 0.34 * inch, "HEAL by FON | Auditoria de readiness LLM1 v2")
        canvas.drawRightString(7.85 * inch, 0.34 * inch, f"Pagina {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-dir", required=True)
    args = parser.parse_args()
    root = Path(args.analysis_dir)
    summary = json.loads((root / "llm_readiness_summary.json").read_text(encoding="utf-8"))
    samples = load_csv(root / "llm_readiness_sample_50_physical.csv")
    groups = load_csv(root / "llm_readiness_group_summary.csv")
    completeness = load_csv(root / "llm_readiness_field_completeness.csv")
    build_docx(summary, samples, groups, completeness, root / "v2_llm_readiness_report.docx")
    build_pdf(summary, samples, root / "v2_llm_readiness_report.pdf")
    summary.setdefault("outputs", {}).update({
        "readinessReportDocx": str(root / "v2_llm_readiness_report.docx"),
        "readinessReportPdf": str(root / "v2_llm_readiness_report.pdf"),
    })
    (root / "llm_readiness_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"docx": str(root / "v2_llm_readiness_report.docx"), "pdf": str(root / "v2_llm_readiness_report.pdf")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
