// fflate and jspdf are imported dynamically inside the exporters below, so the
// ~500 KB of PDF/zip machinery stays out of the initial page load.
import { MATRIX_COLUMNS, formatSimilarity } from './matrix';

const HEADERS = MATRIX_COLUMNS.map((column) => column.label);

function toCells(row) {
  return [
    row.requirement,
    row.requirementText,
    row.code,
    row.similarity,
    row.status,
  ];
}

function timestampedName(extension) {
  // Local time, not toISOString() - a file exported just after midnight should
  // not be stamped with the previous day's UTC date.
  const now = new Date();
  const pad = (value) => String(value).padStart(2, '0');
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
    + `-${pad(now.getHours())}-${pad(now.getMinutes())}`;
  return `traceability-matrix-${stamp}.${extension}`;
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/* ------------------------------ CSV ------------------------------ */

function csvCell(value) {
  if (value === null || value === undefined) return '';
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function exportCsv(rows) {
  const lines = [HEADERS, ...rows.map((row) => toCells(row).map((cell, index) => (
    index === 3 ? formatSimilarity(row.similarity) : cell
  )))];
  const csv = lines.map((line) => line.map(csvCell).join(',')).join('\r\n');
  // BOM so Excel opens UTF-8 accented text correctly on a double click.
  download(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }), timestampedName('csv'));
}

/* ----------------------------- XLSX ------------------------------ */

const XML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' };
const escapeXml = (value) => String(value).replace(/[&<>"']/g, (char) => XML_ESCAPES[char]);

function columnLetter(index) {
  let letter = '';
  let n = index;
  while (n >= 0) {
    letter = String.fromCharCode((n % 26) + 65) + letter;
    n = Math.floor(n / 26) - 1;
  }
  return letter;
}

function sheetCell(value, rowNumber, columnIndex, styleId) {
  const ref = `${columnLetter(columnIndex)}${rowNumber}`;
  const style = styleId ? ` s="${styleId}"` : '';
  if (value === null || value === undefined || value === '') {
    return `<c r="${ref}"${style}/>`;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `<c r="${ref}"${style}><v>${value}</v></c>`;
  }
  return `<c r="${ref}"${style} t="inlineStr"><is><t xml:space="preserve">${escapeXml(value)}</t></is></c>`;
}

function buildSheet(rows) {
  const header = `<row r="1">${HEADERS
    .map((label, index) => sheetCell(label, 1, index, 1))
    .join('')}</row>`;

  const body = rows.map((row, rowIndex) => {
    const rowNumber = rowIndex + 2;
    const cells = toCells(row)
      .map((value, index) => sheetCell(value, rowNumber, index, index === 3 ? 2 : 0))
      .join('');
    return `<row r="${rowNumber}">${cells}</row>`;
  }).join('');

  const cols = MATRIX_COLUMNS
    .map((column, index) => `<col min="${index + 1}" max="${index + 1}" width="${Math.round(column.width / 7)}" customWidth="1"/>`)
    .join('');

  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<cols>${cols}</cols>
<sheetData>${header}${body}</sheetData>
<autoFilter ref="A1:${columnLetter(HEADERS.length - 1)}${rows.length + 1}"/>
</worksheet>`;
}

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>`;

const ROOT_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>`;

const WORKBOOK = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Traceability Matrix" sheetId="1" r:id="rId1"/></sheets>
</workbook>`;

const WORKBOOK_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`;

// cellXfs: 0 = default, 1 = bold header, 2 = two-decimal number.
const STYLES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="0.00"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>`;

export async function exportXlsx(rows) {
  const { zipSync, strToU8 } = await import('fflate');

  const zipped = zipSync({
    '[Content_Types].xml': strToU8(CONTENT_TYPES),
    '_rels/.rels': strToU8(ROOT_RELS),
    'xl/workbook.xml': strToU8(WORKBOOK),
    'xl/_rels/workbook.xml.rels': strToU8(WORKBOOK_RELS),
    'xl/styles.xml': strToU8(STYLES),
    'xl/worksheets/sheet1.xml': strToU8(buildSheet(rows)),
  }, { level: 6 });

  const blob = new Blob([zipped], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  download(blob, timestampedName('xlsx'));
}

/* ------------------------------ PDF ------------------------------ */

/**
 * jsPDF only wraps on whitespace, and code identifiers have none - so a long
 * `file.ts::Class::method()` would blow past its column. Break it at the `::`
 * separators, and hard-split anything still longer than a column can hold.
 */
function wrapIdentifier(value, chunkSize = 30) {
  const text = String(value);
  if (text.length <= chunkSize) return text;

  return text
    .split('::')
    .map((part, index) => (index ? `::${part}` : part))
    .flatMap((part) => part.match(new RegExp(`.{1,${chunkSize}}`, 'g')) ?? [part])
    .join('\n');
}

export async function exportPdf(rows, summary) {
  const [{ jsPDF }, { autoTable }] = await Promise.all([
    import('jspdf'),
    import('jspdf-autotable'),
  ]);

  const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' });

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(16);
  doc.text('Traceability Matrix', 40, 42);

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(9);
  doc.setTextColor(110);
  const subtitle = summary
    ? `${summary.requirements} requirements · ${summary.trace_links} trace links · ${summary.high_confidence} high confidence`
    : `${rows.length} rows`;
  doc.text(`${subtitle}  —  generated ${new Date().toLocaleString()}`, 40, 58);

  autoTable(doc, {
    startY: 72,
    head: [HEADERS],
    body: rows.map((row) => [
      wrapIdentifier(row.requirement, 18),
      row.requirementText,
      row.status === 'Missing' ? '—' : wrapIdentifier(row.code, 34),
      formatSimilarity(row.similarity),
      row.status,
    ]),
    styles: { fontSize: 8, cellPadding: 5, overflow: 'linebreak', valign: 'middle' },
    headStyles: { fillColor: [91, 79, 224], textColor: 255, fontStyle: 'bold' },
    alternateRowStyles: { fillColor: [247, 246, 252] },
    // A4 landscape is 842pt wide; 40pt margins leave 762pt for the table.
    // Description is deliberately left without a cellWidth so it absorbs the
    // leftover space - if every column is fixed, autotable cannot distribute it.
    columnStyles: {
      0: { cellWidth: 105 },
      2: { cellWidth: 215, font: 'courier', fontSize: 7 },
      3: { cellWidth: 55, halign: 'right' },
      4: { cellWidth: 55 },
    },
    margin: { left: 40, right: 40, bottom: 40 },
    didDrawPage: (data) => {
      const page = doc.internal.getNumberOfPages();
      doc.setFontSize(8);
      doc.setTextColor(140);
      doc.text(
        `TraceRAG · page ${page}`,
        data.settings.margin.left,
        doc.internal.pageSize.getHeight() - 20,
      );
    },
  });

  doc.save(timestampedName('pdf'));
}
