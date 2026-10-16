import { execSync } from 'child_process';
import * as path from 'path';

/** Mint JWT into e2e/auth.json for Playwright (mirrors API smoke tests). */
export default async function globalSetup() {
  const backendPython = path.join(__dirname, '..', 'backend', 'venv', 'bin', 'python');
  const script = path.join(__dirname, 'scripts', 'generate-auth-state.py');
  execSync(`"${backendPython}" "${script}"`, {
    stdio: 'inherit',
    env: process.env,
  });
}
