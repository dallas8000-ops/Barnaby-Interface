import { defineConfig, devices } from "@playwright/test";

// E2E runs the real engine process in --mock mode (scripted agent, fake audio)
// against the production UI build. No mic, no models, no API spend.
const TOKEN = "e2e-token-0123456789abcdefghijklmnopqrstuv";
const ENGINE_PORT = 8765;
const PY = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const DATA_DIR = process.env.BARNABY_E2E_DATA ?? "./.e2e-data";

export const ENGINE_QUERY = `?engine=ws://127.0.0.1:${ENGINE_PORT}&token=${TOKEN}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1, // one engine, one client slot
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:1420",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    launchOptions: process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
  },
  webServer: [
    {
      command: `${PY} -m barnaby_engine --mock --port ${ENGINE_PORT}`,
      cwd: "../engine",
      env: { BARNABY_TOKEN: TOKEN, BARNABY_DATA_DIR: DATA_DIR },
      port: ENGINE_PORT,
      reuseExistingServer: false,
      timeout: 20_000,
    },
    {
      command: "npx vite build && npx vite preview --port 1420 --strictPort --host 127.0.0.1",
      port: 1420,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
