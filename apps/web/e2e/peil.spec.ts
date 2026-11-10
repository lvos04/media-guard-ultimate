import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Peil-testmatrix (ONTWERPTAAL §12, docs/ontwerp/PEIL_MIGRATIE.md §11.3) tegen de echte core (playwright.config.ts):
 *  - hoofdschermen × 320 / 390 / 768 / 1024 / 1440 (licht en donker afwisselend): geen horizontale overloop t.o.v. de
 *    apparaatbreedte (ADR-0016 punt 12), axe met de Peil-set (wcag2a/2aa/21a/21aa/22aa) zonder serious/critical,
 *    precies één h1, geen dubbele id's, knoppen/tabs/schakelaars/velden in <main> ≥ 44 px
 *  - statusvormen: Onbekend nooit met het pauze-icoon; groen alleen voor gemeten gezondheid
 *  - axe in dialogen en menu's (noodstop, profielmenu, sneltoetsen, zoeken op de telefoon) in licht en donker
 *  - toetsenbord: elke tabstop heeft de Peil-ring (≥ 3 px) en valt nooit achter kop- of ondernavigatie
 *  - grafieken: tabel-tweeling + lezen met de pijltjestoetsen; "Terug naar de Hub" alleen bij een geldig adres
 *  - review stap 5 (docs/ontwerp/PEIL_REVIEW.md): axe zonder enige overtreding (alle impactniveaus), tekstlinks
 *    onderstreept, losse links en selectievakjes met een raakvlak ≥ 44 px, Onbekend-kaart zonder "—"
 *  - QA (ADR-0020): de volledige ONTWERPTAAL §12-matrix — 320/360/390/768/1024/1440 × licht én donker; ook geen tekst
 *    < 14 px, geen ellips op inhoud, geen kopsprongen en elk aanraakdoel op de hele pagina ≥ 44 × 44 px (zelfde
 *    regels als docs/screenshots/peil/review/review.mjs); prefers-reduced-motion = geen beweging; forced colors houdt
 *    de stippelrand van Onbekend
 */
const ADMIN = { username: "beheerder", password: "e2e-wachtwoord-123" };
const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const MAIN: Array<{ route: string; slug: string }> = [
  { route: "/", slug: "overzicht" },
  { route: "/activiteit", slug: "activiteit" },
  { route: "/bibliotheek", slug: "bibliotheek" },
  { route: "/ontbrekend", slug: "ontbrekend" },
  { route: "/av1", slug: "ruimte" },
  { route: "/quarantaine", slug: "terugzetten" },
  { route: "/aanvragen", slug: "aanvragen" },
  { route: "/instellingen", slug: "instellingen" },
  { route: "/systeem", slug: "systeem" },
];
const WIDTHS = [320, 360, 390, 768, 1024, 1440];
const THEMES = ["light", "dark"] as const;

test.describe.configure({ mode: "default" });

test.beforeAll(async ({ request }) => {
  const status = await (await request.get("/api/v1/auth/setup-status")).json() as { needs_setup: boolean };
  if (status.needs_setup) expect((await request.post("/api/v1/auth/setup", { data: ADMIN })).ok()).toBeTruthy();
});

async function open(page: Page, route: string, theme: "light" | "dark" = "light"): Promise<void> {
  await page.emulateMedia({ colorScheme: theme });
  await page.addInitScript((t) => { try { localStorage.setItem("mg.theme", t); } catch { /* ignore */ } }, theme);
  const r = await page.request.post("/api/v1/auth/login", { data: ADMIN });
  expect(r.status(), "inloggen via de API").toBe(200);
  await page.goto(route);
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => undefined);
  await expect(page.locator("h1").first()).toBeVisible();
  // eerste laadronde afwachten (een kaart die blijft laden, bv. een trage AV1-engine, mag de meting niet blokkeren)
  await expect.poll(async () => page.locator("main .skeleton").count(), { timeout: 15_000 }).toBe(0).catch(() => undefined);
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

