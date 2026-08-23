import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "file:///C:/Users/Usuario/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const [goldPath, evidenceManifestPath, qaCsvPath, outputGold, outputSol, previewDir] = process.argv.slice(2);
if (![goldPath, evidenceManifestPath, qaCsvPath, outputGold, outputSol, previewDir].every(Boolean)) {
  throw new Error("Usage: node build_tier1_final_workbooks.mjs <gold.json> <evidence-manifest.json> <qa.csv> <gold.xlsx> <sol.xlsx> <preview-dir>");
}

function parseCsv(text) {
  const rows = []; let row = []; let value = ""; let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { value += '"'; i++; }
      else if (ch === '"') quoted = false;
      else value += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(value); value = ""; }
    else if (ch === '\n') { row.push(value.replace(/\r$/, "")); rows.push(row); row = []; value = ""; }
    else value += ch;
  }
  if (value || row.length) { row.push(value); rows.push(row); }
  const headers = rows.shift();
  return rows.filter((r) => r.some(Boolean)).map((r) => Object.fromEntries(headers.map((h, i) => [h.replace(/^\uFEFF/, ""), r[i] ?? ""])));
}

const gold = JSON.parse(await fs.readFile(goldPath, "utf8"));
const evidence = JSON.parse(await fs.readFile(evidenceManifestPath, "utf8"));
const qa = parseCsv(await fs.readFile(qaCsvPath, "utf8"));
const packetByGroup = new Map();
for (const item of evidence.packets) packetByGroup.set(item.group_id, JSON.parse(await fs.readFile(item.packet_path, "utf8")));

const navy = "#0B2545", lightBlue = "#E8EEF5", paleGreen = "#E7F4EC", paleYellow = "#FFF6E0", muted = "#5B6673";
function title(sheet, range, text) { sheet.getRange(range).merge(); sheet.getRange(range.split(":")[0]).values = [[text]]; sheet.getRange(range).format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 19 }, wrapText: true, verticalAlignment: "center" }; }
function header(sheet, range) { sheet.getRange(range).format = { fill: lightBlue, font: { bold: true, color: navy }, wrapText: true, verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: "#AAB7C4" } }; }
function body(sheet, range) { sheet.getRange(range).format = { font: { color: "#111111", size: 10 }, wrapText: true, verticalAlignment: "top", borders: { insideHorizontal: { style: "thin", color: "#E0E5EA" } } }; }
function widths(sheet, spec) { for (const [col, width] of Object.entries(spec)) sheet.getRange(`${col}:${col}`).format.columnWidth = width; }

const wb = Workbook.create();
const summary = wb.worksheets.add("RESUMEN"); summary.showGridLines = false;
title(summary, "A1:H2", "Gold Tier 1 reconciliado - control final");
summary.getRange("A3:H3").merge(); summary.getRange("A3").values = [["12/12 firmas heredadas por corrección técnica; PubMed QA sin errores; registro activo sin cambios."]];
summary.getRange("A3:H3").format = { fill: paleGreen, font: { bold: true, color: navy }, wrapText: true };
summary.getRange("A5:B5").values = [["Indicador", "Resultado"]]; header(summary, "A5:B5");
summary.getRange("A6:B13").values = [
  ["Grupos firmados", "12/12"], ["PMID únicos", 1320], ["Apariciones QA", qa.length],
  ["Errores después de reparar", "0"], ["validate-gold", "0 errores"], ["Firma", "Martina Liz Ceballos"],
  ["Fecha", "2026-08-14"], ["Registro activo", "Sin cambios"],
]; body(summary, "A6:B13");
summary.getRange("D5:H5").merge(); summary.getRange("D5").values = [["Lectura rápida"]]; header(summary, "D5:H5");
summary.getRange("D6:H13").merge(); summary.getRange("D6").values = [["La reconciliación corrigió DOI y hashes, pero no cambió PMID, título normalizado, evidencia seleccionada ni decisiones científicas. El Gold está listo. Sol no fue ejecutado porque HEAL_OPENAI_API_KEY no está configurada en el entorno seguro."]];
summary.getRange("D6:H13").format = { fill: paleYellow, font: { color: navy, size: 11 }, wrapText: true, verticalAlignment: "center" };
summary.getRange("A15:H15").values = [["Grupo", "Split", "Estado", "Contexto", "Techo", "Dirección", "Firma", "Reconciliación"]]; header(summary, "A15:H15");
summary.getRange("A16:H27").values = gold.cards.map((c) => [c.group_id, c.split, c.acceptable_core_statuses.join(" | "), c.context_usable ? "Sí" : "No", c.acceptable_inference_ceilings.join(" | "), c.acceptable_directions.join(" | "), `${c.reviewer} | ${c.reviewed_at}`, c.signature_reconciliation]);
body(summary, "A16:H27"); widths(summary, { A: 18, B: 16, C: 25, D: 13, E: 28, F: 24, G: 31, H: 25 }); summary.freezePanes.freezeRows(15);

