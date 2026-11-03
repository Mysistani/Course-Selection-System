// Spreadsheet authoring only; tables contain no witness/SectionID/enrollment genes.
import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [input, output, previewDir] = process.argv.slice(2);
const tables = JSON.parse(await fs.readFile(input, 'utf8'));
const workbook = Workbook.create();
const colName = n => { let s=''; while(n) { n--; s=String.fromCharCode(65+n%26)+s; n=Math.floor(n/26); } return s; };
for (const [name, values] of Object.entries(tables)) {
  const sheet = workbook.worksheets.add(name);
  const end = colName(values[0].length);
  const range = sheet.getRange(`A1:${end}${values.length}`);
  range.values = values;
  range.format.font = { name: 'Arial', size: 10, color: '#172033' };
  range.format.rowHeight = 20;
  range.format.columnWidth = 20;
  range.format.verticalAlignment = 'center';
  const header = sheet.getRange(`A1:${end}1`);
  header.format.fill = '#243B53';
  header.format.font = { name: 'Arial', size: 10, bold: true, color: '#FFFFFF' };
  header.format.wrapText = true;
  header.format.rowHeight = 32;
  header.format.horizontalAlignment = 'center';
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  for(let c=0;c<values[0].length;c++) {
    const width = Math.min(38, Math.max(18, ...values.slice(0,30).map(row => String(row[c] ?? '').length+2)));
    sheet.getRange(`${colName(c+1)}1:${colName(c+1)}${values.length}`).format.columnWidth = width;
  }
}
workbook.recalculate();
if (previewDir) {
  await fs.mkdir(previewDir, {recursive:true});
  for(const name of Object.keys(tables)) {
    const end = colName(tables[name][0].length);
    const render = await workbook.render({sheetName:name, range:`A1:${end}${Math.min(6,tables[name].length)}`,scale:1,format:'png'});
    await fs.writeFile(`${previewDir}/${name}.png`,new Uint8Array(await render.arrayBuffer()));
  }
  const inspection = await workbook.inspect({kind:'region',sheetId:'Courses',range:'A1:L4',maxChars:2500,tableMaxRows:4,tableMaxCols:12});
  await fs.writeFile(`${previewDir}/inspection.json`, JSON.stringify(inspection));
}
await (await SpreadsheetFile.exportXlsx(workbook)).save(output);
