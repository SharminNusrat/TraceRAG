import {
  REQUIREMENTS, analysisPage, asFiles, describeCase, expect, record, seeded, shot, test, versionsOf,
} from './support';

/** The card of the side holding `file`, and the Update dialog it opens. */
const sideCard = (page, file) => page.locator('article.artifact-strip').filter({ hasText: file });
const updateDialog = (page) => page.getByRole('dialog', { name: 'Update side' });

async function openUpdate(page, file) {
  await sideCard(page, file).getByRole('button', { name: 'Update' }).click();
  const dialog = updateDialog(page);
  await expect(dialog.getByRole('button', { name: /Upload files/ })).toBeVisible();
  return dialog;
}

test('UI-02 Analysis page: both sides, their files, and how each is updated', async ({ page, request }) => {
  describeCase({
    id: 'UI-02',
    feature: 'Analysis page / sides and update options',
    priority: 'High',
    why: 'Each side is updated on its own, and only code can come from GitHub. The page must show what each side holds and offer the right ways to update it.',
    preconditions: 'A saved requirements -> code analysis over UC1-UC4 and Auth.java, Loans.java (seeded through the API)',
    input: 'Open the analysis page; open Update on the code side, then on the requirements side',
    expected: 'Two side cards listing their files, each with an Update button. The code side offers Upload files and Connect (GitHub); the requirements side offers Upload files only',
  });
  const seed = await seeded(page, request);
  await page.goto(analysisPage(seed));

  const requirements = sideCard(page, 'UC1.txt');
  const code = sideCard(page, 'Auth.java');
  await expect(page.locator('article.artifact-strip')).toHaveCount(2);
  record(`requirements side files: ${await requirements.locator('.side-files code').allTextContents()}`);
  record(`code side files: ${await code.locator('.side-files code').allTextContents()}`);
  await expect(requirements.getByRole('button', { name: 'Update' })).toBeVisible();
  await expect(code.getByRole('button', { name: 'Update' })).toBeVisible();
  await shot(page, 'ui-02-analysis-page');

  let dialog = await openUpdate(page, 'Auth.java');
  const codeOptions = await dialog.locator('.sync-state button').allTextContents();
  record(`code side offers: ${codeOptions.map((text) => text.trim())}`);
  await expect(dialog.getByRole('button', { name: 'Connect' })).toBeVisible();
  await shot(page, 'ui-02-update-code-side');
  await dialog.getByRole('button', { name: 'Cancel' }).click();

  dialog = await openUpdate(page, 'UC1.txt');
  const requirementOptions = await dialog.locator('.sync-state button').allTextContents();
  record(`requirements side offers: ${requirementOptions.map((text) => text.trim())}`);
  await shot(page, 'ui-02-update-requirements-side');

  await expect(requirements.locator('.side-files code')).toHaveText(['UC1.txt', 'UC2.txt', 'UC3.txt', 'UC4.txt']);
  await expect(code.locator('.side-files code')).toHaveText(['Auth.java', 'Loans.java']);
  await expect(dialog.getByRole('button', { name: 'Connect' })).toHaveCount(0);
  expect(requirementOptions.map((text) => text.trim())).toEqual(['Upload files']);
});

