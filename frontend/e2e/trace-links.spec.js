import {
  REQUIREMENTS, SENTENCES, analyse, analysisPage, asFiles, describeCase, expect, linksPage,
  newProject, record, seeded, shot, signIn, signUp, test, tile, updateSide,
} from './support';

const LOGIN = REQUIREMENTS['UC1.txt'].trim();
const LOGOUT = REQUIREMENTS['UC2.txt'].trim();
const BORROW = REQUIREMENTS['UC3.txt'].trim();
const EXTRA = 'Every account is locked after five failed attempts.';
const TILES = ['Valid', 'No longer found', 'Broken', 'New', 'Uncovered'];

const counts = async (page) => Object.fromEntries(
  await Promise.all(TILES.map(async (label) => [label, await tile(page, label)])),
);

async function updateThroughDialog(page, file, files) {
  await page.locator('article.artifact-strip').filter({ hasText: file })
    .getByRole('button', { name: 'Update' }).click();
  const dialog = page.getByRole('dialog', { name: 'Update side' });
  await dialog.locator('input[type=file]').setInputFiles(asFiles(files));
  await expect(dialog.locator('.change-summary')).toBeVisible();
  await dialog.getByRole('button', { name: 'Update', exact: true }).click();
  await expect(dialog.getByRole('link', { name: 'Trace links' })).toBeVisible();
  return dialog;
}

test('UI-04 Trace Links: an inserted sentence leaves every link valid', async ({ page, request }) => {
  describeCase({
    id: 'UI-04',
    feature: 'Trace Links / a sentence inserted',
    priority: 'Critical',
    why: 'Sentences are named by position, so one inserted sentence renames every one after it. The page the demo ends on must still call those links valid, not broken.',
    preconditions: 'A saved sentence-level analysis of UC.txt (login, logout, borrow sentences) against the code, at v1',
    input: 'On the analysis page, update the requirements with a sentence about account locking inserted after the first; follow "Trace links"; read the five counts',
    expected: 'v1 -> v2: Valid 3, Broken 0, No longer found 0',
  });
  const seed = await seeded(page, request, {
    source: ['requirements', 'reqs', { 'UC.txt': `${LOGIN} ${LOGOUT} ${BORROW}\n` }], settings: SENTENCES,
  });
  await page.goto(analysisPage(seed));

  const dialog = await updateThroughDialog(page, 'UC.txt', { 'UC.txt': `${LOGIN} ${EXTRA} ${LOGOUT} ${BORROW}\n` });
  await shot(page, 'ui-04-update-inserted-sentence');
  await dialog.getByRole('link', { name: 'Trace links' }).click();
  await page.waitForURL('**/app/links**');
  await expect(page.locator('.link-row').first()).toBeVisible();
  const seen = await counts(page);
  record(`counts v1 -> v2: ${JSON.stringify(seen)}`);
  record(`link states: ${(await page.locator('.link-row .link-status').allTextContents()).map((t) => t.trim())}`);
  await shot(page, 'ui-04-trace-links-all-valid');

  expect(seen).toMatchObject({ Valid: 3, Broken: 0, 'No longer found': 0 });
});