async function axe(page: Page): Promise<string[]> {
  const res = await new AxeBuilder({ page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"] }).withTags(AXE_TAGS).analyze();
  // review stap 5: elke overtreding telt (ook moderate/minor), niet alleen serious/critical
  return res.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length}× ${v.nodes[0]?.target.join(" ")}`);
}

/**
 * Meting zoals review.mjs (stap 5), in de pagina: tekst < 14 px, ellips op inhoud, kopsprongen en aanraakdoelen op de
 * hele pagina < 44 × 44 px (uitzondering: link of vakterm in lopende tekst, WCAG 2.5.8; het plotvak van een grafiek).
 */
async function peilMeting(page: Page): Promise<{ tekst: string[]; ellips: string[]; koppen: string[]; doelen: string[] }> {
  return page.evaluate(() => {
    const vis = (el: Element) => {
      const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
      if (r.width < 1 || r.height < 1 || cs.visibility === "hidden" || cs.display === "none") return false;
      for (let e: Element | null = el; e; e = e.parentElement) {
        const s = getComputedStyle(e);
        if (Number(s.opacity) === 0 || s.clipPath.startsWith("inset(50%") || s.clip.startsWith("rect(0")) return false;
      }
      return !el.closest(".sr-only,[hidden]");
    };
    const desc = (el: Element) => {
      const t = (el.getAttribute("aria-label") || el.textContent || el.getAttribute("title") || "").trim().replace(/\s+/g, " ").slice(0, 40);
      const id = el.getAttribute("data-testid");
      return `${el.tagName.toLowerCase()}${id ? `[${id}]` : ""} "${t}"`;
    };
    const tekst: string[] = []; const ellips: string[] = []; const koppen: string[] = []; const doelen: string[] = [];
    for (const el of document.body.querySelectorAll("*")) {
      if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"].includes(el.tagName)) continue;
      if (el instanceof SVGElement && !["text", "tspan"].includes(el.tagName)) continue;
      const own = [...el.childNodes].some((n) => n.nodeType === 3 && (n.textContent ?? "").trim().length > 0);
      if (!own || !vis(el)) continue;
      const cs = getComputedStyle(el);
      if (parseFloat(cs.fontSize) < 13.95) tekst.push(`${desc(el)} ${cs.fontSize}`);
      if (cs.textOverflow === "ellipsis" && el.scrollWidth > el.clientWidth + 1) ellips.push(desc(el));
    }
    let prev = 0;
    for (const h of document.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
      if (!vis(h)) continue;
      const n = Number(h.tagName[1]);
      if (prev && n > prev + 1) koppen.push(`h${prev}→h${n} "${(h.textContent ?? "").trim().slice(0, 40)}"`);
      prev = n;
    }
    const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=switch], [role=checkbox], [role=radio], [role=option], [role=menuitem], [tabindex]:not([tabindex="-1"])';
    for (const el of document.querySelectorAll<HTMLElement>(sel)) {
      if (el.tagName === "MAIN" || el.classList.contains("skip-link") || el.closest(".skip-link,[inert]")) continue;
      let target: HTMLElement = el;
      if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) {
        const lab = el.closest("label") ?? (el.id ? document.querySelector<HTMLElement>(`label[for="${CSS.escape(el.id)}"]`) : null);
        if (lab) target = lab; else if (!vis(el)) continue;
      } else if (!vis(el)) continue;
      if (target !== el && !vis(target)) continue;
      const cs = getComputedStyle(el);
      if (cs.display === "inline" || (cs.display.startsWith("inline") && el.matches("a,.term-btn"))) {
        let p = el.parentElement; while (p && getComputedStyle(p).display === "inline" && p.parentElement) p = p.parentElement;
        const zin = [...(p?.childNodes ?? [])].filter((n) => n.nodeType === 3).map((n) => n.textContent ?? "").join("").replace(/[·•|,]/g, "").trim();
        if (zin.length >= 3) continue;
      }
      if (["INPUT", "SELECT", "TEXTAREA"].includes(el.tagName) && (parseFloat(cs.borderTopWidth) || 0) === 0
        && el.parentElement?.querySelectorAll("input,select,textarea").length === 1 && el.parentElement.tagName === "LABEL") target = el.parentElement;
      if (el.getAttribute("tabindex") === "0" && (el.matches("[role=img],figure *,[data-chart-plot],svg") || el.closest("figure"))) continue;
      const r = target.getBoundingClientRect();
      if (r.width < 43.5 || r.height < 43.5) doelen.push(`${desc(el)} ${Math.round(r.width)}×${Math.round(r.height)}`);
    }
    return { tekst, ellips, koppen, doelen };
  });
}

for (const width of WIDTHS) {
  for (const s of MAIN) {
    for (const theme of THEMES) test(`peil ${s.slug} · ${width} · ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: width < 1024 ? 844 : 900 });
      await open(page, s.route, theme);
      const overflow = await page.evaluate((w) => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - w, width);
      expect(overflow, `geen horizontale overloop op ${width} px`).toBeLessThanOrEqual(1);
      expect(await axe(page), "axe (Peil-set) zonder overtredingen").toEqual([]);
      expect(await page.locator("h1").count(), "precies één h1").toBe(1);
      const dupIds = await page.evaluate(() => {
        const seen = new Map<string, number>();
        for (const el of document.querySelectorAll("[id]")) seen.set(el.id, (seen.get(el.id) ?? 0) + 1);
        return [...seen].filter(([, n]) => n > 1).map(([id]) => id);
      });
      expect(dupIds, "geen dubbele id's").toEqual([]);
      // 44 px voor elke knop, tab, schakelaar, keuzelijst en elk veld in <main> (links in lopende tekst uitgezonderd)
      const small = await page.evaluate(() => {
        const out: string[] = [];
        for (const el of document.querySelectorAll<HTMLElement>("main button, main [role=tab], main [role=switch], main select, main input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=range])")) {
          const r = el.getBoundingClientRect();
          // vaktermen (.term-btn) staan in lopende tekst: de inline-uitzondering van WCAG 2.5.8, net als links in tekst
          if (!r.width || !r.height || el.closest(".sr-only, [hidden], details:not([open]) > :not(summary), .term-btn")) continue;
          if (r.height < 43.5) out.push(`${el.tagName.toLowerCase()} "${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 30)}" ${Math.round(r.height)}px`);
        }
        return out;
      });
      expect(small, "bedienbare elementen in main ≥ 44 px hoog").toEqual([]);
      // review stap 5: losse links (niet in een zin) en selectievakjes (via hun label) ≥ 44 px; tekstlinks onderstreept
      const links = await page.evaluate(() => {
        const out: string[] = [];
        const inSentence = (el: Element) => {
          let p = el.parentElement;
          while (p && getComputedStyle(p).display === "inline" && p.parentElement) p = p.parentElement;
          return [...(p?.childNodes ?? [])].filter((n) => n.nodeType === 3).map((n) => n.textContent ?? "").join("").replace(/[·•|,]/g, "").trim().length >= 3;
        };
        for (const el of document.querySelectorAll<HTMLElement>("main a[href], main input[type=checkbox], main input[type=radio]")) {
          const target = el instanceof HTMLInputElement ? el.closest("label") ?? el : el;
          const r = target.getBoundingClientRect();
          if (!r.width || !r.height || el.closest(".sr-only, [hidden], details:not([open]) > :not(summary)")) continue;
          const name = (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 30);
          if (el.tagName === "A" && getComputedStyle(el).display.startsWith("inline") && inSentence(el)) continue;
          if (r.height < 43.5) out.push(`doel ${el.tagName.toLowerCase()} "${name}" ${Math.round(r.height)}px`);
          if (el.matches("a:not([class]), a.link-more, a.link-arrow, a.back-link, a.list-sub") && !el.querySelector("div, p, img, h2, h3")
            && !getComputedStyle(el).textDecorationLine.includes("underline")) out.push(`link zonder onderstreping "${name}"`);
        }
        return out;
      });
      expect(links, "losse links en selectievakjes ≥ 44 px, tekstlinks onderstreept").toEqual([]);
      // Peil §4.4: onbekend is nooit een getal, 0 of "—"
      expect(await page.locator('.kpi:has([data-status="unknown"]) .kpi-value').count(), "Onbekend-kaart zonder waarde").toBe(0);
      // Peil §7: Onbekend nooit met het pauze-icoon; groen (chip--healthy) alleen voor een gezondheidsstatus
      expect(await page.locator('[data-status="unknown"] .lucide-pause, [data-status="unknown"] .lucide-circle-pause').count(), "Onbekend nooit met het pauze-icoon").toBe(0);
      expect(await page.locator('.chip--healthy:not([data-status="healthy"])').count(), "groen alleen voor gemeten gezondheid").toBe(0);
      // ONTWERPTAAL §12 (QA, ADR-0020): tekst ≥ 14 px, geen ellips op inhoud, geen kopsprongen, doelen ≥ 44 × 44 px
      const m = await peilMeting(page);
      expect(m.tekst, "geen tekst kleiner dan 14 px").toEqual([]);
      expect(m.ellips, "geen ellips op inhoud").toEqual([]);
      expect(m.koppen, "geen kopsprongen").toEqual([]);
      expect(m.doelen, "elk aanraakdoel op de pagina ≥ 44 × 44 px").toEqual([]);
    });
  }
}

