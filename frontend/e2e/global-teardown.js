import { execSync } from 'node:child_process';

/** Drop the UI tests' database and folders once every test has run. */
export default function globalTeardown() {
  const python = process.env.E2E_PYTHON ?? 'python';
  execSync(`"${python}" ../backend/tests/e2e_server.py --drop`, { stdio: 'inherit' });
}
