import { expect, test } from "@playwright/test";
import { request as httpRequest } from "node:http";

const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" }; // gitleaks:allow openbare e2e-testfixture, zelfde aanmelding als werk-apikeys-ontwerpreview.spec.ts; geen echt account.

test("connector en minimale healthroutes zijn bereikbaar via de ene webpoort", async ({ request }) => {
  const setup = await (await request.get("/api/v1/auth/setup-status")).json();
  if (setup.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
  const login = await request.post("/api/v1/auth/login", { data: ADMIN });
  expect(login.status()).toBe(200);
  const me = await (await request.get("/api/v1/auth/me")).json();
  const made = await request.post("/api/v1/auth/api-keys", {
    data: { name: "connector-e2e", role: "viewer", scopes: ["emergency_stop:on"] },
    headers: { "X-CSRF-Token": me.csrf_token },
  });
  expect(made.status()).toBe(200);
  const apiKey = (await made.json()).key as string;
  // Losse machineaanroep: geen sessie, en interne headers mogen geen toegang geven.
  const anon = { Cookie: "", "X-MediaGuard-Token": "ongeldig" };
  const denied = await request.get("/connector/v1/discover", { headers: anon });
  expect(denied.status()).toBe(401);
  expect(await denied.json()).toMatchObject({ code: "unauthorized", details: { reason: "unauthorized" } });
  const machine = { Cookie: "", "X-Api-Key": apiKey };
  const discover = await request.get("/connector/v1/discover", { headers: machine });
  expect(discover.status()).toBe(200);
  expect(await discover.json()).toMatchObject({ capabilities: ["read"], actions: [], item_types: [] });
  const health = await request.get("/connector/v1/health", { headers: machine });
  expect(health.status()).toBe(200);
  expect((await health.json()).checks.length).toBeGreaterThan(0);
  const blocked = await request.post("/connector/v1/apply", { headers: machine });
  expect(blocked.status()).toBe(403);
  expect(await blocked.json()).toMatchObject({ code: "scope_ontbreekt", details: { reason: "scope_ontbreekt" } });
  for (const url of ["/healthz", "/readyz"]) {
    const response = await request.get(url, { headers: anon });
    expect(response.status()).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
  }
  // Bestaande route en foutvorm blijven bereikbaar via dezelfde proxy.
  const legacy = await request.get("/api/v1/status", { headers: anon });
  expect(legacy.status()).toBe(401);
  expect(await legacy.json()).toEqual({ detail: "Niet ingelogd" });
});

test("de webdoorgifte bewaart 413 vóór authenticatie, ook bij chunked invoer", async ({ request, baseURL }) => {
  const payload = Buffer.alloc(1048577, "x");
  for (const path of ["/connector/v1/health", "/connector/v1/onbekend", "/healthz", "/readyz"]) {
    const response = await request.post(path, { data: payload, headers: { Cookie: "" } });
    expect(response.status()).toBe(413);
    expect(await response.json()).toMatchObject({ code: "payload_too_large", details: { max_bytes: 1048576 } });
  }
  // Precies de grens mag door naar de bestaande authenticatiecontrole.
  const boundary = await request.post("/connector/v1/apply", {
    data: payload.subarray(0, 1048576), headers: { Cookie: "" },
  });
  expect(boundary.status()).toBe(401);
  expect(await boundary.json()).toMatchObject({ code: "unauthorized" });
  const chunked = await new Promise<{ status: number | undefined; body: string }>((resolve, reject) => {
    const req = httpRequest(new URL("/connector/v1/onbekend", baseURL), {
      method: "POST", headers: { "Transfer-Encoding": "chunked" },
    }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk: string) => { body += chunk; });
      res.on("end", () => resolve({ status: res.statusCode, body }));
      res.on("error", reject);
    });
    req.on("error", reject);
    req.write(payload.subarray(0, 600000));
    req.end(payload.subarray(600000));
  });
  expect(chunked.status).toBe(413);
  expect(JSON.parse(chunked.body)).toMatchObject({ code: "payload_too_large", details: { max_bytes: 1048576 } });
});
