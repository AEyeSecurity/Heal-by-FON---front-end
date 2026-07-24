import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const analysisDir = process.argv[2];
if (!analysisDir) throw new Error("Usage: node build_llm_readiness_workbook.mjs <analysis-dir>");

const outputPath = path.join(analysisDir, "v2_llm_readiness_workbook.xlsx");
const previewDir = path.join(analysisDir, "workbook-previews");
await fs.mkdir(previewDir, { recursive: true });

const COLORS = {
  navy: "#15324B",
  blue: "#2E74B5",
  paleBlue: "#E8EEF5",
  paleGreen: "#E7F3EC",
  paleRed: "#FCE8E6",
  paleGold: "#FFF4CE",
  gray: "#5F6B76",
  border: "#CBD5DF",
  white: "#FFFFFF",
};

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field.length || row.length) {
    row.push(field.replace(/\r$/, ""));
    rows.push(row);
  }
  const headers = rows.shift() || [];
  if (headers.length) headers[0] = headers[0].replace(/^\uFEFF/, "");
  return rows.filter((values) => values.some((value) => value !== "")).map((values) =>
    Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""])),
  );
}

async function readCsv(name) {
  return parseCsv(await fs.readFile(path.join(analysisDir, name), "utf8"));
}

function coerce(value) {
  if (value === "") return "";
  if (/^-?\d+(\.\d+)?$/.test(value) && value.length < 16) return Number(value);
  return value;
}

function columnName(index) {
  let result = "";
  let current = index + 1;
  while (current > 0) {
    current -= 1;
    result = String.fromCharCode(65 + (current % 26)) + result;
    current = Math.floor(current / 26);
  }
  return result;
}

function valuesFor(rows, columns) {
  return [columns, ...rows.map((row) => columns.map((column) => coerce(row[column] ?? "")))];
}

function truncate(value, max = 420) {
  const text = String(value ?? "");
  return text.length <= max ? text : `${text.slice(0, max)} ... [truncated; see source CSV]`;
}

function compactEvidenceRows(rows) {
  const longFields = ["clinvar_trait_names", "gwas_top_traits", "pharmgkb_clinical_summary", "information_available", "information_limitations"];
  return rows.map((row) => Object.fromEntries(Object.entries(row).map(([key, value]) => [key, longFields.includes(key) ? truncate(value) : value])));
}

function styleDataSheet(sheet, rowCount, colCount, title, widths = {}) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(Math.min(2, colCount));
  const lastColumn = columnName(colCount - 1);
  const header = sheet.getRange(`A1:${lastColumn}1`);
  header.format.fill = COLORS.navy;
  header.format.font = { bold: true, color: COLORS.white, size: 10 };
  header.format.wrapText = true;
  header.format.rowHeight = 32;
  const used = sheet.getRange(`A1:${lastColumn}${Math.max(2, rowCount + 1)}`);
  used.format.font = { name: "Aptos", size: 9 };
  used.format.verticalAlignment = "center";
  used.format.borders = { preset: "all", style: "thin", color: COLORS.border };
  used.format.wrapText = true;
  for (let index = 0; index < colCount; index += 1) {
    sheet.getRange(`${columnName(index)}:${columnName(index)}`).format.columnWidth = widths[index] || 16;
  }
  if (rowCount > 0) {
    const table = sheet.tables.add(`A1:${lastColumn}${rowCount + 1}`, true, `${title.replace(/[^A-Za-z0-9]/g, "")}Table`);
    table.style = "TableStyleMedium2";
    table.showBandedRows = true;
  }
}

function addDataSheet(workbook, name, rows, columns, widths = {}) {
  const sheet = workbook.worksheets.add(name);
  const matrix = valuesFor(rows, columns);
  sheet.getRangeByIndexes(0, 0, matrix.length, columns.length).values = matrix;
  styleDataSheet(sheet, rows.length, columns.length, name, widths);
  return sheet;
}

