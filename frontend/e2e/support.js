/**
 * What the UI tests share: seeding through the API, signing a page in, saving
 * screenshots, and describing each test for the report.
 *
 * Users, projects and analyses are made through the API, never through the
 * UI - only the behaviour a test is about is clicked through.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const DATA = path.resolve(HERE, '../../backend/tests/data');
export const SCREENSHOTS = path.join(HERE, 'screenshots');
export const API = process.env.E2E_API_URL;

/** The corpus files under backend/tests/data/<folder>, as {name: text}. */
export function filesIn(folder) {
  const directory = path.join(DATA, folder);
  return Object.fromEntries(fs.readdirSync(directory).sort().map((name) => [
    name, fs.readFileSync(path.join(directory, name), 'utf-8'),
  ]));
}

export const REQUIREMENTS = filesIn('req');
export const CODE = filesIn('code');
export const MODEL = { 'model.uml': fs.readFileSync(path.join(DATA, 'model.uml'), 'utf-8') };

// Whole documents against methods, as the Python integration tests run.
export const SETTINGS = {
  source_preprocessor: 'single',
  target_preprocessor: 'method',
  source_output_level: 'artifact',
  target_output_level: 'function',
  classifier: 'reasoning',
  n_results: 10,
  dependency_expansion_depth: 1,
  summarize_elements: false,
};
export const SENTENCES = { source_preprocessor: 'sentence', source_output_level: 'sentence', dependency_expansion_depth: 0 };
export const UML = { target_preprocessor: 'model_uml', target_output_level: 'component', dependency_expansion_depth: 0 };

/** Files as setInputFiles takes them, from {name: text}. */
export const asFiles = (files) => Object.entries(files).map(([name, text]) => ({
  name, mimeType: 'text/plain', buffer: Buffer.from(text, 'utf-8'),
}));

/**
 * Describe a test for the report, in the same fields as the Python cases.
 * `record` writes down what the test actually saw.
 */
export function describeCase(fields) {
  test.info().annotations.push({ type: 'case', description: JSON.stringify(fields) });
}

export function record(value) {
  test.info().annotations.push({ type: 'actual', description: String(value) });
}

/** A full-page screenshot, kept for the report. */
export async function shot(page, name) {
  const file = path.join(SCREENSHOTS, `${name}.png`);
  // From the top, or the fixed header is drawn wherever the page was scrolled to.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: file, fullPage: true });
  test.info().annotations.push({ type: 'screenshot', description: `e2e/screenshots/${name}.png` });
}

// ----- The API, for seeding -----

const authorised = (token) => ({ Authorization: `Bearer ${token}` });

async function ok(response) {
  expect(response.ok(), `${response.url()}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.status() === 204 ? null : response.json();
}

export async function signUp(request) {
  const email = `ui-${Date.now()}-${Math.random().toString(16).slice(2, 8)}@example.com`;
  const body = await ok(await request.post(`${API}/auth/register`, {
    data: { full_name: 'UI Tester', email, password: 'a-test-password' },
  }));
  return body.access_token;
}

export async function newProject(request, token, name = 'UI project') {
  const body = await ok(await request.post(`${API}/projects`, {
    headers: authorised(token), data: { project_name: `${name} ${Date.now() % 100000}` },
  }));
  return body.project_id;
}

/** Wait for a background job to finish, and return it. */
async function finished(request, token, jobId) {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const job = await ok(await request.get(`${API}/jobs/${jobId}`, { headers: authorised(token) }));
    if (job.state === 'succeeded' || job.state === 'failed') return job;
    await new Promise((resolve) => { setTimeout(resolve, 250); });
  }
  throw new Error(`job ${jobId} did not finish`);
}

/**
 * A New Analysis through the API: upload, run, save. `source` and `target`
 * are [kind, name, {path: text}]. Returns the saved analysis.
 */
export async function analyse(request, token, projectId, {
  source = ['requirements', 'reqs', REQUIREMENTS], target = ['code', 'code', CODE], settings = {},
} = {}) {
  const all = { ...SETTINGS, ...settings };
  const form = new FormData();
  const artifacts = [];
  const paths = [];
  let index = 0;
  for (const [id, [kind, name, files]] of [['s', source], ['t', target]]) {
    const indexes = [];
    for (const [file, text] of Object.entries(files)) {
      form.append('files', new Blob([text], { type: 'text/plain' }), file);
      paths.push(file);
      indexes.push(index);
      index += 1;
    }
    artifacts.push({ id, name, kind, file_indexes: indexes });
  }
  form.append('artifacts', JSON.stringify(artifacts));
  form.append('source_artifact_ids', '["s"]');
  form.append('target_artifact_ids', '["t"]');
  form.append('file_paths', JSON.stringify(paths));
  form.append('analysis_mode', 'project');
  form.append('project_id', String(projectId));
  for (const [key, value] of Object.entries(all)) form.append(key, String(value));

  const started = await ok(await request.post(`${API}/analyze/upload`, {
    headers: authorised(token), multipart: form,
  }));
  const job = await finished(request, token, started.job_id);
  expect(job.state, job.error).toBe('succeeded');
  return ok(await request.post(`${API}/projects/${projectId}/analyses`, {
    headers: authorised(token),
    data: { config: all, result: job.result, upload_id: job.result.upload_id, execution_duration: 1.0 },
  }));
}

/** Give one side new files and update the analysis with them, through the API. */
export async function updateSide(request, token, projectId, configId, role, files) {
  const base = `${API}/projects/${projectId}/configs/${configId}`;
  const sides = await ok(await request.get(`${base}/sources`, { headers: authorised(token) }));
  const side = sides.find((item) => item.role === role);
  const form = new FormData();
  for (const [file, text] of Object.entries(files)) {
    form.append('files', new Blob([text], { type: 'text/plain' }), file);
  }
  form.append('file_paths', JSON.stringify(Object.keys(files)));
  const staged = await ok(await request.post(`${base}/sources/${side.source_id}/files`, {
    headers: authorised(token), multipart: form,
  }));
  const started = await ok(await request.post(`${base}/sync`, {
    headers: authorised(token), data: { source_id: side.source_id, upload_id: staged.upload_id },
  }));
  return finished(request, token, started.job_id);
}

export async function versionsOf(request, token, projectId, configId) {
  const rows = await ok(await request.get(
    `${API}/projects/${projectId}/configs/${configId}/versions`, { headers: authorised(token) },
  ));
  return rows.map((row) => row.version_number);
}

/** Sign a page in as the holder of `token`, before anything on it loads. */
export async function signIn(page, token) {
  await page.addInitScript((value) => localStorage.setItem('tracerag-token', value), token);
}

/** A whole user with a project and one saved analysis, signed in on `page`. */
export async function seeded(page, request, options) {
  const token = await signUp(request);
  const projectId = await newProject(request, token);
  const saved = await analyse(request, token, projectId, options);
  await signIn(page, token);
  return { token, projectId, configId: saved.config_id, saved };
}

export const analysisPage = ({ projectId, configId }) => `/app/analyses/${configId}?project=${projectId}`;
export const linksPage = ({ projectId, configId }) => `/app/links?project=${projectId}&config=${configId}`;

/** One summary tile's number on the Trace Links page. */
export async function tile(page, label) {
  const box = page.locator('.result-stats > div').filter({
    has: page.locator('.result-stat-label').getByText(label, { exact: true }),
  });
  return Number(await box.locator('b').textContent());
}

export { expect, test };
