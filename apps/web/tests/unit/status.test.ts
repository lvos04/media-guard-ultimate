import { describe, expect, it } from "vitest";

import type { LibraryItem } from "@/lib/api/contracts";
import { EPISODE_STATE, libraryStatus, UNCERTAIN_VIEW } from "@/lib/status";
import { totalStatus } from "@/lib/todo";
import { nl } from "@/lib/i18n/messages";
import { en } from "@/lib/i18n/messages/en";

const item = (stats: Partial<LibraryItem["stats"]>, monitored = true) =>
  ({ monitored, stats: { total: 10, available: 10, missing: 0, cutoff_unmet: 0, upcoming: 0, ...stats } }) as unknown as LibraryItem;

describe("beschikbaarheid is geen gezondheid (ADR-0016 punt 6)", () => {
  it("geen enkele beschikbaarheidstoestand is groen, amber of rood", () => {
    for (const view of Object.values(EPISODE_STATE)) expect(["neutral", "strong"]).toContain(view.tone);
    for (const view of [item({}), item({ missing: 2 }), item({ cutoff_unmet: 1 }), item({ available: 0, upcoming: 3 }), item({}, false)].map(libraryStatus)) {
      expect(["neutral", "strong"]).toContain(view.tone);
      expect(view.icon).toBeTruthy();
    }
  });
  it("ontbreekt = neutraal label met sterke rand; onzeker = de as Onbekend", () => {
    expect(EPISODE_STATE.missing.tone).toBe("strong");
    expect(libraryStatus(item({ missing: 2 }))).toMatchObject({ tone: "strong", label: "library.missingCount", count: 2 });
    expect(UNCERTAIN_VIEW.tone).toBe("unknown");
  });
});

describe("totaalstatus (SysteemStatusPaneel)", () => {
  it("core weg → Onbekend", () => {
    expect(totalStatus({ level: "offline", errors: 0 }, [])).toBe("unknown");
  });
  it("alleen inrichting of info → nog steeds Gezond (nooit rood of amber voor inrichting)", () => {
    expect(totalStatus({ level: "warning", errors: 0 }, [{ kind: "setup" }, { kind: "info" }])).toBe("healthy");
  });
  it("een storing, waarschuwing of gemeten fout → Beperkt", () => {
    expect(totalStatus({ level: "action", errors: 0 }, [{ kind: "fault" }])).toBe("warning");
    expect(totalStatus({ level: "warning", errors: 0 }, [{ kind: "warning" }])).toBe("warning");
    expect(totalStatus({ level: "action", errors: 3 }, [])).toBe("warning");
  });
});

describe("Peil-woorden in beide talen (PEIL_MIGRATIE §6.7)", () => {
  it.each([
    ["status.degraded", "Beperkt", "Degraded"],
    ["status.offline", "Storing", "Down"],
    ["status.succeeded", "Afgerond", "Completed"],
    ["health.allGood", "Alles gezond", "All healthy"],
    ["state.offlineTitle", "MediaGuard-core reageert niet", "MediaGuard core is not responding"],
    ["state.traceId", "Trace-ID", "Trace ID"],
    ["hub.back", "Terug naar de Hub", "Back to the Hub"],
    ["chart.showTable", "Toon als tabel", "Show as table"],
    ["chart.noReading", "Geen meting", "No reading"],
  ] as const)("%s", (key, nlText, enText) => {
    expect(nl[key]).toBe(nlText);
    expect(en[key]).toBe(enText);
  });
  it("elke nieuwe sleutel heeft een niet-lege Engelse tekst", () => {
    const peilKeys = Object.keys(nl).filter((k) => /^(chart|hub|copy|kpi\.engine|lock\.notAFault|hero\.(waiting|total)|notif\.severity|state\.(traceId|errorCode)|topbar\.profileName|live\.title)/.test(k));
    expect(peilKeys.length).toBeGreaterThan(20);
    for (const k of peilKeys) expect((en as Record<string, string>)[k]?.trim(), k).toBeTruthy();
  });
});
