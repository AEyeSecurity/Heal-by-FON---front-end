import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [foundationPath, oldFoundationPath, manifestPath, outputPath, previewDir] = process.argv.slice(2);
if (![foundationPath, oldFoundationPath, manifestPath, outputPath, previewDir].every(Boolean)) {
  throw new Error("Usage: node build_tier1_reconciliation_workbook.mjs <foundation-v3> <foundation-v1> <evidence-manifest> <output.xlsx> <preview-dir>");
}

const foundation = JSON.parse(await fs.readFile(foundationPath, "utf8"));
const oldFoundation = JSON.parse(await fs.readFile(oldFoundationPath, "utf8"));
const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
const packetByGroup = new Map();
for (const row of manifest.packets) {
  packetByGroup.set(row.group_id, JSON.parse(await fs.readFile(row.packet_path, "utf8")));
}

const groups = Object.values(foundation.groups);
const oldGroups = oldFoundation.groups || {};
const workbook = Workbook.create();
const navy = "#0B2545";
const blue = "#2E74B5";
const lightBlue = "#E8EEF5";
const lightGray = "#F2F4F7";
const paleYellow = "#FFF6E0";
const paleGreen = "#E7F4EC";
const paleRed = "#FCE8E6";
const muted = "#5B6673";

function styleTitle(sheet, range, size = 20) {
  sheet.getRange(range).format = { fill: navy, font: { bold: true, color: "#FFFFFF", size }, wrapText: true, verticalAlignment: "center" };
}

function styleHeader(sheet, range) {
  sheet.getRange(range).format = {
    fill: lightBlue, font: { bold: true, color: navy }, wrapText: true,
    verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: "#AAB7C4" },
  };
}

function styleBody(sheet, range) {
  sheet.getRange(range).format = {
    font: { color: "#111111", size: 10 }, wrapText: true, verticalAlignment: "top",
    borders: { insideHorizontal: { style: "thin", color: "#E0E5EA" } },
  };
}

function setWidths(sheet, widths) {
  for (const [column, width] of Object.entries(widths)) sheet.getRange(`${column}:${column}`).format.columnWidth = width;
}

