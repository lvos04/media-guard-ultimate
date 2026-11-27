import {
  copyFileSync, cpSync, existsSync, readFileSync, rmSync, symlinkSync, writeFileSync,
} from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";
import ts from "typescript";
import { actieJs, actieJson, jsPrefix, prerender, previewProps, runtimeMap, sjabloon } from "../webmanifesten.mjs";
import { bestanden, controleerWebpakket } from "./controleer-webpakket.mjs";

// Ook een import onder een andere naam of een actie in een geneste functie telt mee.
export function controleerWebbron(web) {
  const previewApi = new Set(["draftMode", "setDraftMode", "setPreviewData", "clearPreviewData", "previewData"]);
  const overslaan = new Set([".git", ".next", "node_modules", "scripts", "tests", "e2e", "playwright-report", "test-results"]);
  for (const bestand of bestanden(web, overslaan)) {
    if (!/\.[cm]?[jt]sx?$/.test(bestand)) continue;
    const bron = ts.createSourceFile(bestand, readFileSync(bestand, "utf8"), ts.ScriptTarget.Latest);
    function bezoek(knoop) {
      if ((ts.isIdentifier(knoop) || ts.isStringLiteralLike(knoop)) && previewApi.has(knoop.text)) {
        throw new Error("De web-app gebruikt de preview-API van Next.js");
      }
      if (ts.isExpressionStatement(knoop) && knoop.expression.text === "use server") {
        throw new Error("De web-app gebruikt een Server Action");
      }
      ts.forEachChild(knoop, bezoek);
    }
    bezoek(bron);
  }
}

// Lees de waarden van déze build. Controleer het hele te kopiëren pakket, inclusief statische bundels.
export function verwijderBuildsleutels(pakket, runtime = runtimeMap) {
  const nextMap = path.join(pakket, ".next");
  const lees = (naam) => readFileSync(path.join(nextMap, naam), "utf8");
  const routes = JSON.parse(lees(prerender));
  const acties = JSON.parse(lees(actieJson));
  const js = lees(actieJs);
  if (Object.keys(acties.node).length || Object.keys(acties.edge).length) {
    throw new Error("Het productiemanifest bevat een Server Action");
  }
  const preview = routes.preview;
  const waarden = [preview.previewModeId, preview.previewModeSigningKey, preview.previewModeEncryptionKey, acties.encryptionKey];
  if (waarden.some((waarde) => typeof waarde !== "string" || waarde.length < 16)
      || !isDeepStrictEqual(Object.keys(preview).sort(), ["previewModeEncryptionKey", "previewModeId", "previewModeSigningKey"])
      || !isDeepStrictEqual(Object.keys(acties).sort(), ["edge", "encryptionKey", "node"])
      || !js.startsWith(jsPrefix) || !isDeepStrictEqual(JSON.parse(JSON.parse(js.slice(jsPrefix.length))), acties)
      || (existsSync(path.join(nextMap, previewProps)) && !isDeepStrictEqual(JSON.parse(lees(previewProps)), preview))) {
    throw new Error("Onverwachte indeling van de Next.js-manifesten");
  }
  delete routes.preview;
  writeFileSync(path.join(nextMap, sjabloon), JSON.stringify(routes));
  for (const naam of [prerender, previewProps, actieJson, actieJs]) {
    const bestand = path.join(nextMap, naam);
    rmSync(bestand, { force: true });
    symlinkSync(path.join(runtime, path.basename(naam)), bestand);
  }
  controleerWebpakket(pakket, waarden);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  controleerWebbron(".");
  const pakket = ".next/standalone";
  for (const naam of ["server.mjs", "webmanifesten.mjs"]) copyFileSync(naam, path.join(pakket, naam));
  for (const map of ["public", ".next/static"]) cpSync(map, path.join(pakket, map), { recursive: true });
  // De web-app gebruikt geen native beeldoptimalisatie.
  for (const map of ["node_modules/@img", "node_modules/sharp", ".next/cache"]) {
    rmSync(path.join(pakket, map), { recursive: true, force: true });
  }
  verwijderBuildsleutels(pakket);
  console.log("Webpakket gecontroleerd: geen vaste Next.js-buildsleutels.");
}