test('UI-05 Trace Links page: tabs, five groups, two versions, reasons, Show more', async ({ page, request }) => {
  describeCase({
    id: 'UI-05',
    feature: 'Trace Links / the page itself',
    priority: 'High',
    why: 'This page is where a reader sees what an update did. Each part of it - the groups, the version choice, the reasons and the list of changes - has to be there and readable, also for a large change.',
    preconditions: 'An analysis over 55 one-line requirement files at v2, where v2 edited all 55 and removed nothing (seeded through the API)',
    input: 'Open Trace Links for the analysis; read the tabs, the five counts, the version selectors and the reasons; switch to Changes and press "Show more"',
    expected: 'Tabs Links and Changes; five groups with counts (Valid, No longer found, Broken, New, Uncovered); two version selectors at v1 -> v2; every link gives a reason ("Source changed"); Changes lists 50 files first and "Show more" shows the other 5',
  });
  const token = await signUp(request);
  const projectId = await newProject(request, token, 'Large');
  const many = (suffix) => Object.fromEntries(Array.from({ length: 55 }, (_, n) => [
    `R${String(n + 1).padStart(2, '0')}.txt`, `Requirement ${n + 1}: a visitor can login${suffix}.\n`,
  ]));
  const saved = await analyse(request, token, projectId, { source: ['requirements', 'reqs', many('')] });
  const job = await updateSide(request, token, projectId, saved.config_id, 'source', many(' with a passphrase'));
  expect(job.state, job.error).toBe('succeeded');
  await signIn(page, token);
  await page.goto(linksPage({ projectId, configId: saved.config_id }));
  await expect(page.locator('.link-row').first()).toBeVisible();

  const tabs = page.locator('.tabs').first().getByRole('button');
  const selectors = page.locator('.graph-config select');
  const reasons = [...new Set(await page.locator('.link-reason').allTextContents())];
  const seen = await counts(page);
  record(`tabs: ${await tabs.allTextContents()}; counts: ${JSON.stringify(seen)}`);
  record(`version selectors: ${await selectors.evaluateAll((all) => all.map((s) => s.selectedOptions[0].textContent))}`);
  record(`reasons shown: ${reasons}`);
  await shot(page, 'ui-05-trace-links-links-tab');

  await page.getByRole('button', { name: 'Changes', exact: true }).click();
  const files = page.locator('.change-file');
  await expect(files).toHaveCount(50);
  await shot(page, 'ui-05-trace-links-changes-tab');
  await page.getByRole('button', { name: 'Show more' }).click();
  await expect(files).toHaveCount(55);
  record('changes tab: 50 files, then 55 after "Show more"');
  await expect(page.getByRole('button', { name: 'Show more' })).toHaveCount(0);

  await expect(tabs).toHaveText(['Links', 'Changes']);
  expect(Object.keys(seen)).toEqual(TILES);
  expect(Object.values(seen).every(Number.isInteger)).toBe(true);
  await expect(selectors).toHaveCount(2);
  expect(await selectors.evaluateAll((all) => all.map((s) => s.value))).toEqual(['1', '2']);
  expect(reasons).toEqual(['Source changed']);
});

test('UI-06 Trace Links: a removed requirement file shows its link as Broken', async ({ page, request }) => {
  describeCase({
    id: 'UI-06',
    feature: 'Trace Links / a requirement removed',
    priority: 'Critical',
    why: '"Broken" is what the user is told to act on. A link whose requirement was deleted must be listed there, with the missing end marked.',
    preconditions: 'A saved requirements -> code analysis over UC1-UC4 at v1, where UC3 is linked to borrow()',
    input: 'Update the requirements with UC1, UC2 and UC4 only; open Trace links; choose the Broken group',
    expected: 'Broken 1, Valid 2. The Broken group lists UC3.txt -> borrow, with UC3.txt marked "no longer exists"',
  });
  const seed = await seeded(page, request);
  await page.goto(analysisPage(seed));

  const { 'UC3.txt': removed, ...rest } = REQUIREMENTS;
  const dialog = await updateThroughDialog(page, 'UC1.txt', rest);
  record(`removed file: UC3.txt (${removed.trim()})`);
  await dialog.getByRole('link', { name: 'Trace links' }).click();
  await expect(page.locator('.link-row').first()).toBeVisible();
  const seen = await counts(page);
  record(`counts: ${JSON.stringify(seen)}`);

  await page.locator('.tabs').nth(1).getByRole('button', { name: 'Broken', exact: true }).click();
  const rows = page.locator('.link-row');
  await expect(rows).toHaveCount(1);
  record(`broken group: "${(await rows.first().locator('.link-ends').innerText()).replace(/\s+/g, ' ')}"`);
  await shot(page, 'ui-06-trace-links-broken');

  expect(seen).toMatchObject({ Broken: 1, Valid: 2 });
  await expect(rows.first().locator('.gone')).toContainText('UC3.txt');
  await expect(rows.first().locator('.gone')).toContainText('no longer exists');
  await expect(rows.first()).toContainText('borrow');
});