// Create every worksheet before writing cross-sheet formulas.
const summary = workbook.worksheets.add("RESUMEN");
const signoff = workbook.worksheets.add("FIRMA_12_GRUPOS");
const detailSheet = workbook.worksheets.add("DETALLE_12_GRUPOS");
const validSheet = workbook.worksheets.add("FUENTES_VALIDAS");
const excludedSheet = workbook.worksheets.add("FUENTES_EXCLUIDAS");
const diff = workbook.worksheets.add("DIFERENCIAS_V1_V3");
const glossary = workbook.worksheets.add("GLOSARIO");
summary.showGridLines = false;
summary.getRange("A1:H2").merge();
summary.getRange("A1").values = [["Reconciliación Tier 1 - estado para firma humana"]];
styleTitle(summary, "A1:H2", 20);
summary.getRange("A3:H3").merge();
summary.getRange("A3").values = [["Los 12 paquetes están técnicamente reconciliados. Ningún modelo fue ejecutado y el registro activo no fue modificado."]];
summary.getRange("A3:H3").format = { fill: paleGreen, font: { bold: true, color: navy }, wrapText: true, verticalAlignment: "center" };
summary.getRange("A5:B5").values = [["Indicador", "Valor"]];
styleHeader(summary, "A5:B5");
summary.getRange("A6:A10").values = [["Grupos del gold"], ["Bloqueos técnicos"], ["Firmas pendientes"], ["Paquetes con 20 fuentes"], ["Registro activo modificado"]];
summary.getRange("B6").formulas = [["=COUNTA('FIRMA_12_GRUPOS'!A2:A13)"]];
summary.getRange("B7").formulas = [["=COUNTIF(G13:G24,\"REVISAR\")"]];
summary.getRange("B8").formulas = [["=COUNTIF('FIRMA_12_GRUPOS'!J2:J13,\"PENDIENTE\")"]];
summary.getRange("B9").formulas = [["=COUNTIF('FIRMA_12_GRUPOS'!O2:O13,20)"]];
summary.getRange("B10").values = [["No"]];
styleBody(summary, "A6:B10");
summary.getRange("B7:B10").format.horizontalAlignment = "center";
summary.getRange("D5:H5").merge();
summary.getRange("D5").values = [["Qué tenés que hacer ahora"]];
styleHeader(summary, "D5:H5");
summary.getRange("D6:H10").merge();
summary.getRange("D6").values = [["1) Leer la hoja FIRMA_12_GRUPOS.\n2) Revisar estado, contexto, techo, dirección, límites y fuentes.\n3) Escribir APROBADO o CORREGIR, tu nombre y la fecha.\n4) Si corregís, describir el cambio sin borrar la propuesta original.\n5) Devolver la planilla. Recién entonces se podrá compilar el gold y ejecutar Sol."]];
summary.getRange("D6:H10").format = { fill: paleYellow, font: { color: navy, size: 11 }, wrapText: true, verticalAlignment: "center" };
summary.getRange("A12:H12").values = [["Grupo", "Módulo", "Uso", "Estado propuesto", "Contexto", "Techo", "Control técnico", "Firma"]];
styleHeader(summary, "A12:H12");
summary.getRange("A13:H24").values = groups.map((g) => [
  g.group_id, g.module_id, g.split === "calibration" ? "Calibración" : "Holdout",
  g.scientific_status_proposed, g.context_usable_proposed ? "Sí" : "No", g.inference_ceiling_proposed,
  g.technical_flags.filter((x) => x !== "human_signature_pending").length ? "REVISAR" : "OK", "PENDIENTE",
]);
styleBody(summary, "A13:H24");
summary.getRange("G13:G24").conditionalFormats.add("containsText", { text: "OK", format: { fill: paleGreen, font: { bold: true, color: "#1B5E20" } } });
summary.getRange("H13:H24").conditionalFormats.add("containsText", { text: "PENDIENTE", format: { fill: paleYellow, font: { bold: true, color: "#7A5A00" } } });
setWidths(summary, { A: 18, B: 10, C: 16, D: 24, E: 12, F: 27, G: 18, H: 16 });
summary.getRange("1:3").format.rowHeight = 28;
summary.getRange("6:10").format.rowHeight = 30;
summary.freezePanes.freezeRows(12);

signoff.showGridLines = false;
const signHeaders = ["Grupo", "Gen", "Uso", "Estado propuesto", "Contexto", "Techo", "Dirección", "Fuentes válidas", "Fuentes excluidas", "Aprobación final", "Revisor final", "Fecha firma", "Correcciones", "Control técnico", "Fuentes seleccionadas"];
signoff.getRange("A1:O1").values = [signHeaders];
styleHeader(signoff, "A1:O1");
signoff.getRange("A2:O13").values = groups.map((g) => [
  g.group_id, g.gene, g.split === "calibration" ? "Calibración" : "Holdout", g.scientific_status_proposed,
  g.context_usable_proposed ? "Sí" : "No", g.inference_ceiling_proposed,
  (g.acceptable_directions_proposed || ["supports_relation"]).join(" | "),
  g.allowed_evidence_ids_proposed.length, g.invalid_evidence_ids_proposed.length,
  "PENDIENTE", "", "", "", g.technical_flags.filter((x) => x !== "human_signature_pending").join(" | ") || "OK",
  packetByGroup.get(g.group_id).selection_summary.selected_total,
]);
styleBody(signoff, "A2:O13");
signoff.getRange("D2:G13").format.fill = "#FFFDF3";
signoff.getRange("J2:M13").format.fill = paleYellow;
signoff.getRange("J2:J13").dataValidation = { rule: { type: "list", values: ["PENDIENTE", "APROBADO", "CORREGIR"] } };
signoff.getRange("E2:E13").dataValidation = { rule: { type: "list", values: ["Sí", "No"] } };
signoff.getRange("D2:D13").dataValidation = { rule: { type: "list", values: ["approved", "approved_with_conflict", "withheld", "rejected"] } };
signoff.getRange("F2:F13").dataValidation = { rule: { type: "list", values: ["none", "context_only", "initial_guide_candidate"] } };
signoff.getRange("J2:J13").conditionalFormats.add("containsText", { text: "APROBADO", format: { fill: paleGreen, font: { bold: true, color: "#1B5E20" } } });
signoff.getRange("J2:J13").conditionalFormats.add("containsText", { text: "CORREGIR", format: { fill: paleRed, font: { bold: true, color: "#9B1C1C" } } });
signoff.getRange("L2:L13").format.numberFormat = "yyyy-mm-dd";
setWidths(signoff, { A: 18, B: 12, C: 14, D: 24, E: 12, F: 28, G: 23, H: 15, I: 16, J: 18, K: 20, L: 16, M: 42, N: 18, O: 16 });
signoff.getRange("1:1").format.rowHeight = 40;
signoff.getRange("2:13").format.rowHeight = 48;
signoff.freezePanes.freezeRows(1);
signoff.freezePanes.freezeColumns(3);

