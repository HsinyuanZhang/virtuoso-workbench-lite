import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:8877", viewport: { width: 1440, height: 1000 } },
  webServer: {
    command: "../.venv/bin/vwb --port 8877 --state-dir ../.artifacts/e2e-handoffs",
    url: "http://127.0.0.1:8877/api/demo",
    reuseExistingServer: false,
  },
});