const summary = JSON.parse(await fs.readFile(path.join(analysisDir, "llm_readiness_summary.json"), "utf8"));
const completeness = await readCsv("llm_readiness_field_completeness.csv");
const variantAudit = await readCsv("llm_readiness_variant_audit.csv");
const conflicts = await readCsv("llm_readiness_annotation_conflicts.csv");
const groups = await readCsv("llm_readiness_group_summary.csv");
const samplePhysical = await readCsv("llm_readiness_sample_50_physical.csv");
const sampleModules = await readCsv("llm_readiness_sample_50_module_projection.csv");
const canonical = await readCsv("llm_readiness_canonical_status.csv");
const mechanisms = await readCsv("mechanism_registry_v1_template.csv");
const bioReview = await readCsv("bioinformatician_review_template.csv");

const workbook = Workbook.create();
workbook.comments.setSelf({ displayName: "HEAL QA" });

const overview = workbook.worksheets.add("Overview");
overview.showGridLines = false;
overview.getRange("A1:H2").merge();
overview.getRange("A1").values = [["HEAL Genomics v2 - LLM1 Readiness Audit"]];
overview.getRange("A1:H2").format.fill = COLORS.navy;
overview.getRange("A1:H2").format.font = { name: "Aptos Display", size: 22, bold: true, color: COLORS.white };
overview.getRange("A1:H2").format.verticalAlignment = "center";
overview.getRange("A4:H5").merge();
overview.getRange("A4").values = [["DECISION: NO-GO for production LLM1. GO for deterministic QA and dry-run payloads only."]];
overview.getRange("A4:H5").format.fill = COLORS.paleRed;
overview.getRange("A4:H5").format.font = { name: "Aptos", size: 13, bold: true, color: "#9B1C1C" };
overview.getRange("A4:H5").format.wrapText = true;
overview.getRange("A7:C7").values = [["Pipeline stage", "Unit", "Count"]];
overview.getRange("A8:C13").values = [
  ["Normalization", "physical candidates", summary.counts.normalized_variants],
  ["Variant-gene match", "variant-gene", summary.counts.variant_gene_matches],
  ["Expanded match", "variant-gene-module", summary.counts.matched_module_rows],
  ["AI triage", "eligible module rows", summary.counts.triage_rows],
  ["Enrichment", "unique physical variants", summary.counts.physical_variants],
  ["Future grouping", "gene-module", summary.counts.gene_module_groups],
];
overview.getRange("E7:H7").values = [["Gate", "Status", "Blocking reason", "Owner"]];
const gateRows = Object.entries(summary.gates).map(([gate, value]) => [gate, value.status, value.reason, gate === "llm1_pilot_ready" ? "Bioinformatician + Product" : "Engineering + Bioinformatics"]);
overview.getRangeByIndexes(7, 4, gateRows.length, 4).values = gateRows;
overview.getRange("A16:D16").values = [["Finding", "Count", "% physical", "Action"]];
const findingRows = [
  ["VEP errors", summary.findings.vep_errors, null, "Retry or keep blocked"],
  ["Unresolved identity", summary.findings.identity_unresolved, null, "Do not promote secondary evidence"],
  ["Source error variants", summary.findings.source_error_variants, null, "Separate from not_found"],
  ["SpliceAI <0.10", summary.findings.spliceai_zero_signal, null, "No attention increment"],
  ["Intermediate prefilter leakage", summary.findings.normalization_prefilter_leakage_rows, null, "Fix before performance sign-off"],
];
overview.getRange("A17:D21").values = findingRows;
overview.getRange("C17").formulas = [["=B17/$C$12"]];
overview.getRange("C17:C20").fillDown();
overview.getRange("C17:C20").format.numberFormat = "0.0%";
overview.getRange("A23:H24").merge();
overview.getRange("A23").values = [["Biological interpretation boundary: the workbook supports technical prioritization and professional review. It does not establish diagnosis, penetrance, causality, or treatment response."]];
overview.getRange("A23:H24").format.fill = COLORS.paleGold;
overview.getRange("A23:H24").format.wrapText = true;
overview.getRange("A23:H24").format.font = { name: "Aptos", size: 11, color: COLORS.navy };
for (const range of ["A7:C7", "E7:H7", "A16:D16"]) {
  overview.getRange(range).format.fill = COLORS.blue;
  overview.getRange(range).format.font = { bold: true, color: COLORS.white };
}
for (const range of ["A7:C13", "E7:H11", "A16:D20"]) {
  overview.getRange(range).format.borders = { preset: "all", style: "thin", color: COLORS.border };
  overview.getRange(range).format.wrapText = true;
  overview.getRange(range).format.verticalAlignment = "center";
}
overview.getRange("A:H").format.columnWidth = 18;
overview.getRange("A:A").format.columnWidth = 25;
overview.getRange("C:C").format.columnWidth = 14;
overview.getRange("E:E").format.columnWidth = 25;
overview.getRange("F:F").format.columnWidth = 12;
overview.getRange("G:G").format.columnWidth = 54;
overview.getRange("H:H").format.columnWidth = 24;
overview.freezePanes.freezeRows(2);