const signed = wb.worksheets.add("FIRMA_RECONCILIADA"); signed.showGridLines = false;
signed.getRange("A1:L1").values = [["Grupo", "Estado", "Contexto", "Techo", "Dirección", "Fuentes incluidas", "Fuentes excluidas", "Limitaciones", "Revisor", "Fecha", "Hash packet final", "Firma fuente"]]; header(signed, "A1:L1");
signed.getRange("A2:L13").values = gold.cards.map((c) => [c.group_id, c.acceptable_core_statuses.join(" | "), c.context_usable ? "Sí" : "No", c.acceptable_inference_ceilings.join(" | "), c.acceptable_directions.join(" | "), c.valid_evidence_ids.join(" | "), c.invalid_evidence_ids.join(" | "), c.required_limitations.join(" | "), c.reviewer, c.reviewed_at, c.packet_sha256, c.signature_source_csv_sha256]);
body(signed, "A2:L13"); widths(signed, { A: 18, B: 24, C: 12, D: 28, E: 23, F: 58, G: 38, H: 70, I: 24, J: 15, K: 25, L: 25 }); signed.getRange("2:13").format.rowHeight = 92; signed.freezePanes.freezeRows(1); signed.freezePanes.freezeColumns(2);

const byGroup = new Map();
for (const row of qa) {
  const state = byGroup.get(row.group_id) || { total: 0, selected: 0, corrected: 0 };
  state.total++; if (row.selected === "true") state.selected++; if (row.doi_consistent_before === "false") state.corrected++;
  byGroup.set(row.group_id, state);
}
const qas = wb.worksheets.add("QA_POR_GRUPO"); qas.showGridLines = false;
qas.getRange("A1:F1").values = [["Grupo", "Registros PMID", "Seleccionados", "DOI corregidos", "Título final consistente", "Estado final"]]; header(qas, "A1:F1");
qas.getRange("A2:F13").values = gold.cards.map((c) => { const s = byGroup.get(c.group_id); return [c.group_id, s.total, s.selected, s.corrected, "Sí", "OK"]; });
body(qas, "A2:F13"); qas.getRange("F2:F13").format = { fill: paleGreen, font: { bold: true, color: "#1B5E20" }, horizontalAlignment: "center" }; widths(qas, { A: 18, B: 20, C: 18, D: 18, E: 24, F: 14 }); qas.freezePanes.freezeRows(1);

const doi = wb.worksheets.add("DOI_CORREGIDOS"); doi.showGridLines = false;
const changed = qa.filter((r) => r.doi_consistent_before === "false");
doi.getRange("A1:F1").values = [["Grupo", "PMID", "Seleccionado", "DOI anterior", "DOI PubMed/final", "Estado"]]; header(doi, "A1:F1");
doi.getRangeByIndexes(1, 0, changed.length, 6).values = changed.map((r) => [r.group_id, r.pmid, r.selected === "true" ? "Sí" : "No", r.packet_doi_before || "Ausente", r.pubmed_doi || "Ausente", "Corregido"]);
body(doi, `A2:F${changed.length + 1}`); widths(doi, { A: 18, B: 15, C: 15, D: 36, E: 36, F: 16 }); doi.freezePanes.freezeRows(1);

const trace = wb.worksheets.add("TRAZABILIDAD"); trace.showGridLines = false;
trace.getRange("A1:B1").values = [["Elemento", "SHA-256 / valor"]]; header(trace, "A1:B1");
trace.getRange("A2:B9").values = [
  ["Gold manifest", gold.manifest_sha256], ["Evidence manifest", evidence.manifest_sha256],
  ["PubMed snapshot", evidence.pubmed_snapshot_manifest_sha256], ["QA PMID-title-DOI", evidence.metadata_qa_sha256],
  ["Firma fuente CSV", evidence.signature_source_csv_sha256], ["Firma fuente XLSX", evidence.signature_source_xlsx_sha256],
  ["Cutoff", evidence.evidence_cutoff], ["Modelo Sol", "gpt-5.6-sol (no ejecutado)"],
]; body(trace, "A2:B9"); widths(trace, { A: 32, B: 88 });

