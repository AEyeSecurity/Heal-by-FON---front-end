import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [dataPath, outputPath, previewDir] = process.argv.slice(2);
if (!dataPath || !outputPath || !previewDir) {
  throw new Error("Usage: node build_candidate1_audit_workbook.mjs data.json output.xlsx preview_dir");
}

const sheetsData = JSON.parse(await fs.readFile(dataPath, "utf8"));
const workbook = Workbook.create();
const navy = "#0B2545";
const blue = "#2E74B5";
const pale = "#E8EEF5";
const light = "#F4F6F9";
const red = "#9B1C1C";
const green = "#1F6D42";
const gray = "#5B6573";

function colName(index) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function normalize(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

for (const [sheetName, rows] of Object.entries(sheetsData)) {
  const sheet = workbook.worksheets.add(sheetName);
  sheet.showGridLines = false;
  const headers = rows.length ? Object.keys(rows[0]) : ["estado"];
  const matrix = rows.length ? rows.map((row) => headers.map((header) => normalize(row[header]))) : [["Sin datos"]];
  const finalCol = colName(headers.length - 1);
  const finalRow = Math.max(3, matrix.length + 2);

  sheet.getRange(`A1:${finalCol}1`).merge();
  sheet.getRange("A1").values = [[sheetName.replaceAll("_", " ")]];
  sheet.getRange(`A1:${finalCol}1`).format = {
    fill: navy,
    font: { bold: true, color: "#FFFFFF", size: 16 },
    verticalAlignment: "center",
  };
  sheet.getRange(`A1:${finalCol}1`).format.rowHeight = 28;

  sheet.getRange(`A2:${finalCol}2`).values = [headers.map((header) => header.replaceAll("_", " ").toUpperCase())];
  sheet.getRange(`A2:${finalCol}2`).format = {
    fill: pale,
    font: { bold: true, color: navy, size: 10 },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: "#B7C4D3" },
  };
  sheet.getRange(`A2:${finalCol}2`).format.rowHeight = 30;
  sheet.getRange(`A3:${finalCol}${matrix.length + 2}`).values = matrix;
  sheet.getRange(`A3:${finalCol}${matrix.length + 2}`).format = {
    font: { color: "#1F2937", size: 9 },
    verticalAlignment: "top",
    wrapText: true,
    borders: { preset: "insideHorizontal", style: "thin", color: "#E5E7EB" },
  };

  if (sheetName === "RESUMEN") {
    sheet.getRange("A3:A8").format = { fill: light, font: { bold: true, color: navy, size: 10 } };
    sheet.getRange("B3:B8").format = { font: { bold: true, color: gray, size: 10 }, wrapText: true };
    sheet.getRange("B3").format = { fill: "#FDECEC", font: { bold: true, color: red, size: 10 } };
  }
  if (["CALIBRATION", "HOLDOUT", "ERRORES_CONFLICTOS"].includes(sheetName)) {
    const used = sheet.getRange(`A3:${finalCol}${matrix.length + 2}`);
    used.conditionalFormats.add("containsText", { text: "FALLIDO", format: { fill: "#FDECEC", font: { color: red, bold: true } } });
    used.conditionalFormats.add("containsText", { text: "NO INICI", format: { fill: "#FFF7E0", font: { color: "#7A5A00" } } });
    used.conditionalFormats.add("containsText", { text: "PAS", format: { fill: "#EAF6EF", font: { color: green, bold: true } } });
  }

  sheet.freezePanes.freezeRows(2);
  sheet.getRange(`A1:${finalCol}${finalRow}`).format.autofitColumns();
  sheet.getRange(`A1:${finalCol}${Math.min(finalRow, 40)}`).format.autofitRows();
  for (let column = 0; column < headers.length; column += 1) {
    const header = headers[column].toLowerCase();
    let width = 18;
    if (header.includes("titulo") || header.includes("detalle") || header.includes("error") || header.includes("limit") || header.includes("evidence")) width = 42;
    if (header.includes("ruta") || header.includes("archivo") || header.includes("url")) width = 48;
    if (header.includes("sha256") || header.includes("hash")) width = 36;
    if (header.includes("grupo") || header.includes("fase") || header.includes("rol") || header.includes("estado")) width = 16;
    sheet.getRange(`${colName(column)}:${colName(column)}`).format.columnWidth = width;
  }
  if (sheetName === "RESUMEN") {
    sheet.getRange("B:B").format.columnWidth = 52;
  }
  if (sheetName === "FUENTES") {
    sheet.getRange("I:I").format.columnWidth = 28;
  }
  if (["GOLD_VS_SOL", "PROMPTS", "ERRORES_CONFLICTOS", "HASHES"].includes(sheetName)) {
    sheet.getRange(`A3:${finalCol}${matrix.length + 2}`).format.rowHeight = 42;
  }
  if (sheetName === "FUENTES") {
    sheet.getRange(`A3:${finalCol}${Math.min(matrix.length + 2, 200)}`).format.rowHeight = 34;
  }
  if (sheetName === "TELEMETRIA_COSTOS") {
    sheet.getRange(`A3:${finalCol}${matrix.length + 2}`).format.rowHeight = 46;
  }
}

const inspect = await workbook.inspect({
  kind: "sheet,match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  maxChars: 6000,
});
await fs.mkdir(previewDir, { recursive: true });
await fs.writeFile(path.join(previewDir, "workbook_inspect.ndjson"), inspect.ndjson ?? "", "utf8");

for (const [sheetName, rows] of Object.entries(sheetsData)) {
  const headers = rows.length ? Object.keys(rows[0]) : ["estado"];
  const finalCol = colName(headers.length - 1);
  const previewRows = Math.min(rows.length + 2, 18);
  const preview = await workbook.render({ sheetName, range: `A1:${finalCol}${Math.max(3, previewRows)}`, scale: 1.25, format: "png" });
  await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
console.log(JSON.stringify({ status: "exported", outputPath, sheets: Object.keys(sheetsData) }));