addDataSheet(workbook, "Field Completeness", completeness, ["field", "rows", "populated", "populated_pct", "zero_like", "usable_nonzero"], { 0: 34, 1: 12, 2: 12, 3: 14, 4: 12, 5: 16 });

const comparisonRows = [];
for (const [source, statuses] of Object.entries(summary.previous_run_comparison?.source_status_deltas || {})) {
  for (const [status, values] of Object.entries(statuses)) {
    comparisonRows.push({ source, status, previous: values.previous, current: values.current, delta: values.delta });
  }
}
addDataSheet(workbook, "Run Comparison", comparisonRows, ["source", "status", "previous", "current", "delta"], { 0: 28, 1: 20, 2: 14, 3: 14, 4: 14 });

const variantColumns = [
  "variant_key", "chrom_vcf", "pos_vcf", "ref_vcf", "alt_vcf", "resolved_rsid", "candidate_rsid",
  "gene_symbols", "module_ids", "identity_axis", "functional_axis", "evidence_axis", "readiness_axis",
  "transcript_concordance", "vep_status", "vep_most_severe_consequence", "vep_hgvsc", "vep_hgvsp",
  "vep_cadd_phred", "vep_revel_score", "spliceai_max_ds", "spliceai_signal_class",
  "clinvar_normalized_classification", "clinvar_review_status", "clinvar_trait_names",
  "population_max_frequency", "gwas_association_count", "gwas_top_traits",
  "pharmgkb_clinical_annotation_count", "pharmgkb_clinical_summary", "source_error_sources",
  "information_available", "information_limitations",
];
const vaSheet = addDataSheet(workbook, "Variant Audit 5910", compactEvidenceRows(variantAudit), variantColumns, { 0: 27, 7: 14, 8: 14, 9: 20, 10: 19, 11: 25, 12: 24, 13: 25, 15: 28, 16: 26, 17: 26, 22: 27, 23: 24, 24: 40, 27: 45, 29: 48, 30: 24, 31: 55, 32: 55 });
vaSheet.getRange(`M2:M${variantAudit.length + 1}`).conditionalFormats.add("containsText", { text: "blocked", format: { fill: COLORS.paleRed, font: { color: "#9B1C1C" } } });
vaSheet.getRange(`M2:M${variantAudit.length + 1}`).conditionalFormats.add("containsText", { text: "high", format: { fill: COLORS.paleGreen, font: { color: "#1E6B45" } } });

const conflictSheet = addDataSheet(workbook, "Annotation Conflicts", conflicts, ["variant_key", "approved_symbol", "module_id", "local_region_class", "vep_most_severe_consequence", "vep_target_gene_effect_status", "conflict_class", "detail", "readiness_axis"], { 0: 28, 1: 14, 2: 11, 3: 26, 4: 29, 5: 25, 6: 29, 7: 64, 8: 24 });
conflictSheet.getRange(`G2:G${conflicts.length + 1}`).conditionalFormats.add("containsText", { text: "discordant", format: { fill: COLORS.paleGold } });

const groupSheet = addDataSheet(workbook, "Group Readiness", groups, ["group_id", "gene", "module_id", "module_name", "system_within_module", "physical_variant_count", "module_row_count", "high_or_moderate_variants", "blocked_variants", "readiness_counts", "evidence_counts", "group_payload_ready", "blocking_reasons"], { 0: 20, 1: 14, 2: 11, 3: 40, 4: 34, 9: 44, 10: 44, 11: 18, 12: 56 });
groupSheet.getRange(`L2:L${groups.length + 1}`).conditionalFormats.add("containsText", { text: "false", format: { fill: COLORS.paleRed, font: { color: "#9B1C1C" } } });

