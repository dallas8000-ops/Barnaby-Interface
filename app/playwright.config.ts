import { defineConfig, devices } from "@playwright/test";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// E2E runs the real engine process in --mock mode (scripted agent, fake audio)
// against the production UI build. No mic, no models, no API spend.
const TOKEN = "e2e-token-0123456789abcdefghijklmnopqrstuv";
const ENGINE_PORT = 8765;
const PY = process.env.PYTHON ?? (process.platform === "win32" ? "python" : "python3");
// A fresh data dir per run: sessions and learned voice corrections persisted in a
// fixed "./.e2e-data" (which, with cwd ../engine, was engine/.e2e-data) and leaked
// between runs; e.g. a learned "tests -> specs" rewrote the next run's transcript.
const DATA_DIR = process.env.BARNABY_E2E_DATA ?? mkdtempSync(join(tmpdir(), "barnaby-e2e-"));

export const ENGINE_QUERY = `?engine=ws://127.0.0.1:${ENGINE_PORT}&token=${TOKEN}`;
// Tablet access on, on a fixed port, so the tablet flow can pair against the real TLS server.
export const TABLET_PORT = 18766;
// A small project with folders, so background tasks have lanes to pick.
const PROJECT = join(DATA_DIR, "project");
for (const d of ["api", "app", "engine"]) mkdirSync(join(PROJECT, d), { recursive: true });
writeFileSync(
  join(DATA_DIR, "config.json"),
  JSON.stringify({ tablet_access: true, tablet_port: TABLET_PORT, workspace: PROJECT, max_parallel_tasks: 3 }),
);

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
    launchOptions: {
      ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}),
      // The tablet flow records with a synthetic microphone (a steady beep), permission pre-granted.
      args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
    },
  },
  webServer: [
    {
      command: `${PY} -m barnaby_engine --mock --port ${ENGINE_PORT}`,
      cwd: "../engine",
      env: { BARNABY_TOKEN: TOKEN, BARNABY_DATA_DIR: DATA_DIR },
      // HTTP readiness check: the engine answers plain GETs with 200. A bare `port`
      // probe opens and drops a TCP connection, which websockets logs as a scary
      // (harmless) "opening handshake failed" traceback on every run.
      url: `http://127.0.0.1:${ENGINE_PORT}/`,
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