test("peil prefers-reduced-motion: geen beweging (animaties en overgangen ≈ 0)", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const route of ["/", "/av1", "/activiteit"]) {
    await open(page, route);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const moving = await page.evaluate(() => {
      const out: string[] = [];
      const dur = (v: string) => Math.max(0, ...v.split(",").map((x) => (x.trim().endsWith("ms") ? parseFloat(x) : parseFloat(x) * 1000) || 0));
      for (const el of document.querySelectorAll("*")) {
        for (const pseudo of [null, "::before", "::after"]) {
          const cs = getComputedStyle(el, pseudo);
          const a = cs.animationName !== "none" ? dur(cs.animationDuration) : 0;
          const t = dur(cs.transitionDuration);
          if (a > 10 || t > 10) out.push(`${el.tagName.toLowerCase()}${pseudo ?? ""}.${String((el as HTMLElement).className).slice(0, 40)} animatie ${a} ms, overgang ${t} ms`);
        }
      }
      for (const an of document.getAnimations()) {
        const d = Number(an.effect?.getTiming().duration ?? 0);
        if (an.playState === "running" && d > 10) out.push(`lopende animatie ${d} ms`);
      }
      return [...new Set(out)].slice(0, 20);
    });
    expect(moving, `${route}: niets beweegt bij prefers-reduced-motion`).toEqual([]);
  }
});

