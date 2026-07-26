import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const targets = [
  {
    path: process.argv[2],
    ranges: [
      ["Inventory Purchase Log", "C4:T12"],
      ["Inventory Purchase Log", "C150:T159"],
    ],
  },
  {
    path: process.argv[3],
    ranges: [
      ["Transaction_report_20250101_202", "A12:AM20"],
      ["Transaction_report_20250101_202", "A47:AM55"],
    ],
  },
  {
    path: process.argv[4],
    ranges: [
      ["Cards", "A1:Q12"],
      ["Cards", "A2915:Q2925"],
    ],
  },
  {
    path: process.argv[5],
    ranges: [
      ["Hidden Richz LLC Expenses", "C3:M12"],
      ["Hidden Richz LLC Expenses", "C51:M60"],
    ],
  },
];

for (const target of targets) {
  const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(target.path));
  process.stdout.write(`\n=== ${target.path} ===\n`);
  for (const [sheetName, address] of target.ranges) {
    const range = workbook.worksheets.getItem(sheetName).getRange(address);
    process.stdout.write(`\n--- ${sheetName}!${address} values ---\n`);
    process.stdout.write(`${JSON.stringify(range.values)}\n`);
    process.stdout.write(`--- ${sheetName}!${address} formulas ---\n`);
    process.stdout.write(`${JSON.stringify(range.formulas)}\n`);
  }
}
