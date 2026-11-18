import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { expect, it } from "vitest";

it("laat de eerste installatie vóór e2e-bestanden met een beheerder lopen", () => {
  const map = path.resolve(import.meta.dirname, "../../e2e");
  const eerste = "mediaguard.spec.ts";
  const teVroeg = readdirSync(map).filter((naam) => {
    // De gewone Playwright-run sluit deze documentatieafdrukken uit (playwright.config.ts).
    if (naam === "design-shots.spec.ts" || !naam.endsWith(".spec.ts") || naam >= eerste) return false;
    const inhoud = readFileSync(path.join(map, naam), "utf8");
    return /\/api\/v1\/auth\/(?:setup(?:[\"'`/?]|$)|login)/.test(inhoud)
      || /(?:ADMIN|beheerder)/i.test(inhoud) && /(?:\/login|auth\/login)/.test(inhoud);
  });
  expect(teVroeg).toEqual([]);
});
