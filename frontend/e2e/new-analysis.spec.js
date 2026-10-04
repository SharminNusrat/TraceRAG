import path from 'node:path';
import {
  DATA, describeCase, expect, newProject, record, shot, signIn, signUp, test,
} from './support';

test('UI-01 New Analysis: upload, run with progress, save as v1', async ({ page, request }) => {
  describeCase({
    id: 'UI-01',
    feature: 'New Analysis / upload, run, save',
    priority: 'Critical',
    why: 'The first thing shown in the demo. If choosing files, running or saving fails in the browser, nothing after it can be shown.',
    preconditions: 'A signed-in user with one empty project (made through the API)',
    input: 'On New Analysis, pick UC1-UC4.txt, Auth.java and Loans.java from backend/tests/data with "Select files", go through Settings to Review, Run analysis; on the results page, Save results into the project',
    expected: 'The files become two artifacts, one per side, and Next is enabled. While running, the progress bar is shown. The results page appears, saving says "Saved to <project>", and the project lists the analysis as v1',
  });
  const token = await signUp(request);
  const projectId = await newProject(request, token, 'Demo');
  await signIn(page, token);
  // Hold each job poll back a little, so the run is still going when it is looked at.
  await page.route('**/jobs/**', async (route) => {
    await new Promise((resolve) => { setTimeout(resolve, 1200); });
    await route.continue();
  });

  await page.goto('/analysis');
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Select files' }).click();
  await (await chooser).setFiles([
    ...['UC1.txt', 'UC2.txt', 'UC3.txt', 'UC4.txt'].map((name) => path.join(DATA, 'req', name)),
    ...['Auth.java', 'Loans.java'].map((name) => path.join(DATA, 'code', name)),
  ]);
  const sides = await page.locator('.file-row select.side-select').evaluateAll(
    (selects) => selects.map((select) => select.value),
  );
  record(`artifacts: ${await page.locator('.file-row b').allTextContents()}; sides: ${sides}`);
  await shot(page, 'ui-01-new-analysis-files-chosen');
  await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();

  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByRole('button', { name: 'Next' }).click();
  await shot(page, 'ui-01-new-analysis-review');
  await page.getByRole('button', { name: 'Run analysis' }).click();
  await expect(page.getByRole('progressbar', { name: 'Analysis in progress' })).toBeVisible();
  record('progress bar visible while running: true');
  await shot(page, 'ui-01-new-analysis-running');

  await page.waitForURL('**/results');
  await expect(page.getByRole('button', { name: 'Save results' })).toBeVisible();
  await shot(page, 'ui-01-results');
  await page.getByRole('button', { name: 'Save results' }).click();
  const dialog = page.getByRole('dialog', { name: 'Save analysis' });
  await dialog.getByRole('button', { name: 'Save analysis' }).click();
  const saved = page.getByRole('button', { name: /^Saved to / });
  await expect(saved).toBeVisible();
  record(`after saving: "${await saved.textContent()}"`);

  await page.goto(`/app/history?project=${projectId}`);
  const row = page.locator('.analysis-row').first();
  await expect(row.locator('.analysis-version')).toHaveText('v1');
  record(`project lists: "${(await row.locator('b').textContent()).trim()}" - ${await row.locator('small').textContent()}`);
  await shot(page, 'ui-01-project-lists-v1');

  expect(sides).toEqual(['source', 'target']);
  await expect(page.locator('.analysis-row')).toHaveCount(1);
});
