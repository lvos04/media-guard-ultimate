import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

export function* bestanden(map, overslaan = new Set()) {
  for (const item of readdirSync(map, { withFileTypes: true })) {
    if (overslaan.has(item.name)) continue;
    const bestand = path.join(map, item.name);
    if (item.isDirectory()) yield* bestanden(bestand);
    else if (item.isFile()) yield bestand;
  }
}

// Next schrijft JSON en neemt dezelfde JSON als gequote tekenreeks op in JavaScript.
// Herken ook waarden van een andere build, onafhankelijk van de bestandsnaam.
const vasteManifestwaarde = /\b(?:previewMode(?:Id|SigningKey|EncryptionKey)|encryptionKey)\\*["']?\s*:\s*\\*["'][A-Za-z0-9+/_=-]{22,}\\*["']/;

export function controleerWebpakket(pakket, bouwWaarden = []) {
  for (const bestand of bestanden(pakket)) {
    const inhoud = readFileSync(bestand);
    if (bouwWaarden.some((waarde) => inhoud.includes(waarde)) || vasteManifestwaarde.test(inhoud.toString("latin1"))) {
      throw new Error("Het webpakket bevat nog een vaste buildsleutel");
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const pakket = path.resolve(process.argv[2]);
  if (existsSync(path.join(pakket, ".gitleaks.toml"))) throw new Error("Het webpakket bevat een eigen scanconfiguratie");
  controleerWebpakket(pakket);
  execFileSync("gitleaks", ["dir", "--redact=100", "--ignore-gitleaks-allow", "--gitleaks-ignore-path=/dev/null",
    "--max-archive-depth=3", "--no-banner", "--no-color", pakket], { stdio: "inherit" });
}