test('UI-03 Update: identical files change nothing, a cosmetic edit makes v2', async ({ page, request }) => {
  describeCase({
    id: 'UI-03',
    feature: 'Update dialog / no change, then a small edit',
    priority: 'Critical',
    why: 'Uploading the same files again is common and must not make an empty version; a real edit must show what changed before it is applied and then appear in the history.',
    preconditions: 'A saved requirements -> code analysis at v1 (seeded through the API)',
    input: 'Update the requirements side with exactly the same 4 files. Then update it again with UC1.txt reworded ("A visitor performs a login, with a passphrase.") and press Update',
    expected: 'Same files: "No file differs from what this side holds.", the Update button disabled, still only v1. Edited file: the change summary lists 1 file modified before updating; afterwards the dialog reports version 2 and the version history lists v2 above v1',
  });
  const seed = await seeded(page, request);
  await page.goto(analysisPage(seed));

  let dialog = await openUpdate(page, 'UC1.txt');
  await dialog.locator('input[type=file]').setInputFiles(asFiles(REQUIREMENTS));
  await expect(dialog.getByText('No file differs from what this side holds.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Update', exact: true })).toBeDisabled();
  record(`same files: "${await dialog.locator('.dialog-note').last().textContent()}"; Update enabled: `
    + `${await dialog.getByRole('button', { name: 'Update', exact: true }).isEnabled()}`);
  await shot(page, 'ui-03-update-identical-files');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  record(`versions after identical files: ${await versionsOf(request, seed.token, seed.projectId, seed.configId)}`);
  await expect(page.locator('.version-row')).toHaveCount(1);

  dialog = await openUpdate(page, 'UC1.txt');
  await dialog.locator('input[type=file]').setInputFiles(asFiles({
    ...REQUIREMENTS, 'UC1.txt': 'A visitor performs a login, with a passphrase.\n',
  }));
  const modified = dialog.locator('.change-summary summary').filter({ hasText: 'Files modified' });
  await expect(modified).toBeVisible();
  record(`change summary before updating: ${(await dialog.locator('.change-summary summary').allTextContents()).map((t) => t.trim())}`);
  await shot(page, 'ui-03-update-cosmetic-edit-summary');
  await dialog.getByRole('button', { name: 'Update', exact: true }).click();
  await expect(dialog.getByText('version 2', { exact: true })).toBeVisible();
  record(`after updating: "${(await dialog.locator('.dialog-note').first().textContent()).trim()}"`);
  await shot(page, 'ui-03-update-cosmetic-edit-done');
  await dialog.getByRole('button', { name: 'Done' }).click();

  await expect(page.locator('.version-row .version-mark b')).toHaveText(['v2', 'v1']);
  record(`version history: ${await page.locator('.version-row .version-mark b').allTextContents()}`);
  await shot(page, 'ui-03-version-history-v2');
  await expect(modified).toHaveCount(0);
});