detailSheet.showGridLines = false;
detailSheet.getRange("A1:F1").values = [["Grupo", "Dirección y conflictos", "Limitaciones", "Fuentes incluidas", "Fuentes excluidas", "Hash paquete reconciliado"]];
styleHeader(detailSheet, "A1:F1");
detailSheet.getRange("A2:F13").values = groups.map((g) => [
  g.group_id, g.review_notes, g.required_limitations, g.allowed_evidence_ids_proposed.join(" | "),
  g.invalid_evidence_ids_proposed.join(" | ") || "Ninguna", g.reconciled_packet_sha256,
]);
styleBody(detailSheet, "A2:F13");
detailSheet.getRange("B2:E13").format.fill = "#FFFDF3";
setWidths(detailSheet, { A: 18, B: 58, C: 64, D: 62, E: 42, F: 24 });
detailSheet.getRange("2:13").format.rowHeight = 96;
detailSheet.freezePanes.freezeRows(1);
detailSheet.freezePanes.freezeColumns(1);

validSheet.showGridLines = false;
validSheet.getRange("A1:K1").values = [["Grupo", "ID", "Título", "Fecha", "Tipo", "Roles", "Identidad", "Coincidencia automática de términos", "Seleccionada", "DOI", "URL"]];
styleHeader(validSheet, "A1:K1");
const validRows = [];
for (const g of groups) {
  const packet = packetByGroup.get(g.group_id);
  const ledger = new Map(packet.source_ledger.map((s) => [s.evidence_id, s]));
  for (const evidenceId of g.allowed_evidence_ids_proposed) {
    const s = ledger.get(evidenceId);
    validRows.push([g.group_id, evidenceId, s?.title || "Registro no encontrado", s?.publication_date || "", s?.source_kind || "", (s?.role_candidates || []).join(" | "), s?.identity_match || "", s?.module_relevance_candidate ? "Sí" : "No", packet.selected_evidence_ids.includes(evidenceId) ? "Sí" : "No", s?.doi || "", s?.url || ""]);
  }
}
validSheet.getRangeByIndexes(1, 0, validRows.length, 11).values = validRows;
styleBody(validSheet, `A2:K${validRows.length + 1}`);
setWidths(validSheet, { A: 18, B: 20, C: 58, D: 14, E: 22, F: 34, G: 14, H: 18, I: 14, J: 28, K: 48 });
validSheet.freezePanes.freezeRows(1);
validSheet.freezePanes.freezeColumns(2);

excludedSheet.showGridLines = false;
excludedSheet.getRange("A1:H1").values = [["Grupo", "ID", "Título", "Fecha", "Motivo", "En ledger", "Seleccionada", "URL"]];
styleHeader(excludedSheet, "A1:H1");
const excludedRows = [];
for (const g of groups) {
  const packet = packetByGroup.get(g.group_id);
  const ledger = new Map(packet.source_ledger.map((s) => [s.evidence_id, s]));
  for (const evidenceId of g.invalid_evidence_ids_proposed) {
    const s = ledger.get(evidenceId);
    excludedRows.push([g.group_id, evidenceId, s?.title || "Identificador histórico/inválido; no forma parte del ledger reconciliado", s?.publication_date || "", (s?.exclusion_reasons || ["Marcada inválida en la revisión humana"]).join(" | "), s ? "Sí" : "No", packet.selected_evidence_ids.includes(evidenceId) ? "Sí" : "No", s?.url || ""]);
  }
}
excludedSheet.getRangeByIndexes(1, 0, excludedRows.length, 8).values = excludedRows;
styleBody(excludedSheet, `A2:H${excludedRows.length + 1}`);
excludedSheet.getRange(`G2:G${excludedRows.length + 1}`).conditionalFormats.add("containsText", { text: "Sí", format: { fill: paleRed, font: { bold: true, color: "#9B1C1C" } } });
setWidths(excludedSheet, { A: 18, B: 20, C: 62, D: 14, E: 38, F: 14, G: 14, H: 50 });
excludedSheet.freezePanes.freezeRows(1);

