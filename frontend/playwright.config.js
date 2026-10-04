import { execSync } from 'node:child_process';
import { defineConfig, devices } from '@playwright/test';

// A free port, asked of the operating system. Chosen once by the main process
// and passed to the workers through the environment, which they inherit.
const freePort = () => execSync(
  'node -e "const s=require(\'net\').createServer();s.listen(0,()=>{console.log(s.address().port);s.close()})"',
).toString().trim();

process.env.E2E_API_PORT ??= freePort();
process.env.E2E_WEB_PORT ??= freePort();
process.env.E2E_API_URL = `http://127.0.0.1:${process.env.E2E_API_PORT}`;
const web = `http://localhost:${process.env.E2E_WEB_PORT}`;

// The backend's Python, with its dependencies installed.
const python = process.env.E2E_PYTHON ?? 'python';

export default defineConfig({
  testDir: './e2e',
  // One backend and one database for every test, so one test at a time.
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['json', { outputFile: 'e2e/results/results.json' }]],
  globalTeardown: './e2e/global-teardown.js',
  use: {
    baseURL: web,
    acceptDownloads: true,
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `"${python}" ../backend/tests/e2e_server.py ${process.env.E2E_API_PORT} ${web}`,
      url: `${process.env.E2E_API_URL}/health`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `npx vite --port ${process.env.E2E_WEB_PORT} --strictPort`,
      url: web,
      env: { VITE_API_URL: process.env.E2E_API_URL },
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