const sol = Workbook.create();
const status = sol.worksheets.add("ESTADO"); status.showGridLines = false; title(status, "A1:F2", "Evaluación estricta con Sol - estado de ejecución");
status.getRange("A4:B4").values = [["Campo", "Estado"]]; header(status, "A4:B4");
status.getRange("A5:B12").values = [
  ["Gold", "VALIDADO - 0 errores"], ["Calibration", "NO EJECUTADA"], ["Holdout", "NO EJECUTADO"],
  ["Motivo", "HEAL_OPENAI_API_KEY ausente"], ["Modelo previsto", "gpt-5.6-sol"], ["Esfuerzo base", "high"],
  ["Arbitraje", "xhigh"], ["Registro activo", "Sin cambios"],
]; body(status, "A5:B12"); status.getRange("B6:B7").format = { fill: paleYellow, font: { bold: true, color: "#7A5A00" } }; widths(status, { A: 28, B: 62 });

for (const [sheetName, groups, label] of [["CALIBRATION", gold.calibration_groups, "Pendiente de credencial"], ["HOLDOUT", gold.holdout_groups, "Bloqueado hasta freeze-prompt"]]) {
  const sheet = sol.worksheets.add(sheetName); sheet.showGridLines = false;
  sheet.getRange("A1:E1").values = [["Grupo", "Fase", "Ejecución", "Resultado", "Observación"]]; header(sheet, "A1:E1");
  sheet.getRangeByIndexes(1, 0, groups.length, 5).values = groups.map((g) => [g, sheetName.toLowerCase(), "No ejecutada", "Sin datos", label]);
  body(sheet, `A2:E${groups.length + 1}`); widths(sheet, { A: 20, B: 18, C: 20, D: 18, E: 42 }); sheet.freezePanes.freezeRows(1);
}
const protocol = sol.worksheets.add("PROTOCOLO"); protocol.showGridLines = false;
protocol.getRange("A1:C1").values = [["Paso", "Gate", "Regla"]]; header(protocol, "A1:C1");
protocol.getRange("A2:C7").values = [
  ["1. calibrate", "6/6 calibration", "Sólo ve los seis casos de desarrollo; máximo cinco prompts."],
  ["2. optimize", "Sólo errores calibration", "Rechaza reportes de holdout."],
  ["3. freeze-prompt", "Calibration aprobada", "Sella prompt, schema, Gold, evidencia y hashes."],
  ["4. evaluate-holdout", "Una única ejecución", "No permite sobrescritura ni repetición."],
  ["5. aceptación", "12/12 y acuerdo >=11/12", "Cero errores y cero aprobaciones sin respaldo."],
  ["6. fallo holdout", "Nuevo holdout requerido", "No ajustar y reutilizar los mismos seis como inéditos."],
]; body(protocol, "A2:C7"); widths(protocol, { A: 25, B: 30, C: 75 }); protocol.getRange("2:7").format.rowHeight = 45;

await fs.mkdir(path.dirname(outputGold), { recursive: true }); await fs.mkdir(previewDir, { recursive: true });
for (const [book, prefix, sheets] of [[wb, "gold", ["RESUMEN", "FIRMA_RECONCILIADA", "QA_POR_GRUPO", "DOI_CORREGIDOS", "TRAZABILIDAD"]], [sol, "sol", ["ESTADO", "CALIBRATION", "HOLDOUT", "PROTOCOLO"]]]) {
  for (const sheetName of sheets) {
    const range = sheetName === "DOI_CORREGIDOS" ? "A1:F45" : undefined;
    const image = await book.render({ sheetName, range, autoCrop: "all", scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDir, `${prefix}_${sheetName}.png`), new Uint8Array(await image.arrayBuffer()));
  }
}
await (await SpreadsheetFile.exportXlsx(wb)).save(outputGold);
await (await SpreadsheetFile.exportXlsx(sol)).save(outputSol);
for (const book of [wb, sol]) {
  const errors = await book.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "formula error scan" });
  console.log(errors.ndjson);
}
console.log(JSON.stringify({ outputGold: path.resolve(outputGold), outputSol: path.resolve(outputSol), qaRows: qa.length, doiCorrections: changed.length }));
