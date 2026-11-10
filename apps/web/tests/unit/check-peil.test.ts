import { createHash } from "node:crypto";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { checkCopies, parseHeaded, PEIL_VERSION } from "../../scripts/check-peil.mjs";

// Pin- en driftregel van de Peil-kopieën in de Docker-context (ONTWERPTAAL §14, E08 van Peil 1.1.1): versie + bron-commit
// + sha256 in de kop, gelijk aan design/ontwerptaal/ en aan design/ontwerptaal/HERKOMST.json. Negatieve gevallen op een
// tijdelijke kopie van beide mappen.
const web = path.resolve(import.meta.dirname, "..", "..");
const design = path.resolve(web, "..", "..", "design", "ontwerptaal");
const tmp: string[] = [];

function copyTree(): { w: string; d: string } {
  const root = mkdtempSync(path.join(tmpdir(), "peil-"));
  tmp.push(root);
  const w = path.join(root, "web");
  const d = path.join(root, "design");
  cpSync(path.join(web, "styles", "peil"), path.join(w, "styles", "peil"), { recursive: true });
  cpSync(design, d, { recursive: true });
  return { w, d };
}

afterEach(() => { for (const t of tmp.splice(0)) rmSync(t, { recursive: true, force: true }); });

describe("check-peil (Peil-kopieën in apps/web/styles/peil)", () => {
  it("de echte kopieën pinnen Peil 1.1.1 met bron-commit en kloppen", () => {
    expect(PEIL_VERSION).toBe("1.1.1");
    expect(checkCopies()).toEqual([]);
    const head = parseHeaded(readFileSync(path.join(web, "styles", "peil", "mediaguard-mso.css"), "utf8"));
    expect(head.version).toBe("1.1.1");
    expect(head.commit).toMatch(/^[0-9a-f]{40}$/);
    expect(createHash("sha256").update(head.body).digest("hex")).toBe(head.sha);
  });

  it("de aliaslaag 1.1.1 zet focusring en scroll-padding zelf terug (E14)", () => {
    const { body } = parseHeaded(readFileSync(path.join(web, "styles", "peil", "mediaguard-mso.css"), "utf8"));
    expect(body).toContain("outline: var(--focus-width) solid var(--ring);");
    expect(body).toContain("scroll-padding-top: calc(var(--header-h) + 0.75rem);");
  });

  it("met de hand gewijzigde kopie faalt", () => {
    const { w, d } = copyTree();
    const f = path.join(w, "styles", "peil", "ontwerptaal.css");
    writeFileSync(f, readFileSync(f, "utf8") + "\n.extra{}");
    expect(checkCopies(w, d).join("\n")).toMatch(/met de hand gewijzigd/);
  });

  it("kop zonder bron-commit faalt (E08)", () => {
    const { w, d } = copyTree();
    const f = path.join(w, "styles", "peil", "mediaguard-mso.css");
    writeFileSync(f, readFileSync(f, "utf8").replace(/@[0-9a-f]{40}/, ""));
    expect(checkCopies(w, d).join("\n")).toMatch(/geen bron-commit/);
  });

  it("andere versie of andere commit dan design/ontwerptaal en HERKOMST.json faalt", () => {
    const { w, d } = copyTree();
    const f = path.join(w, "styles", "peil", "ontwerptaal.css");
    writeFileSync(f, readFileSync(f, "utf8").replace("Peil 1.1.1", "Peil 1.1.0").replace(/@[0-9a-f]{40}/, "@" + "1".repeat(40)));
    const errors = checkCopies(w, d).join("\n");
    expect(errors).toMatch(/verwacht 1\.1\.1/);
    expect(errors).toMatch(/bron-commit ≠ design\/ontwerptaal/);
    expect(errors).toMatch(/HERKOMST\.json pint een andere bron-commit/);
  });

  it("zonder design/ontwerptaal (Docker-context) controleert hij alleen de kop en de inhoud", () => {
    const { w } = copyTree();
    expect(checkCopies(w, path.join(w, "bestaat-niet"))).toEqual([]);
  });
});
