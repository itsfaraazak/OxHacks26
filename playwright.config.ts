import { defineConfig } from "@playwright/test";

const backendPort = process.env.FIDO_TEST_BACKEND_PORT ?? "5000";
const frontendPort = process.env.FIDO_TEST_FRONTEND_PORT ?? "3000";
const backendPython = process.platform === "win32" ? ".venv\\Scripts\\python.exe" : ".venv/bin/python";

// The tests drive a real backend on :5000 (one shared session), so they run one at a time.
// Both servers are started unless already running; the backend needs backend/.venv (see README).
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  fullyParallel: false,
  use: { baseURL: `http://localhost:${frontendPort}` },
  webServer: [
    {
      command: `"${backendPython}" -m fido --host 127.0.0.1 --port ${backendPort}`,
      cwd: "backend",
      url: `http://localhost:${backendPort}/api/health`,
      reuseExistingServer: true,
    },
    {
      command: "npm run dev",
      env: { NEXT_PUBLIC_FIDO_API: `http://localhost:${backendPort}`, PORT: frontendPort },
      url: `http://localhost:${frontendPort}`,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