const sampleColumns = ["sample_category", ...variantColumns.filter((column) => column !== "candidate_rsid")];
addDataSheet(workbook, "Sample 50 Physical", compactEvidenceRows(samplePhysical), sampleColumns, { 0: 30, 1: 27, 8: 14, 9: 14, 13: 25, 14: 24, 15: 25, 17: 29, 24: 27, 26: 42, 29: 45, 31: 48, 33: 56 });

const moduleColumns = ["variant_key", "approved_symbol", "full_gene_name", "module_id", "module_name", "system_within_module", "tier", "module_status", "evidence_tier", "local_region_class", "triage_decision", "triage_reason", "identity_match_class", "vep_status", "vep_most_severe_consequence", "clinvar_normalized_classification", "clinvar_trait_names", "gwas_top_traits", "pharmgkb_clinical_summary"];
addDataSheet(workbook, "Sample 50 Modules", sampleModules, moduleColumns, { 0: 28, 1: 15, 2: 30, 3: 11, 4: 42, 5: 35, 9: 27, 11: 42, 12: 24, 14: 29, 15: 27, 16: 45, 17: 45, 18: 50 });

addDataSheet(workbook, "Canonical Status", canonical, ["canon_row_id", "gene", "full_gene_name", "module_id", "module_name", "tier", "module_status", "evidence_tier", "canonical_status", "observed_physical_variant_count", "callable", "callability_reason", "hom_ref", "canonical_snp_status", "cnv_status", "vntr_status"], { 0: 18, 1: 14, 2: 32, 3: 11, 4: 42, 8: 18, 11: 58, 13: 46 });

const mechanismSheet = addDataSheet(workbook, "Mechanism Registry", mechanisms, ["mechanism_registry_version", "gene", "module_id", "module_name", "system_within_module", "biological_function", "pathway", "directionality", "related_systems", "related_modules", "mechanism_evidence_tier", "source_ids_or_urls", "curation_status", "curation_notes"], { 0: 28, 1: 14, 2: 11, 3: 40, 4: 35, 5: 52, 6: 40, 7: 40, 8: 35, 9: 28, 10: 20, 11: 50, 12: 30, 13: 50 });
mechanismSheet.getRange(`M2:M${mechanisms.length + 1}`).dataValidation = { rule: { type: "list", values: ["needs_bioinformatician_curation", "in_review", "approved", "rejected"] } };

const reviewColumns = ["variant_key", "sample_category", "gene_symbols", "module_ids", "identity_axis", "functional_axis", "evidence_axis", "transcript_concordance", "readiness_axis", "information_available", "information_limitations", "reviewer", "review_date", "identity_ok", "transcript_context_ok", "evidence_attribution_ok", "biological_coherence", "decision", "comments"];
const reviewSheet = addDataSheet(workbook, "Bioinfo Review", bioReview, reviewColumns, { 0: 28, 1: 30, 2: 15, 3: 14, 4: 24, 5: 24, 6: 27, 7: 28, 8: 24, 9: 58, 10: 58, 11: 22, 12: 14, 13: 16, 14: 22, 15: 24, 16: 26, 17: 22, 18: 62 });
for (const col of ["N", "O", "P"]) reviewSheet.getRange(`${col}2:${col}${bioReview.length + 1}`).dataValidation = { rule: { type: "list", values: ["yes", "no", "uncertain"] } };
reviewSheet.getRange(`Q2:Q${bioReview.length + 1}`).dataValidation = { rule: { type: "list", values: ["coherent", "contextual", "conflicting", "insufficient"] } };
reviewSheet.getRange(`R2:R${bioReview.length + 1}`).dataValidation = { rule: { type: "list", values: ["approve", "correct", "exclude", "escalate"] } };
reviewSheet.getRange(`R2:R${bioReview.length + 1}`).conditionalFormats.add("containsText", { text: "approve", format: { fill: COLORS.paleGreen } });
reviewSheet.getRange(`R2:R${bioReview.length + 1}`).conditionalFormats.add("containsText", { text: "exclude", format: { fill: COLORS.paleRed } });

