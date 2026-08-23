import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [dataPath, outputPath, previewDir] = process.argv.slice(2);
if (!dataPath || !outputPath || !previewDir) {
  throw new Error("Usage: node build_semantic_v1_audit_workbook.mjs data.json output.xlsx preview_dir");
}

const sheetsData = JSON.parse(await fs.readFile(dataPath, "utf8"));
const workbook = Workbook.create();
const navy = "#0B2545";
const blue = "#2E74B5";
const pale = "#E8EEF5";
const light = "#F4F6F9";
const red = "#9B1C1C";
const green = "#1F6D42";
const amber = "#8A6500";

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
  if (typeof value === "boolean") return value ? "Sí" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

for (const [sheetName, rows] of Object.entries(sheetsData)) {
  const sheet = workbook.worksheets.add(sheetName.substring(0, 31));
  sheet.showGridLines = false;
  const headers = rows.length ? Object.keys(rows[0]) : ["estado"];
  const matrix = rows.length ? rows.map((row) => headers.map((header) => normalize(row[header]))) : [["Sin datos"]];
  const finalCol = colName(headers.length - 1);
  const lastRow = matrix.length + 2;

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
    font: { bold: true, color: navy, size: 9 },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: "#B7C4D3" },
  };
  sheet.getRange(`A2:${finalCol}2`).format.rowHeight = 32;
  sheet.getRange(`A3:${finalCol}${lastRow}`).values = matrix;
  sheet.getRange(`A3:${finalCol}${lastRow}`).format = {
    font: { color: "#1F2937", size: 9 },
    verticalAlignment: "top",
    wrapText: true,
    borders: { preset: "insideHorizontal", style: "thin", color: "#E5E7EB" },
  };

  const used = sheet.getRange(`A3:${finalCol}${lastRow}`);
  used.conditionalFormats.add("containsText", { text: "FALL", format: { fill: "#FDECEC", font: { color: red, bold: true } } });
  used.conditionalFormats.add("containsText", { text: "NO INICI", format: { fill: "#FFF7E0", font: { color: amber, bold: true } } });
  used.conditionalFormats.add("containsText", { text: "Válido", format: { fill: "#EAF6EF", font: { color: green } } });

  sheet.freezePanes.freezeRows(2);
  sheet.getRange(`A1:${finalCol}${lastRow}`).format.autofitColumns();
  sheet.getRange(`A1:${finalCol}${Math.min(lastRow, 45)}`).format.autofitRows();
  for (let column = 0; column < headers.length; column += 1) {
    const header = headers[column].toLowerCase();
    let width = 17;
    if (header.includes("detalle") || header.includes("error") || header.includes("limit") || header.includes("evidencia") || header.includes("motivo")) width = 42;
    if (header.includes("ruta") || header.includes("archivo")) width = 48;
    if (header.includes("sha256") || header.includes("hash") || header.includes("response_id")) width = 38;
    if (header.includes("grupo") || header.includes("fase") || header.includes("rol") || header.includes("estado")) width = 16;
    sheet.getRange(`${colName(column)}:${colName(column)}`).format.columnWidth = width;
  }
  if (sheetName === "RESUMEN") {
    sheet.getRange("A:A").format.columnWidth = 30;
    sheet.getRange("B:B").format.columnWidth = 28;
    sheet.getRange("C:C").format.columnWidth = 62;
    sheet.getRange(`A3:A${lastRow}`).format = { fill: light, font: { bold: true, color: navy, size: 9 }, wrapText: true };
  }
  if (["GOLD_VS_SOL", "FUENTES", "TELEMETRIA_COSTOS"].includes(sheetName)) {
    sheet.getRange(`A3:${finalCol}${Math.min(lastRow, 220)}`).format.rowHeight = 38;
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
  const safeName = sheetName.substring(0, 31);
  const headers = rows.length ? Object.keys(rows[0]) : ["estado"];
  const finalCol = colName(headers.length - 1);
  const previewRows = Math.min(rows.length + 2, 18);
  const preview = await workbook.render({ sheetName: safeName, range: `A1:${finalCol}${Math.max(3, previewRows)}`, scale: 1.15, format: "png" });
  await fs.writeFile(path.join(previewDir, `${safeName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
console.log(JSON.stringify({ status: "exported", outputPath, sheets: Object.keys(sheetsData) }));