test("peil forced colors: Onbekend houdt de stippelrand", async ({ page }) => {
  // de AV1-engine draait niet in de e2e-omgeving (playwright.config.ts): Ruimte toont "Onbekend — AV1-engine reageert niet"
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ forcedColors: "active" });
  await open(page, "/av1");
  await page.emulateMedia({ forcedColors: "active" });
  expect(await page.evaluate(() => matchMedia("(forced-colors: active)").matches), "forced colors actief").toBe(true);
  const onbekend = page.locator('[data-status="unknown"]:visible');
  await expect(onbekend.first(), "er staat iets op Onbekend").toBeVisible();
  // de stippelvorm zit op het element zelf (pil, kaart) of op zijn icoontegel (StateView: de container is een kale
  // houder met data-status, de tegel .state-icon draagt de stippelrand)
  const randen = await onbekend.evaluateAll((els) => els.map((el) => {
    const stippel = (e: Element) => {
      const cs = getComputedStyle(e);
      return ["top", "right", "bottom", "left"].some((z) => ["dashed", "dotted"].includes(cs.getPropertyValue(`border-${z}-style`))
        && parseFloat(cs.getPropertyValue(`border-${z}-width`)) >= 1);
    };
    return { tekst: (el.textContent ?? "").trim().slice(0, 40), stippel: stippel(el) || [...el.querySelectorAll("*")].some(stippel) };
  }));
  expect(randen.length, "minstens één Onbekend-vorm gemeten").toBeGreaterThan(0);
  expect(randen.filter((r) => !r.stippel).map((r) => r.tekst), "elke Onbekend-vorm houdt zijn stippelrand").toEqual([]);
});

for (const theme of ["light", "dark"] as const) {
  test(`peil dialogen en menu's zonder axe-fouten · ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await open(page, "/", theme);
    const profile = page.getByTestId("profile-button");
    await profile.click();
    await expect(page.getByTestId("profile-menu")).toBeVisible();
    expect(await axe(page), "profielmenu").toEqual([]);
    await page.getByTestId("estop-button").click();
    const estop = page.getByRole("alertdialog");
    await expect(estop).toBeVisible();
    expect(await axe(page), "noodstop-dialoog").toEqual([]);
    await page.keyboard.press("Escape");
    await expect(estop).toBeHidden();
    await page.keyboard.press("?");
    const keys = page.getByRole("dialog", { name: "Sneltoetsen" });
    await expect(keys).toBeVisible();
    expect(await axe(page), "sneltoetsen").toEqual([]);
    await page.keyboard.press("Escape");
    await expect(keys).toBeHidden();
    // telefoon: profielmenu en zoeken als volledig scherm
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.getByTestId("profile-button").click();
    await expect(page.getByTestId("profile-menu")).toBeVisible();
    await expect(page.getByTestId("estop-button"), "noodstopknop heeft een zichtbare naam").toHaveText(/Noodstop/);
    expect(await axe(page), "profielmenu telefoon").toEqual([]);
    await page.keyboard.press("Escape");
    await page.getByTestId("mobile-search").click();
    await expect(page.getByRole("dialog", { name: /Zoek/ })).toBeVisible();
    expect(await axe(page), "zoeken telefoon").toEqual([]);
  });
}

for (const width of [390, 1440]) {
  test(`peil toetsenbord: ring ≥ 3 px en nooit bedekt · ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 1024 ? 844 : 900 });
    await open(page, "/instellingen");
    const problems: string[] = [];
    for (let i = 0; i < 40; i++) {
      await page.keyboard.press("Tab");
      const r = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        const cs = getComputedStyle(el);
        const box = el.getBoundingClientRect();
        const name = (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 30);
        const ring = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) >= 3;
        const cx = Math.min(innerWidth - 1, Math.max(0, box.left + box.width / 2));
        const cy = Math.min(innerHeight - 1, Math.max(0, box.top + Math.min(box.height / 2, 10)));
        const hit = document.elementFromPoint(cx, cy);
        const bar = hit?.closest(".topbar, .bottomnav");
        return { name, ring, covered: Boolean(bar && !bar.contains(el)), skip: el.classList.contains("skip-link") };
      });
      if (!r || r.skip) continue;
      if (!r.ring) problems.push(`geen Peil-ring: ${r.name}`);
      if (r.covered) problems.push(`bedekt door kop/ondernavigatie: ${r.name}`);
    }
    expect(problems).toEqual([]);
  });
}