const sources = workbook.worksheets.add("Sources and Guide");
sources.showGridLines = false;
sources.getRange("A1:F2").merge();
sources.getRange("A1").values = [["Sources, definitions, and review order"]];
sources.getRange("A1:F2").format.fill = COLORS.navy;
sources.getRange("A1:F2").format.font = { size: 20, bold: true, color: COLORS.white };
sources.getRange("A4:B4").values = [["Source", "Use"]];
sources.getRange("A5:B11").values = [
  ["HEAL Business Brief v.1", "Product requirements and guardrails"],
  ["HEAL Genetics Product Specification", "Canon-first architecture and layer separation"],
  ["Master Prompt Karen", "Voice, uncertainty and limits only; not perimenopause content"],
  ["https://rest.ensembl.org/documentation/info/vep_region_post", "VEP coordinate annotation"],
  ["https://www.ncbi.nlm.nih.gov/clinvar/docs/maintenance_use/", "ClinVar use and provenance"],
  [summary.run_id, "Audited HEAL runtime run"],
  [`'${summary.created_at}`, "Audit generation timestamp"],
];
sources.getRange("D4:F4").values = [["Review order", "Goal", "Rule"]];
sources.getRange("D5:F10").values = [
  [1, "Read Overview", "Understand scope and gates"],
  [2, "Review Sample 50 Physical", "Validate public evidence"],
  [3, "Review Sample 50 Modules", "Validate canon context"],
  [4, "Resolve Annotation Conflicts", "Transcript/identity review"],
  [5, "Complete Mechanism Registry", "No LLM-invented mechanisms"],
  [6, "Complete Bioinfo Review", "Approve/correct/exclude/escalate"],
];
sources.getRange("A13:F15").merge();
sources.getRange("A13").values = [["Status semantics: success = usable provider response; not_found = queried with no evidence; source_error = operational failure; not_queried = source was not applicable or identity was insufficient. Never merge these states."]];
sources.getRange("A13:F15").format.fill = COLORS.paleGold;
sources.getRange("A13:F15").format.wrapText = true;
for (const range of ["A4:B4", "D4:F4"]) {
  sources.getRange(range).format.fill = COLORS.blue;
  sources.getRange(range).format.font = { bold: true, color: COLORS.white };
}
sources.getRange("A4:B11").format.borders = { preset: "all", style: "thin", color: COLORS.border };
sources.getRange("D4:F10").format.borders = { preset: "all", style: "thin", color: COLORS.border };
sources.getRange("A:F").format.columnWidth = 24;
sources.getRange("A:A").format.columnWidth = 48;
sources.getRange("B:B").format.columnWidth = 55;
sources.getRange("D:D").format.columnWidth = 12;
sources.getRange("E:E").format.columnWidth = 34;
sources.getRange("F:F").format.columnWidth = 42;

const exportBlob = await SpreadsheetFile.exportXlsx(workbook);
await exportBlob.save(outputPath);
summary.outputs = { ...(summary.outputs || {}), readinessWorkbookXlsx: outputPath };
await fs.writeFile(path.join(analysisDir, "llm_readiness_summary.json"), JSON.stringify(summary, null, 2), "utf8");

const previewRanges = {
  "Overview": "A1:H25",
  "Field Completeness": "A1:F28",
  "Run Comparison": "A1:E28",
  "Variant Audit 5910": "A1:AG28",
  "Annotation Conflicts": "A1:I28",
  "Group Readiness": "A1:M28",
  "Sample 50 Physical": "A1:AF28",
  "Sample 50 Modules": "A1:S28",
  "Canonical Status": "A1:P28",
  "Mechanism Registry": "A1:N28",
  "Bioinfo Review": "A1:S28",
  "Sources and Guide": "A1:F16",
};
const sheetNames = Object.keys(previewRanges);
for (const sheetName of sheetNames) {
  const preview = await workbook.render({ sheetName, range: previewRanges[sheetName], scale: 1.1, format: "png" });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(path.join(previewDir, `${sheetName.replace(/[^A-Za-z0-9]/g, "_")}.png`), bytes);
}

const overviewInspect = await workbook.inspect({ kind: "table", range: "Overview!A1:H24", include: "values,formulas", tableMaxRows: 25, tableMaxCols: 8, maxChars: 6000 });
const errorInspect = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan", maxChars: 3000 });
console.log(JSON.stringify({ outputPath, previewDir, sheets: sheetNames.length, overview: overviewInspect.ndjson, errors: errorInspect.ndjson }));
