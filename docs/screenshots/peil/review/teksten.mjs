// Peil-review MediaGuard (stap 5): laadt alle NL- en en-US-teksten (TS → JS via typescript.transpileModule, in een
// tijdelijke map) en controleert de Peil-woordenlijst (§11), ontbrekende vertalingen en placeholders. Alleen-lezen.
// Gebruik: node docs/screenshots/peil/review/teksten.mjs "$PWD/apps/web"
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
const WEB = process.argv[2];
const require = createRequire(path.join(WEB, "package.json"));
const ts = require("typescript");
const tmp = mkdtempSync(path.join(tmpdir(), "mg-teksten-"));
for (const sub of ["", "en"]) {
  mkdirSync(path.join(tmp, sub), { recursive: true });
  for (const f of readdirSync(path.join(WEB, "lib/i18n/messages", sub))) {
    if (!f.endsWith(".ts")) continue;
    const src = readFileSync(path.join(WEB, "lib/i18n/messages", sub, f), "utf8");
    let js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
    js = js.replace(/from "\.\/([^"]+)"/g, 'from "./$1.mjs"').replace(/from "\.\.\/([^"]+)"/g, 'from "../$1.mjs"');
    writeFileSync(path.join(tmp, sub, f.replace(/\.ts$/, ".mjs")), js);
  }
}
const nlMod = await import(path.join(tmp, "index.mjs"));
const enMod = await import(path.join(tmp, "en", "index.mjs"));
const nl = nlMod.nl; const en = enMod.en ?? enMod.default ?? Object.values(enMod).find((v) => typeof v === "object");
const out = { nl_n: Object.keys(nl).length, en_n: Object.keys(en).length };
out.ontbreekt_en = Object.keys(nl).filter((k) => !(k in en));
out.alleen_en = Object.keys(en).filter((k) => !(k in nl));
const VERBODEN_NL = [[/\bVerminderd\b/, "Verminderd → Beperkt"], [/\bVerstoord\b/, "Verstoord → Beperkt"], [/\bIn orde\b/i, "In orde → Gezond"], [/\bActueel\b/, "Actueel (status)"],
  [/\bNiet gezond\b/i, "Niet gezond → Storing"], [/Gelukt!/, "Gelukt!"], [/\bsnooze/i, "snoozen → uitstellen"], [/\bsafe mode\b/i, "safe mode → veilige modus"], [/\bblast radius\b/i, "blast radius → reikwijdte"],
  [/\brunbook\b/i, "runbook → stappenplan"], [/\bhealthcheck\b/i, "healthcheck → gezondheidscontrole"], [/\ballowlist\b/i, "allowlist → lijst met toegestane acties"], [/Weet je het zeker/i, "Weet je het zeker?"],
  [/^OK$/, "OK"], [/^Doorgaan$/, "Doorgaan"], [/!(\s|$)/, "uitroepteken"], [/\bLaden…?\s*$/, "Laden… (mag alleen bij echt laden)"], [/\bOffline\b/, "Offline (als status: Storing/Onbekend)"]];
const VERBODEN_EN = [[/\bDegraded\b/, null], [/\bOK\b/, "OK"], [/!(\s|$)/, "uitroepteken"], [/Are you sure/i, "Are you sure?"]];
out.nl_verdacht = [];
for (const [k, v] of Object.entries(nl)) for (const [re, why] of VERBODEN_NL) if (typeof v === "string" && re.test(v)) out.nl_verdacht.push(`${why} | ${k}: ${v.slice(0, 110)}`);
out.en_verdacht = [];
for (const [k, v] of Object.entries(en)) for (const [re, why] of VERBODEN_EN) if (why && typeof v === "string" && re.test(v)) out.en_verdacht.push(`${why} | ${k}: ${v.slice(0, 110)}`);
// statuswoorden: NL ↔ en-US gelijkwaardig
const PAAR = [["Gezond", "Healthy"], ["Beperkt", "Degraded"], ["Storing", "Down"], ["Onbekend", "Unknown"], ["Verouderd", "Stale"], ["Gepauzeerd", "Paused"], ["Alles gezond", "All healthy"], ["Deels onbekend", "Partly unknown"], ["Niet beschikbaar", "Not available"], ["Nog niet beschikbaar", "Not available yet"]];
out.statuswoorden = [];
for (const [k, v] of Object.entries(nl)) {
  if (typeof v !== "string") continue;
  for (const [n, e] of PAAR) if (new RegExp(`^${n}\\b`).test(v) && en[k] && !new RegExp(`^${e}\\b`, "i").test(en[k])) out.statuswoorden.push(`${k}: "${v.slice(0, 50)}" ↔ "${String(en[k]).slice(0, 50)}"`);
}
// placeholders gelijk
out.placeholders_anders = [];
for (const k of Object.keys(nl)) { if (!(k in en)) continue; const a = [...String(nl[k]).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(","); const b = [...String(en[k]).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(","); if (a !== b) out.placeholders_anders.push(`${k}: {${a}} ↔ {${b}}`); }
// en-US die gelijk is aan NL (niet vertaald), alleen bij zinnen met Nederlandse woorden
out.en_gelijk_aan_nl = Object.keys(nl).filter((k) => en[k] === nl[k] && /\b(de|het|een|niet|wordt|bij|voor|naar)\b/.test(nl[k])).map((k) => `${k}: ${nl[k].slice(0, 80)}`);
console.log(JSON.stringify(out, null, 1));