diff.showGridLines = false;
diff.getRange("A1:H1").values = [["Grupo", "Bloqueos v1", "Bloqueos técnicos v3", "Estado actual", "Hash anterior", "Hash reconciliado", "PMID agregados", "Fuentes inválidas retiradas"]];
styleHeader(diff, "A1:H1");
diff.getRange("A2:H13").values = groups.map((g) => {
  const old = oldGroups[g.group_id] || {};
  return [g.group_id, (old.technical_flags || []).join(" | "), g.technical_flags.filter((x) => x !== "human_signature_pending").join(" | "), "Sólo firma pendiente", g.original_packet_sha256, g.reconciled_packet_sha256, (g.reconciliation?.added_valid_ids_since_foundation_v1 || []).join(" | ") || "Reconciliadas dentro del mismo allowlist", (old.reconciliation?.invalid_evidence_ids_currently_selected || []).join(" | ") || "Ninguna"];
});
styleBody(diff, "A2:H13");
diff.getRange("D2:D13").format.fill = paleGreen;
setWidths(diff, { A: 18, B: 52, C: 28, D: 22, E: 22, F: 22, G: 42, H: 46 });
diff.getRange("2:13").format.rowHeight = 70;
diff.freezePanes.freezeRows(1);

glossary.showGridLines = false;
glossary.getRange("A1:C1").values = [["Término", "En lenguaje simple", "Qué no significa"]];
styleHeader(glossary, "A1:C1");
const glossaryRows = [
  ["approved", "La relación entre el gen y el módulo tiene respaldo suficiente para usarse como contexto.", "No significa diagnóstico ni efecto individual."],
  ["approved_with_conflict", "La relación es utilizable, pero hay resultados relevantes que difieren.", "No significa que una postura pueda ocultarse."],
  ["context_only", "Se puede explicar el marco biológico, sin dar una conclusión personal.", "No es una abstención total."],
  ["initial_guide_candidate", "Podría habilitar una guía inicial si el genotipo observado también resulta aplicable.", "No habilita tratamiento, dosis ni diagnóstico."],
  ["Holdout", "Caso guardado para comprobar que el proceso funciona fuera de los ejemplos usados para ajustar.", "No es un caso menos importante."],
  ["PMID", "Identificador de una publicación en PubMed.", "No garantiza por sí solo calidad o relevancia."],
  ["Cutoff", "Fecha máxima permitida para incorporar publicaciones: 2026-07-28.", "No es la fecha de recuperación de las bases."],
  ["Hash", "Huella digital que permite detectar si un archivo cambió.", "No evalúa la calidad científica."],
  ["Firma humana", "Aprobación explícita y atribuible de las 12 decisiones.", "No equivale a validación clínica independiente."],
];
glossary.getRangeByIndexes(1, 0, glossaryRows.length, 3).values = glossaryRows;
styleBody(glossary, `A2:C${glossaryRows.length + 1}`);
setWidths(glossary, { A: 26, B: 68, C: 58 });
glossary.getRange(`2:${glossaryRows.length + 1}`).format.rowHeight = 54;
glossary.freezePanes.freezeRows(1);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
for (const sheetName of ["RESUMEN", "FIRMA_12_GRUPOS", "DETALLE_12_GRUPOS", "FUENTES_VALIDAS", "FUENTES_EXCLUIDAS", "DIFERENCIAS_V1_V3", "GLOSARIO"]) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const summaryCheck = await workbook.inspect({ kind: "table", range: "RESUMEN!A1:H24", include: "values,formulas", tableMaxRows: 24, tableMaxCols: 8 });
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" });
console.log(summaryCheck.ndjson);
console.log(errors.ndjson);
console.log(JSON.stringify({ output: path.resolve(outputPath), sheets: 7, valid_sources: validRows.length, excluded_sources: excludedRows.length }));
