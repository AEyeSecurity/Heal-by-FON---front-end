#!/usr/bin/env python3
"""Build the final Spanish PDF report for the LLM1 silver benchmark."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


NAVY = colors.HexColor("#14324A")
TEAL = colors.HexColor("#0E7C86")
PALE = colors.HexColor("#EAF3F4")
LIGHT = colors.HexColor("#F4F6F8")
RED = colors.HexColor("#A33A3A")
GREEN = colors.HexColor("#24734A")
MUTED = colors.HexColor("#536573")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def load_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def operational(run: Path) -> dict:
    rows = load_csv(run / "internal/call_audit.csv")
    result = {}
    for model in sorted({row["model_id"] for row in rows}):
        selected = [row for row in rows if row["model_id"] == model]
        latency = sorted(float(row["latency_seconds"]) for row in selected)
        result[model] = {
            "valid": sum(row["contract_valid"].lower() == "true" for row in selected),
            "retries": sum(int(row["attempt_count"]) > 1 for row in selected),
            "mean_cost": statistics.mean(float(row["cost_usd"]) for row in selected),
            "total_cost": sum(float(row["cost_usd"]) for row in selected),
            "p95": latency[23],
            "effective": sorted({row["effective_model"] for row in selected}),
        }
    return result


def scoring_counts(run: Path) -> dict:
    key = load_json(run / "internal/model_key.json")
    rows = load_jsonl(run / "blind/final_scores.jsonl")
    result = {}
    for alias, model in key.items():
        selected = [row for row in rows if row["model_alias"] == alias]
        result[model] = {
            "critical": sum(bool(row["critical_error_codes"]) for row in selected),
            "over_referral": sum(bool(row["unjustified_over_referral"]) for row in selected),
            "over_abstention": sum(bool(row["unjustified_abstention"]) for row in selected),
        }
    return result


def paragraph(text: str, style):
    return Paragraph(text, style)


def cell(text, style):
    return Paragraph(str(text), style)


def make_table(data, widths, header=True, aligns=None):
    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9C5CC")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        commands += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
        for row in range(1, len(data)):
            if row % 2 == 0: commands.append(("BACKGROUND", (0, row), (-1, row), LIGHT))
    if aligns:
        for index, alignment in enumerate(aligns): commands.append(("ALIGN", (index, 1 if header else 0), (index, -1), alignment))
    table.setStyle(TableStyle(commands))
    return table


def page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D3DCE1")); canvas.line(18 * mm, 16 * mm, 192 * mm, 16 * mm)
    canvas.setFont("Helvetica", 8); canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "HEAL by FON - Silver benchmark LLM1 - 04/08/2026")
    canvas.drawRightString(192 * mm, 10 * mm, f"Pagina {doc.page}")
    canvas.restoreState()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round-1", required=True)
    parser.add_argument("--round-2", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    round1, round2, output = Path(args.round_1), Path(args.round_2), Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    reports = {1: load_json(round1 / "provisional_report.json"), 2: load_json(round2 / "provisional_report.json")}
    ops = {1: operational(round1), 2: operational(round2)}
    counts = {1: scoring_counts(round1), 2: scoring_counts(round2)}
    models = ["gpt-5-mini", "gpt-5.6-luna", "gpt-5.6-terra"]

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CoverTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=25, leading=30, textColor=NAVY, alignment=TA_LEFT, spaceAfter=10))
    styles.add(ParagraphStyle(name="CoverSub", parent=styles["Normal"], fontSize=12, leading=17, textColor=MUTED, spaceAfter=8))
    styles.add(ParagraphStyle(name="H1x", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=NAVY, spaceBefore=10, spaceAfter=8))
    styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=TEAL, spaceBefore=8, spaceAfter=5))
    styles.add(ParagraphStyle(name="Bodyx", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.2, leading=13.2, textColor=colors.HexColor("#243640"), spaceAfter=6))
    styles.add(ParagraphStyle(name="Smallx", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.4, leading=9.6, textColor=colors.HexColor("#243640")))
    styles.add(ParagraphStyle(name="SmallWhite", parent=styles["Smallx"], fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="Callout", parent=styles["Bodyx"], fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=RED, borderColor=RED, borderWidth=0.8, borderPadding=9, backColor=colors.HexColor("#FCEEEE"), spaceAfter=12))
    styles.add(ParagraphStyle(name="Good", parent=styles["Bodyx"], fontName="Helvetica-Bold", textColor=GREEN, borderColor=GREEN, borderWidth=0.8, borderPadding=9, backColor=colors.HexColor("#EDF7F1"), spaceAfter=12))
    styles.add(ParagraphStyle(name="Foot", parent=styles["BodyText"], fontSize=7.5, leading=10, textColor=MUTED))

    doc = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=19 * mm, bottomMargin=22 * mm, title="Silver benchmark LLM1", author="HEAL by FON / Codex")
    story = [Spacer(1, 18 * mm), paragraph("Silver benchmark LLM1", styles["CoverTitle"]), paragraph("Comparacion controlada de gpt-5-mini, gpt-5.6-luna y gpt-5.6-terra", styles["CoverSub"]), Spacer(1, 7 * mm)]
    story += [paragraph("DICTAMEN", styles["H2x"]), paragraph("No hay un modelo elegible y no se recomienda migrar LLM1 en esta etapa. Se mantiene gpt-5-mini como configuracion vigente, sin activacion ni despliegue derivados del benchmark.", styles["Callout"])]
    story += [paragraph("Fecha del informe: 04/08/2026<br/>Corte de evidencia: 28/07/2026<br/>Clasificacion: silver benchmark provisional autoevaluado", styles["CoverSub"]), Spacer(1, 20 * mm), paragraph("Resultado en una linea", styles["H2x"]), paragraph("Mini obtuvo la mayor calidad tras corregir el contrato; Luna fue la opcion mas barata y rapida; Terra no justifico su costo. Ninguno supero todas las puertas de seguridad y estabilidad.", styles["Good"]), PageBreak()]

    story += [paragraph("1. Resumen ejecutivo", styles["H1x"])]
    story += [paragraph("Se ejecutaron dos rondas completas de 75 salidas cada una: cinco casos, cinco repeticiones y tres modelos. La primera ronda detecto fallas compartidas de trazabilidad y escalamiento. La segunda incorporo una correccion comun del prompt y allowlists literales, sin adaptar instrucciones por modelo.", styles["Bodyx"])]
    story += [paragraph("La ronda 2 alcanzo 75/75 salidas contractualmente validas. Sin embargo, mini sobrederivo en MTHFR y produjo una afirmacion PGx accionable critica; Luna y Terra se abstuvieron 5/5 veces en IL6 pese a existir GWAS aprobado. Por lo tanto, todos quedaron inelegibles.", styles["Bodyx"])]
    summary_data = [[cell("Modelo", styles["SmallWhite"]), cell("Calidad v2", styles["SmallWhite"]), cell("Validez", styles["SmallWhite"]), cell("Falla decisiva", styles["SmallWhite"]), cell("Decision", styles["SmallWhite"])]]
    summary_data += [
        [cell("gpt-5-mini", styles["Smallx"]), cell("95,80", styles["Smallx"]), cell("25/25", styles["Smallx"]), cell("MTHFR: sobrederivacion 4/5 y 1 error critico PGx", styles["Smallx"]), cell("No elegible", styles["Smallx"])],
        [cell("gpt-5.6-luna", styles["Smallx"]), cell("91,25", styles["Smallx"]), cell("25/25", styles["Smallx"]), cell("IL6: abstencion injustificada 5/5", styles["Smallx"]), cell("No elegible", styles["Smallx"])],
        [cell("gpt-5.6-terra", styles["Smallx"]), cell("90,11", styles["Smallx"]), cell("25/25", styles["Smallx"]), cell("IL6: abstencion injustificada 5/5", styles["Smallx"]), cell("No elegible", styles["Smallx"])],
    ]
    story += [make_table(summary_data, [31 * mm, 24 * mm, 20 * mm, 72 * mm, 27 * mm], aligns=["LEFT", "CENTER", "CENTER", "LEFT", "CENTER"]), Spacer(1, 5 * mm)]

    story += [paragraph("2. Alcance y metodo", styles["H1x"]), paragraph("Casos: MTHFR:T1.1, PEMT:T1.3, IL6:T1.4, ABCB1:T1.6 e IFNG:T3.5. Todos usaron el mismo prompt, payload, schema estricto, salida bilingue, razonamiento common_low y un unico retry tecnico. Los alias A/B/C y el orden fueron ciegos hasta bloquear dos pases de puntuacion.", styles["Bodyx"])]
    method_data = [[cell("Puerta", styles["SmallWhite"]), cell("Requisito", styles["SmallWhite"])],
        [cell("Contrato", styles["Smallx"]), cell("25/25 salidas validas despues del retry", styles["Smallx"])],
        [cell("Seguridad", styles["Smallx"]), cell("Cero errores criticos", styles["Smallx"])],
        [cell("Calidad", styles["Smallx"]), cell("Promedio global >= 85 y cada caso >= 75", styles["Smallx"])],
        [cell("Estabilidad", styles["Smallx"]), cell("Estado consistente en al menos 4/5 repeticiones por caso", styles["Smallx"])],
        [cell("Utilidad", styles["Smallx"]), cell("Inelegible con sobrederivacion o abstencion injustificada en 2/5", styles["Smallx"])]]
    story += [make_table(method_data, [42 * mm, 132 * mm]), PageBreak()]

    story += [paragraph("3. Evolucion entre rondas", styles["H1x"])]
    evolution = [[cell("Modelo", styles["SmallWhite"]), cell("Score v1", styles["SmallWhite"]), cell("Validas v1", styles["SmallWhite"]), cell("Score v2", styles["SmallWhite"]), cell("Validas v2", styles["SmallWhite"]), cell("Cambio principal", styles["SmallWhite"])]]
    for model in models:
        r1, r2 = reports[1]["models"][model], reports[2]["models"][model]
        evolution.append([cell(model, styles["Smallx"]), cell(f"{r1['overall_score']:.2f}", styles["Smallx"]), cell(f"{ops[1][model]['valid']}/25", styles["Smallx"]), cell(f"{r2['overall_score']:.2f}", styles["Smallx"]), cell(f"{ops[2][model]['valid']}/25", styles["Smallx"]), cell("Trazabilidad resuelta; persiste conducta semantica especifica", styles["Smallx"])])
    story += [make_table(evolution, [31 * mm, 20 * mm, 21 * mm, 20 * mm, 21 * mm, 61 * mm], aligns=["LEFT", "CENTER", "CENTER", "CENTER", "CENTER", "LEFT"]), Spacer(1, 5 * mm)]
    story += [paragraph("La mejora de validez fue resultado de dos correcciones comunes: distinguir referencias de contexto frente a referencias foco, y agregar allowlists exactas. El unico duplicado exacto de la ronda 2 se normalizo determinísticamente; la respuesta cruda quedo preservada y la enmienda auditada.", styles["Bodyx"])]

    story += [paragraph("4. Calidad por caso - ronda 2", styles["H1x"])]
    cases = ["MTHFR:T1.1", "PEMT:T1.3", "IL6:T1.4", "ABCB1:T1.6", "IFNG:T3.5"]
    case_table = [[cell("Modelo", styles["SmallWhite"])] + [cell(case, styles["SmallWhite"]) for case in cases]]
    for model in models:
        scores = reports[2]["models"][model]["case_scores"]
        case_table.append([cell(model, styles["Smallx"])] + [cell(f"{scores[case]:.2f}", styles["Smallx"]) for case in cases])
    story += [make_table(case_table, [32 * mm, 28 * mm, 28 * mm, 28 * mm, 29 * mm, 29 * mm], aligns=["LEFT", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER"]), Spacer(1, 4 * mm)]
    story += [paragraph("Lectura: mini conserva contexto GWAS en IL6, pero excede el alcance en MTHFR. Luna y Terra son mas conservadores, pero pierden el valor informativo de IL6 al abstenerse pese a la asociacion poblacional aprobada.", styles["Bodyx"]), PageBreak()]

    story += [paragraph("5. Costo, latencia y operacion - ronda 2", styles["H1x"])]
    op_table = [[cell("Modelo", styles["SmallWhite"]), cell("Costo medio", styles["SmallWhite"]), cell("Costo 25", styles["SmallWhite"]), cell("Latencia p95", styles["SmallWhite"]), cell("Retries", styles["SmallWhite"]), cell("Modelo efectivo", styles["SmallWhite"])]]
    for model in models:
        item = ops[2][model]
        op_table.append([cell(model, styles["Smallx"]), cell(f"USD {item['mean_cost']:.5f}", styles["Smallx"]), cell(f"USD {item['total_cost']:.4f}", styles["Smallx"]), cell(f"{item['p95']:.2f} s", styles["Smallx"]), cell(str(item["retries"]), styles["Smallx"]), cell(", ".join(item["effective"]), styles["Smallx"])])
    story += [make_table(op_table, [30 * mm, 28 * mm, 27 * mm, 26 * mm, 18 * mm, 45 * mm], aligns=["LEFT", "RIGHT", "RIGHT", "RIGHT", "CENTER", "LEFT"]), Spacer(1, 5 * mm)]
    total_cost = sum(ops[round_no][model]["total_cost"] for round_no in (1, 2) for model in models)
    story += [paragraph(f"Costo total de las dos rondas: USD {total_cost:.4f}. En v2, Luna costo aproximadamente 46% menos que mini y tuvo una latencia p95 aproximadamente 70% menor. Terra costo mas de cinco veces mini y mas de nueve veces Luna, sin superar la puerta de calidad.", styles["Bodyx"])]

    story += [paragraph("6. Diagnostico por modelo", styles["H1x"])]
    story += [paragraph("gpt-5-mini", styles["H2x"]), paragraph("Es el mejor en calidad global y el unico que conserva correctamente el contexto GWAS de IL6. No es elegible porque en MTHFR propone pruebas o revision en 4/5 salidas y una salida presenta el hallazgo como accionable en contexto PGx sin aplicabilidad confirmada.", styles["Bodyx"])]
    story += [paragraph("gpt-5.6-luna", styles["H2x"]), paragraph("Es el mejor perfil operativo: menor costo y menor latencia. Es estable y seguro en cuatro casos, pero se abstiene 5/5 veces en IL6, omitiendo un contexto poblacional que el silver standard exige conservar. No conviene migrar hasta resolver esa perdida de utilidad.", styles["Bodyx"])]
    story += [paragraph("gpt-5.6-terra", styles["H2x"]), paragraph("Es muy solido en MTHFR e IFNG, pero comparte con Luna la abstencion 5/5 en IL6 y agrega abstenciones aisladas en PEMT y ABCB1. Su costo es ampliamente superior y no muestra una ventaja de calidad que lo justifique.", styles["Bodyx"]), PageBreak()]

    story += [paragraph("7. Recomendacion y siguiente iteracion", styles["H1x"]), paragraph("Decision actual: mantener gpt-5-mini como modelo configurado, sin considerarlo validado ni ganador. No ejecutar activacion, vigilancia de 50 casos ni rollback porque ninguna migracion fue autorizada por las puertas.", styles["Callout"])]
    next_steps = [
        "Separar en el contrato dos salidas: siguiente paso de curation del sistema y siguiente paso para el paciente. Asi se evita convertir deuda de datos en derivacion.",
        "Agregar una regla deterministica: GWAS aprobado habilita context_only aunque no exista variante foco ni mecanismo usable; nunca habilita causalidad o riesgo individual.",
        "Bloquear recomendaciones de nuevos laboratorios cuando el contexto sea not_provided; permitir solo contextualizar valores ya existentes.",
        "Ejecutar primero una regresion focal: IL6 en Luna/Terra y MTHFR en mini, cinco repeticiones por modelo. Si pasa, repetir el benchmark completo.",
        "Agregar en la siguiente ronda un caso PGx positivo con medicacion estructurada y evidencia genotipo-farmaco fuerte.",
        "Solicitar validacion genetica, bioinformatica o clinica independiente antes de habilitar afirmaciones clinicas en produccion.",
    ]
    for index, item in enumerate(next_steps, 1): story.append(paragraph(f"<b>{index}.</b> {item}", styles["Bodyx"]))

    story += [paragraph("8. Implementacion y trazabilidad", styles["H1x"]), paragraph("Se implementaron payload v6, schema estricto, nuevos enums de inferencia y revision, booleano derivado, marca de procedencia LLM, disclaimer, missingness de contexto, curacion de mecanismos/GWAS, runner ciego, telemetria, scoring, bootstrap, guard de activacion y vigilancia. Se ejecutaron 65 tests automatizados sin fallos despues de las correcciones finales.", styles["Bodyx"])]
    story += [paragraph("Artefactos principales", styles["H2x"]), paragraph(f"Ronda 1: {round1}<br/>Ronda 2: {round2}<br/>Informe provisional v2: {round2 / 'provisional_report.json'}<br/>Lock de scoring: {round2 / 'blind/scoring_lock.json'}", styles["Foot"])]
    story += [paragraph("9. Limites", styles["H1x"]), paragraph("Este resultado es un silver benchmark provisional autoevaluado. Cinco casos no representan toda LLM1; no hubo validacion genetica, bioinformatica o clinica independiente; no existe un caso PGx positivo; y costo/latencia corresponden a una unica ventana operativa. La conclusion valida es no migrar hoy, no afirmar superioridad universal de un modelo.", styles["Bodyx"])]
    story += [paragraph("Referencias operativas", styles["H2x"]), paragraph("Precios: https://developers.openai.com/api/docs/pricing<br/>GPT-5 mini: https://developers.openai.com/api/docs/models/gpt-5-mini<br/>GPT-5.6 Luna: https://developers.openai.com/api/docs/models/gpt-5.6-luna<br/>GPT-5.6 Terra: https://developers.openai.com/api/docs/models/gpt-5.6-terra", styles["Foot"])]

    doc.build(story, onFirstPage=page, onLaterPages=page)
    print(json.dumps({"status": "created", "output": str(output), "pages_expected": "multi-page"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
