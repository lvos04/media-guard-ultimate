import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * E2E against a REAL MediaGuard core (no mocks):
 *   core: apps/core, fresh MG_DATA_DIR, MG_START_BACKGROUND=0, port MG_E2E_CORE_PORT (default 8190)
 *   web:  node server.mjs (production build), port MG_E2E_WEB_PORT (default 3200)
 * Run: npm run build && npm run e2e
 */
const corePort = Number(process.env.MG_E2E_CORE_PORT ?? 8191);
const webPort = Number(process.env.MG_E2E_WEB_PORT ?? 3200);
const python = process.env.MG_E2E_PYTHON ?? "/tmp/corevenv/bin/python";
// fresh core data dir per run (wiped by the webServer command); tests seed quarantine fixtures here
const dataDir = path.resolve(__dirname, "e2e/.data");
process.env.MG_E2E_DATA_DIR = dataDir;
process.env.MG_E2E_PYTHON = python;

export default defineConfig({
  testDir: "./e2e",
  // design-shots.spec.ts maakt alleen ontwerp-schermafdrukken (MG_SHOTS_DIR=<map>). Buiten die modus hoort hij niet in
  // de run: een gewone run heeft dan 0 overgeslagen tests en elke overslag valt op (ADR-0020 punt 4).
  testIgnore: process.env.MG_SHOTS_DIR ? [] : ["**/design-shots.spec.ts"],
  fullyParallel: false,
  workers: 1,
  // Op de CI-runner (2 kernen, core + web + browser tegelijk) falen soms losse tests op
  // trage pagina's; twee herkansingen. Een test die steeds faalt blokkeert nog steeds de release.
  retries: process.env.CI ? 2 : 0,
  timeout: 45_000,
  reporter: [["list"]],
  outputDir: "./e2e/.results",
  use: { baseURL: `http://localhost:${webPort}`, trace: process.env.CI ? "on-first-retry" : "retain-on-failure", locale: "nl-NL" },
  projects: [
    { name: "desktop-1440", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      command: `sh -c 'rm -rf ${dataDir} && mkdir -p ${dataDir} && cd ../core && MG_DATA_DIR=${dataDir} MG_PORT=${corePort} MG_START_BACKGROUND=0 ${python} -m mediaguard'`,
      url: `http://localhost:${corePort}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `PORT=${webPort} MG_CORE_URL=http://localhost:${corePort} NODE_ENV=production node server.mjs`,
      url: `http://localhost:${webPort}/login`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
