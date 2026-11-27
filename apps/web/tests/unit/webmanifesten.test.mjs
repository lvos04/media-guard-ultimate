import { randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, expect, test } from "vitest";
import { controleerWebpakket } from "../../scripts/controleer-webpakket.mjs";
import { controleerWebbron, verwijderBuildsleutels } from "../../scripts/verpak-web.mjs";
import { actieJs, actieJson, installatiesleutel, jsPrefix, prerender, previewProps, schrijfManifesten } from "../../webmanifesten.mjs";

let werk, nextMap, runtime, opslag, preview, acties;
const schrijf = (naam, data) => writeFileSync(path.join(nextMap, naam), data);
const lees = (naam) => readFileSync(path.join(nextMap, naam), "utf8");

function previewWaarden() {
  return {
    previewModeId: randomBytes(16).toString("hex"),
    previewModeSigningKey: randomBytes(32).toString("hex"),
    previewModeEncryptionKey: randomBytes(32).toString("hex"),
  };
}

beforeEach(() => {
  werk = mkdtempSync(path.join(tmpdir(), "mg-webtest-"));
  nextMap = path.join(werk, ".next");
  runtime = path.join(werk, "runtime");
  opslag = path.join(werk, "appdata");
  mkdirSync(path.join(nextMap, "server"), { recursive: true });
  preview = previewWaarden();
  acties = { node: {}, edge: {}, encryptionKey: randomBytes(32).toString("base64") };
  schrijf(prerender, JSON.stringify({ version: 4, routes: { "/login": {} }, preview }));
  schrijf(previewProps, JSON.stringify(preview));
  schrijf(actieJson, JSON.stringify(acties));
  schrijf(actieJs, jsPrefix + JSON.stringify(JSON.stringify(acties)));
});
afterEach(() => rmSync(werk, { recursive: true, force: true }));

test("de eerste start, herstart en tweede installatie houden routes en sleutels uit elkaar", () => {
  verwijderBuildsleutels(werk, runtime);
  expect(existsSync(path.join(nextMap, prerender))).toBe(false);
  const start = (map) => {
    const key = installatiesleutel(map);
    const env = schrijfManifesten(nextMap, runtime, key);
    const routes = JSON.parse(lees(prerender));
    const referenties = JSON.parse(lees(actieJson));
    expect(routes.routes).toEqual({ "/login": {} });
    expect(JSON.stringify(routes.preview) === lees(previewProps)).toBe(true);
    expect(referenties.encryptionKey === env && env === key.toString("base64")).toBe(true);
    expect(JSON.parse(lees(actieJs).slice(jsPrefix.length)) === lees(actieJson)).toBe(true);
    expect(statSync(path.join(map, "server-actions.key")).mode & 0o777).toBe(0o600);
    for (const naam of [prerender, previewProps, actieJson, actieJs]) {
      expect(statSync(path.join(nextMap, naam)).mode & 0o777).toBe(0o600);
    }
    return [...Object.values(routes.preview), env];
  };
  const eerste = start(opslag);
  const herstart = start(opslag);
  const tweede = start(path.join(werk, "andere-installatie"));
  const buildwaarden = [...Object.values(preview), acties.encryptionKey];
  expect(new Set(eerste).size).toBe(4);
  expect(eerste.every((waarde, i) => waarde === herstart[i] && !tweede.includes(waarde) && !buildwaarden.includes(waarde))).toBe(true);
});

test("een restkopie buiten de bekende manifesten blokkeert de build", () => {
  schrijf("ander-bestand.js", acties.encryptionKey);
  expect(() => verwijderBuildsleutels(werk, runtime)).toThrow(/vaste buildsleutel/);
});

test.each(["preview", "acties", "acties-js"])("een hernoemd %s-manifest uit een andere build blokkeert de verpakking", (soort) => {
  const oud = { node: {}, edge: {}, encryptionKey: randomBytes(32).toString("base64") };
  const inhoud = {
    preview: JSON.stringify(previewWaarden()),
    acties: JSON.stringify(oud, null, 2),
    "acties-js": jsPrefix + JSON.stringify(JSON.stringify(oud)),
  };
  mkdirSync(path.join(werk, "public", "archief"), { recursive: true });
  writeFileSync(path.join(werk, "public", "archief", "vorige-build.txt"), inhoud[soort]);
  expect(() => verwijderBuildsleutels(werk, runtime)).toThrow(/vaste buildsleutel/);
});

test("de poort voor een aangeleverd pakket herkent een latere toevoeging buiten .next", () => {
  verwijderBuildsleutels(werk, runtime);
  expect(() => controleerWebpakket(werk)).not.toThrow();
  const pad = path.join(werk, "oude-kopie.txt");
  writeFileSync(pad, jsPrefix + JSON.stringify(JSON.stringify({
    node: {}, edge: {}, encryptionKey: randomBytes(32).toString("base64"),
  })));
  expect(() => controleerWebpakket(werk)).toThrow(/vaste buildsleutel/);
});

test.each(["node", "edge"])("een geregistreerde Server Action voor %s blokkeert de build", (type) => {
  acties[type].voorbeeld = {};
  schrijf(actieJson, JSON.stringify(acties));
  expect(() => verwijderBuildsleutels(werk, runtime)).toThrow(/Server Action/);
});

test.each(["import { draftMode as concept } from 'next/headers'", "res.setPreviewData({})", "'use server'"])(
  "de broncontrole herkent de preview- en actie-API", (bron) => {
    mkdirSync(path.join(werk, "app"));
    writeFileSync(path.join(werk, "app", "voorbeeld.ts"), bron);
    expect(() => controleerWebbron(werk)).toThrow(/preview-API|Server Action/);
  },
);

test("een onleesbare of te ruim gedeelde installatiesleutel wordt nooit vervangen", () => {
  const key = installatiesleutel(opslag);
  const bestand = path.join(opslag, "server-actions.key");
  chmodSync(bestand, 0o644);
  expect(() => installatiesleutel(opslag)).toThrow(/0600/);
  expect(readFileSync(bestand).equals(key)).toBe(true);
  chmodSync(bestand, 0o600);
  writeFileSync(bestand, Buffer.alloc(0));
  expect(() => installatiesleutel(opslag)).toThrow(/32 bytes/);
  expect(statSync(bestand).size).toBe(0);
});