test("peil grafiek: Besparing per dag met tabel, toetsenbord en 'Geen meting' voor een ontbrekende dag", async ({ page }) => {
  // AV1-engine nagebootst (zoals studio-av1.spec.ts): 14 dagen, dag 10 zonder meting
  const days = Array.from({ length: 14 }, (_, i) => new Date(Date.UTC(2026, 10, 1 + i)).toISOString().slice(0, 10));
  await page.route("**/api/v1/encoder/statistieken", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    totaal: { bespaard_bytes: 2.5e12, gemiddeld: 41.5, jobs: 120 }, doorvoer_bytes_per_dag_7d: 1.1e11, resterend_bytes: 4e12, prognose_klaar: null,
    per_dag: days.filter((_, i) => i !== 9).map((dag, i) => ({ dag, bespaard_bytes: (40 + i) * 1e9, jobs: 2 + (i % 3) })),
  }) }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "/av1");
  const chart = page.getByTestId("grafiek-besparing-per-dag");
  await expect(chart).toBeVisible();
  const plot = chart.locator('[tabindex="0"][role="img"]');
  await expect(plot).toHaveAttribute("aria-label", /pijltjestoetsen/);
  await plot.focus();
  await page.keyboard.press("Home");
  const live = chart.getByTestId("grafiek-besparing-per-dag-lezing");
  await expect(live).toHaveText(/bespaard/);
  for (let i = 0; i < 9; i++) await page.keyboard.press("ArrowRight");
  await expect(live, "de ontbrekende dag is 'Geen meting', nooit 0").toHaveText(/Geen meting/);
  await page.keyboard.press("Escape");
  await expect(live).toHaveText("");
  const twin = chart.getByTestId("grafiek-besparing-per-dag-tabel");
  await twin.getByText("Toon als tabel").click();
  await expect(twin.getByText("Verberg tabel")).toBeVisible();
  await expect(twin.locator("tbody tr"), "elke dag een rij, ook zonder meting").toHaveCount(14);
  await expect(twin.locator("tbody tr").nth(9)).toContainText("Geen meting");
  expect(await chart.locator("rect[data-gat]").count(), "gat als gearceerd vak, geen staaf van 0").toBe(1);
});

test("peil Terug naar de Hub: alleen bij een geldig system.hub_url", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, "/");
  const csrf = (await (await page.request.get("/api/v1/auth/me")).json()).csrf_token as string;
  const patch = (value: string) => page.request.patch("/api/v1/settings/scoped", {
    data: { changes: [{ scope_type: "global", scope_id: "", key: "system.hub_url", value }] }, headers: { "X-CSRF-Token": csrf } });
  try {
    expect((await patch("")).status()).toBe(200);
    await page.reload();
    await expect(page.getByTestId("sidebar-health")).toBeVisible();
    await expect(page.getByTestId("hub-link"), "leeg ⇒ geen link").toHaveCount(0);
    for (const bad of ["javascript:alert(1)", "http://hub.example:30080/pad", "http://127.0.0.1:30080", "http://localhost:30080"]) {
      expect((await patch(bad)).status(), `422 voor ${bad}`).toBe(422);
    }
    await page.reload();
    await expect(page.getByTestId("sidebar-health")).toBeVisible();
    await expect(page.getByTestId("hub-link"), "ongeldig ⇒ geen link").toHaveCount(0);
    // fictief testadres (.example), geen NAS-gegevens
    expect((await patch("http://hub.example:30080")).status()).toBe(200);
    await page.reload();
    const link = page.locator(".sidebar").getByTestId("hub-link");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "http://hub.example:30080");
    await expect(link).toHaveText(/Terug naar de Hub/);
    // telefoon: eerste item van het profielmenu
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTestId("profile-button").click();
    await expect(page.getByTestId("profile-menu").getByTestId("hub-link")).toBeVisible();
  } finally {
    await patch("");
  }
});
