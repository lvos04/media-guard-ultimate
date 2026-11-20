import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import OverviewPage from "@/app/(app)/page";
import { I18nProvider } from "@/lib/i18n";

const proef = vi.hoisted(() => ({
  bronnen: new Map<string, unknown>(), stop: false, aantal: 1, onbereikbaar: [] as string[],
}));

vi.mock("@/lib/api/hooks", () => ({
  useResource: (pad: string | null) => ({
    data: pad === "/status" ? { version: "proef", time: new Date().toISOString(), emergency_stop: { active: proef.stop } }
      : proef.bronnen.get(pad ?? "") ?? null,
    error: null, fetchedAt: Date.now(), stale: false, loading: false, reload: async () => {},
  }),
}));
vi.mock("@/components/av1/use-encoder", () => ({
  useEncoder: (pad: string) => ({ data: pad === "/health" ? { status: "ok" } : null,
    error: null, fetchedAt: Date.now(), stale: false, reload: async () => {} }),
}));
vi.mock("@/lib/todo", () => ({ useTodoList: () => ({ rows: [], missing: [], known: 0 }), useTodoLabel: (_list: unknown, label: string) => label }));
vi.mock("@/lib/events", () => ({ useCoreEvents: () => {} }));
vi.mock("@/components/mg/library-lock", () => ({ LibraryLockBanner: () => null }));
vi.mock("@/lib/health", () => ({
  useHealth: () => ({
    measurement: { state: "fresh", at: Date.now(), active: 1, errors: proef.aantal, complete: true, stale: false, failed: false, reload: async () => {} },
    breakdown: { instances_unreachable: proef.aantal }, unreachableIds: proef.onbereikbaar, activeParts: {},
  }),
}));
const scherm = () => renderToStaticMarkup(<I18nProvider locale="nl-NL"><OverviewPage /></I18nProvider>);

it("vergelijkt de getoonde taak- en systeemstatus met de meetgegevens", () => {
  const taak = { id: 680, source_path: "Testfilm.mkv", status: "running", progress: 0.5, updated_at: "2025-01-01T08:00:00Z" };
  proef.bronnen.set("/instances", []);
  proef.bronnen.set("/encoding/jobs", [taak]);
  proef.bronnen.set("/jobs", []);
  proef.bronnen.set("/calendar", { items: [] });
  const eerste = scherm();
  const paneel = eerste.match(/<section\b[^>]*data-testid="status-hero"[^>]*>/)?.[0];
  expect(paneel).toBeDefined();
  const totaal = paneel?.match(/data-status="([^"]*)"/)?.[1];
  const balk = eerste.match(/<div\b[^>]*role="progressbar"[^>]*>/)?.[0];
  const waarde = balk?.match(/aria-valuenow="([^"]*)"/)?.[1] ?? null;
  proef.stop = true;
  const pauze = scherm().includes("Gepauzeerd (noodstop)");
  console.log(`Eén gemelde fout: totaal=${totaal}. Oude taak: balk=${waarde}%. Kernstop zonder encoderbevestiging: taak gepauzeerd=${pauze}.`);
  expect.soft(totaal).not.toBe("healthy");
  expect.soft(waarde).toBeNull();
  expect(pauze).toBe(false);
});

it.each([
  { naam: "eigen actuele rij vertegenwoordigt de fout", bron: ["film"], oud: false, fouten: 1, lokaal: 0 },
  { naam: "andere instantie vertegenwoordigt de fout niet", bron: ["serie"], oud: false, fouten: 1, lokaal: 1 },
  { naam: "oude eigen rij vertegenwoordigt de actuele fout niet", bron: ["film"], oud: true, fouten: 1, lokaal: 1 },
  { naam: "downloadclient naast de eigen storing blijft meetellen", bron: ["film"], oud: false, fouten: 2, lokaal: 1 },
])("$naam", ({ bron, oud, fouten, lokaal }) => {
  proef.aantal = fouten;
  proef.onbereikbaar = bron;
  proef.stop = false;
  proef.bronnen.set("/instances", [{ id: "film", status: "offline", name: "Proeffilm",
    last_check_at: oud ? "2025-01-01T08:00:00Z" : new Date().toISOString() }]);
  const html = scherm();
  const controles = html.match(/<li><a[^>]+href="\/activiteit\?tab=problemen".*?<\/li>/)?.[0];
  expect(controles).toContain(`data-status="${lokaal ? "degraded" : "healthy"}"`);
  expect(html).not.toMatch(/data-testid="status-hero" data-status="healthy"/);
});
