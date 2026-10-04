import fs from 'node:fs';
import {
  MODEL, UML, analysisPage, describeCase, expect, linksPage, record, seeded, shot, test,
} from './support';

const UML_ANALYSIS = { target: ['architecture', 'model', MODEL], settings: UML };
const COMPONENTS = ['Authentication', 'Lending'];

test('UI-08 UML analysis: the graph and the link list show component names', async ({ page, request }) => {
  describeCase({
    id: 'UI-08',
    feature: 'Display names / a UML analysis on Trace Links',
    priority: 'High',
    why: 'A UML component\'s identifier is a counter and an XMI id ("model.uml$0$_auth"). Shown raw, the graph and the list are unreadable in the demo.',
    preconditions: 'A saved requirements -> architecture model analysis at component level over model.uml (seeded through the API)',
    input: 'Open Trace Links for the analysis; read the node labels of the graph and the target names in the link list',
    expected: 'The graph\'s nodes and the link list name the components Authentication and Lending; no label shown contains a "$"',
  });
  const seed = await seeded(page, request, UML_ANALYSIS);
  await page.goto(linksPage(seed));
  await expect(page.locator('.link-row').first()).toBeVisible();

  const graph = (await page.locator('svg text').allTextContents()).map((text) => text.trim()).filter(Boolean);
  const targets = await page.locator('.link-row .link-ends > span:nth-of-type(2) b').allTextContents();
  const shown = await page.locator('.link-row .link-ends b').allTextContents();
  record(`graph labels: ${graph}`);
  record(`link list targets: ${targets}`);
  await shot(page, 'ui-08-uml-trace-links-names');

  expect(targets.length).toBeGreaterThan(0);
  expect(targets.every((name) => COMPONENTS.includes(name))).toBe(true);
  expect(graph.some((label) => COMPONENTS.some((name) => label.includes(name)))).toBe(true);
  expect([...graph, ...shown].filter((label) => label.includes('$'))).toEqual([]);
});

test('UI-10 Export from the results page downloads named, non-empty files', async ({ page, request }) => {
  describeCase({
    id: 'UI-10',
    feature: 'Export / CSV and PDF of a UML analysis',
    priority: 'High',
    why: 'Exports are what leaves the tool and goes into a report. They must arrive, and must name UML components rather than print their raw ids.',
    preconditions: 'The saved UML analysis of UI-08, opened from its version history into the results page',
    input: 'Export -> CSV, then Export -> PDF',
    expected: 'Both downloads arrive and are not empty. The CSV\'s Target column holds Authentication or Lending, never a "$" id, and its last column keeps the raw ids. The PDF names the components too',
  });
  const seed = await seeded(page, request, UML_ANALYSIS);
  await page.goto(analysisPage(seed));
  await page.locator('.version-run').getByRole('button', { name: 'Open' }).click();
  await page.waitForURL('**/results');
  await expect(page.getByRole('button', { name: 'Export' })).toBeEnabled();
  await shot(page, 'ui-10-results-page');

  const download = async (format) => {
    await page.getByRole('button', { name: 'Export' }).click();
    const arriving = page.waitForEvent('download');
    await page.getByRole('menuitem', { name: new RegExp(format) }).click();
    const file = await arriving;
    const saved = await file.path();
    return { name: file.suggestedFilename(), bytes: fs.readFileSync(saved) };
  };

  const csv = await download('CSV');
  const lines = csv.bytes.toString('utf-8').replace(/^﻿/, '').trim().split(/\r\n/);
  const header = lines[0].split(',');
  const rows = lines.slice(1).map((line) => line.split(','));
  // The matrix's columns: source, description, target, similarity, status.
  const targetColumn = 2;
  record(`CSV ${csv.name}: ${csv.bytes.length} bytes; header ${header}`);
  record(`CSV first row: ${lines[1]}`);

  const pdf = await download('PDF');
  const pdfText = pdf.bytes.toString('latin1');
  record(`PDF ${pdf.name}: ${pdf.bytes.length} bytes; names in it: ${COMPONENTS.filter((name) => pdfText.includes(name))}`);

  expect(csv.bytes.length).toBeGreaterThan(0);
  expect(pdf.bytes.length).toBeGreaterThan(0);
  const linked = rows.filter((row) => row[row.length - 1]);
  expect(linked.length).toBeGreaterThan(0);
  expect(linked.every((row) => COMPONENTS.includes(row[targetColumn]))).toBe(true);
  expect(linked.every((row) => row[row.length - 1].includes('$'))).toBe(true);
  expect(COMPONENTS.some((name) => pdfText.includes(name))).toBe(true);
});
