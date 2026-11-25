import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const stateDir = process.env.VWB_E2E_STATE_DIR ?? resolve("../.artifacts", `e2e-${Date.now()}`);
process.env.VWB_E2E_STATE_DIR = stateDir;

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 60000,
  use: { baseURL: "http://127.0.0.1:8877", viewport: { width: 1440, height: 1000 } },
  webServer: {
    command: `../.venv/bin/vwb --port 8877 --state-dir ${JSON.stringify(stateDir)}`,
    url: "http://127.0.0.1:8877/api/demo",
    reuseExistingServer: false,
  },
});