test('UI-07 GitHub connect dialog: connected, not connected, needs reconnect, token', async ({ page, request }) => {
  describeCase({
    id: 'UI-07',
    feature: 'Connect dialog / the three account states',
    priority: 'High',
    why: 'Connecting the code side to GitHub is how the demo fetches new commits. The dialog must offer what fits the account: the repository list when connected, a connect button when not, a reconnect button when the token died.',
    preconditions: 'A saved requirements -> code analysis. /github/connection and /github/repositories are answered by route mocks in the browser',
    input: 'Open Update on the code side, then Connect, three times: with the account connected (repository owner/library), not connected, and needing a reconnect. In the last, click "Use an access token instead"',
    expected: 'Connected: a Repository choice listing owner/library and no Access token field. Not connected: a "Connect GitHub" button and no repository field. Needs reconnect: "Reconnect GitHub". After "Use an access token instead": the Repository and Access token fields appear',
  });
  const seed = await seeded(page, request);
  let connection = { connected: true, login: 'octocat', configured: true, needs_reconnect: false };
  await page.route('**/github/connection', (route) => route.fulfill({ json: connection }));
  await page.route('**/github/repositories', (route) => route.fulfill({
    json: [{ full_name: 'owner/library', private: true, default_branch: 'main' }],
  }));
  await page.goto(analysisPage(seed));

  const openConnect = async () => {
    const dialog = await openUpdate(page, 'Auth.java');
    await dialog.getByRole('button', { name: 'Connect' }).click();
    const connect = page.getByRole('dialog', { name: 'Connect a source' });
    await expect(connect.getByText('Checking your GitHub account…')).toHaveCount(0);
    return connect;
  };
  const close = async (connect) => {
    await connect.getByRole('button', { name: 'Cancel' }).click();
    await updateDialog(page).getByRole('button', { name: 'Cancel' }).click();
  };

  // The fields by their own labels: 'access token' also appears in the link that reveals one.
  const tokenField = (dialog) => dialog.locator('input[name="tracerag-source-token"]');
  const repositoryField = (dialog) => dialog.locator('label', { hasText: /^Repository/ });
  let connect = await openConnect();
  await expect(connect.getByRole('combobox').filter({ hasText: 'Choose a repository…' })).toBeVisible();
  record(`connected: repositories offered ${await connect.locator('option[value="owner/library"]').allTextContents()}; `
    + `access token field: ${await tokenField(connect).count()}`);
  await expect(tokenField(connect)).toHaveCount(0);
  await shot(page, 'ui-07-connect-connected');
  await close(connect);

  connection = { connected: false, login: null, configured: true, needs_reconnect: false };
  connect = await openConnect();
  await expect(connect.getByRole('button', { name: 'Connect GitHub' })).toBeVisible();
  await expect(repositoryField(connect)).toHaveCount(0);
  record(`not connected: buttons ${(await connect.locator('button').allTextContents()).map((t) => t.trim()).filter(Boolean)}`);
  await shot(page, 'ui-07-connect-not-connected');
  await close(connect);

  connection = { connected: false, login: null, configured: true, needs_reconnect: true };
  connect = await openConnect();
  await expect(connect.getByRole('button', { name: 'Reconnect GitHub' })).toBeVisible();
  record(`needs reconnect: buttons ${(await connect.locator('button').allTextContents()).map((t) => t.trim()).filter(Boolean)}`);
  await shot(page, 'ui-07-connect-needs-reconnect');

  await connect.getByRole('button', { name: 'Use an access token instead' }).click();
  await expect(tokenField(connect)).toBeVisible();
  await expect(repositoryField(connect)).toBeVisible();
  record('after "Use an access token instead": Repository and Access token fields shown');
  await shot(page, 'ui-07-connect-access-token');
});

test('UI-09 Update: a classifier failure shows an error and makes no version', async ({ page, request }) => {
  describeCase({
    id: 'UI-09',
    feature: 'Update dialog / the run fails',
    priority: 'High',
    why: 'Groq can stop answering in the middle of an update. The user must be told, and the analysis must stay as it was.',
    preconditions: 'A saved requirements -> code analysis at v1. The test server\'s fake classifier fails on any requirement containing CLASSIFIER-OUTAGE',
    input: 'Update the requirements side with UC1.txt changed to a text containing CLASSIFIER-OUTAGE, and press Update',
    expected: 'The dialog shows the error "The classifier stopped answering." as an alert; after closing it the version history still lists only v1',
  });
  const seed = await seeded(page, request);
  await page.goto(analysisPage(seed));

  const dialog = await openUpdate(page, 'UC1.txt');
  await dialog.locator('input[type=file]').setInputFiles(asFiles({
    ...REQUIREMENTS, 'UC1.txt': 'A visitor performs a login. CLASSIFIER-OUTAGE\n',
  }));
  await expect(dialog.locator('.change-summary')).toBeVisible();
  await dialog.getByRole('button', { name: 'Update', exact: true }).click();
  const alert = dialog.getByRole('alert');
  await expect(alert).toHaveText('The classifier stopped answering.');
  record(`error shown: "${await alert.textContent()}"`);
  await shot(page, 'ui-09-update-classifier-failed');
  await dialog.getByRole('button', { name: 'Done' }).click();

  await expect(page.locator('.version-row .version-mark b')).toHaveText(['v1']);
  const versions = await versionsOf(request, seed.token, seed.projectId, seed.configId);
  record(`versions afterwards: ${versions}`);
  await shot(page, 'ui-09-version-history-unchanged');
  expect(versions).toEqual([1]);
});
