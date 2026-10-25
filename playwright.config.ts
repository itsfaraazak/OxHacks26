import { defineConfig } from "@playwright/test";

// The tests drive a real backend on :5000 (one shared session), so they run one at a time.
// Both servers are started unless already running; the backend needs backend/.venv (see README).
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  fullyParallel: false,
  use: { baseURL: "http://localhost:3000" },
  webServer: [
    {
      command: ".venv/bin/python -m fido --host 127.0.0.1 --port 5000",
      cwd: "backend",
      url: "http://localhost:5000/api/health",
      reuseExistingServer: true,
    },
    {
      command: "npm run dev",
      url: "http://localhost:3000",
      reuseExistingServer: !process.env.CI,
    },
  ],
});
