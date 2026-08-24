#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const artifactToolPath = process.env.CODEX_ARTIFACT_TOOL_PATH ||
  "C:\\Users\\Usuario\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\node\\node_modules\\@oai\\artifact-tool\\dist\\artifact_tool.mjs";
const { SpreadsheetFile, Workbook } = await import(pathToFileURL(artifactToolPath).href);

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return process.argv[index + 1];
}

function columnName(index) {
  let value = index + 1;
  let output = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    output = String.fromCharCode(65 + remainder) + output;
    value = Math.floor((value - 1) / 26);
  }
  return output;
}

function cellValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

function safeTableName(name) {
  return `Heal${name.replace(/[^A-Za-z0-9]/g, "").slice(0, 180)}`;
}

const dataPath = path.resolve(argument("--data"));
const outputPath = path.resolve(argument("--output"));
const previewDir = path.resolve(argument("--preview-dir"));
const data = JSON.parse(await fs.readFile(dataPath, "utf8"));
const workbook = Workbook.create();

const palette = {
  navy: "#173B57",
  teal: "#187C82",
  cyan: "#DDF2F2",
  pale: "#F3F7F9",
  gold: "#D99A2B",
  red: "#B54747",
  green: "#2F7D61",
  ink: "#172B3A",
  white: "#FFFFFF",
  border: "#CCD8DE",
};

for (const [sheetName, rawRows] of Object.entries(data)) {
  const rows = Array.isArray(rawRows) ? rawRows : [];
  const columns = [];
  for (const row of rows) {
    for (const key of Object.keys(row || {})) if (!columns.includes(key)) columns.push(key);
  }
  if (columns.length === 0) columns.push("sin_datos");
  const sheet = workbook.worksheets.add(sheetName.slice(0, 31));
  sheet.showGridLines = false;
  const lastColumn = columnName(columns.length - 1);
  sheet.mergeCells(`A1:${lastColumn}1`);
  sheet.getRange("A1").values = [[`HEAL · ${sheetName.replaceAll("_", " ")}`]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: palette.navy,
    font: { bold: true, color: palette.white, size: 16 },
    rowHeight: 30,
    verticalAlignment: "center",
  };
  sheet.mergeCells(`A2:${lastColumn}2`);
  sheet.getRange("A2").values = [["Prototipo de desarrollo · Tier 1 firmado · validación formal pendiente"]];
  sheet.getRange(`A2:${lastColumn}2`).format = {
    fill: palette.cyan,
    font: { italic: true, color: palette.ink, size: 10 },
    rowHeight: 22,
    verticalAlignment: "center",
  };
  sheet.getRange(`A3:${lastColumn}3`).values = [columns];
  sheet.getRange(`A3:${lastColumn}3`).format = {
    fill: palette.teal,
    font: { bold: true, color: palette.white, size: 10 },
    wrapText: true,
    rowHeight: 31,
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: palette.border },
  };
  if (rows.length > 0) {
    const values = rows.map((row) => columns.map((column) => cellValue(row?.[column])));
    const dataRange = sheet.getRange(`A4:${lastColumn}${rows.length + 3}`);
    dataRange.values = values;
    dataRange.format = {
      font: { color: palette.ink, size: 9 },
      wrapText: true,
      verticalAlignment: "top",
      borders: { preset: "all", style: "thin", color: palette.border },
    };
    sheet.tables.add(`A3:${lastColumn}${rows.length + 3}`, true, safeTableName(sheetName));
  }
  sheet.freezePanes.freezeRows(3);
  sheet.freezePanes.freezeColumns(Math.min(2, columns.length));
  columns.forEach((column, index) => {
    const width = /interpret|limit|error|comment|evidence|detalle|message|hash/i.test(column)
      ? 42
      : /group|grupo|indicador|status|estado|role|stage/i.test(column)
        ? 23
        : 16;
    sheet.getRange(`${columnName(index)}:${columnName(index)}`).format.columnWidth = width;
    if (/date|created_at|reviewed_at|updated_at/i.test(column) && rows.length > 0) {
      sheet.getRange(`${columnName(index)}4:${columnName(index)}${rows.length + 3}`).setNumberFormat("yyyy-mm-dd hh:mm:ss");
    }
  });
  const statusIndex = columns.findIndex((column) => /^(status|estado|valor|inference_mode)$/.test(column));
  if (statusIndex >= 0 && rows.length > 0) {
    const statusRange = sheet.getRange(`${columnName(statusIndex)}4:${columnName(statusIndex)}${rows.length + 3}`);
    statusRange.conditionalFormats.add("containsText", { text: "valid", format: { fill: "#E4F3EA", font: { color: palette.green } } });
    statusRange.conditionalFormats.add("containsText", { text: "quarantined", format: { fill: "#FBE8E7", font: { color: palette.red } } });
    statusRange.conditionalFormats.add("containsText", { text: "incomplete", format: { fill: "#FFF1D8", font: { color: palette.gold } } });
  }
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
const inspection = await workbook.inspect({ kind: "sheet,table", maxChars: 12000, tableMaxRows: 4, tableMaxCols: 8 });
await fs.writeFile(path.join(previewDir, "workbook_inspection.ndjson"), inspection.ndjson || String(inspection), "utf8");
for (const sheetName of Object.keys(data)) {
  const preview = await workbook.render({ sheetName: sheetName.slice(0, 31), autoCrop: "all", scale: 0.6, format: "png" });
  const bytes = new Uint8Array(await preview.arrayBuffer());
  await fs.writeFile(path.join(previewDir, `${sheetName}.png`), bytes);
}
process.stdout.write(JSON.stringify({ output: outputPath, sheets: Object.keys(data), previews: previewDir }));
