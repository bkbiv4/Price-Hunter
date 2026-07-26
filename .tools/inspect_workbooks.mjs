import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

for (const path of process.argv.slice(2)) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(path));
  const overview = await workbook.inspect({
    kind: "workbook,sheet,table",
    maxChars: 5000,
    tableMaxRows: 5,
    tableMaxCols: 15,
    tableMaxCellChars: 100,
  });
  process.stdout.write(`\n=== ${path} ===\n${overview.ndjson}\n`);

  for (const sheet of workbook.worksheets.items) {
    const region = await workbook.inspect({
      kind: "region",
      sheetId: sheet.name,
      range: "A1:Z18",
      maxChars: 4500,
      tableMaxRows: 18,
      tableMaxCols: 26,
      tableMaxCellChars: 100,
    });
    process.stdout.write(`\n--- ${sheet.name} A1:Z18 ---\n${region.ndjson}\n`);
  }
}
