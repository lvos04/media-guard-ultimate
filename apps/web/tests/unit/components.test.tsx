import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { dayRange } from "@/components/av1/system";
import { DataTable } from "@/components/mg/chart-frame";
import { LineChart } from "@/components/mg/charts";
import { KpiCard } from "@/components/mg/domain";
import { Button, InlineState, Segmented, StatusChip, Switch, TONE_ICON, type Tone } from "@/components/mg/primitives";
import { buttonVariants } from "@/components/ui/button";
import { I18nProvider } from "@/lib/i18n";
import { Zap } from "lucide-react";

const html = (node: React.ReactNode) => renderToStaticMarkup(<I18nProvider locale="nl-NL">{node}</I18nProvider>);

describe("StatusChip (Peil StatusBadge)", () => {
  it("houdt de .chip-haak en markeert de gezondheidsas", () => {
    const out = html(<StatusChip tone="healthy">Gezond</StatusChip>);
    expect(out).toContain("chip");
    expect(out).toContain('data-status="healthy"');
    expect(out).toContain("lucide-circle-check");
  });
  it("nooit het pauze-icoon voor onbekend of neutraal (Peil §7)", () => {
    for (const tone of ["unknown", "neutral", "unlinked", "strong"] as Tone[]) {
      expect(html(<StatusChip tone={tone}>x</StatusChip>)).not.toMatch(/lucide-(circle-)?pause/);
    }
    expect(TONE_ICON.neutral).toBeNull();
    expect(html(<StatusChip tone="unknown">Onbekend</StatusChip>)).toContain("border-dashed");
  });
  it("een statusvorm houdt zijn icoon, ook met icon={null} (review stap 5: nooit alleen kleur + woord)", () => {
    expect(html(<StatusChip tone="danger" icon={null}>Mislukt</StatusChip>)).toContain("lucide-octagon-x");
    expect(html(<StatusChip tone="warning" icon={null}>Afwijking</StatusChip>)).toContain("lucide-triangle-alert");
    expect(html(<StatusChip tone="unknown" icon={null}>Onbekend</StatusChip>)).toContain("lucide-circle-question-mark");
    expect(html(<StatusChip tone="neutral" icon={null}>Label</StatusChip>)).not.toContain("<svg");
  });
  it("een goede uitkomst die geen gezondheid is, is een neutraal label met vinkje", () => {
    const out = html(<StatusChip tone="success">Afgerond</StatusChip>);
    expect(out).toContain("lucide-check");
    expect(out).not.toContain("chip--healthy");
    expect(out).not.toContain("data-status");
  });
  it("Storing = octagon-x, Gepauzeerd = circle-pause", () => {
    expect(html(<StatusChip tone="danger">Storing</StatusChip>)).toContain("lucide-octagon-x");
    expect(html(<StatusChip tone="paused">Gepauzeerd</StatusChip>)).toContain("lucide-circle-pause");
  });
});

describe("Verouderd (Peil §4.4)", () => {
  it("is gestippeld en gearceerd met een klok, nooit groen en nooit de vorm van Onbekend", () => {
    const out = html(<StatusChip tone="stale">Verouderd</StatusChip>);
    expect(out).toContain('data-status="stale"');
    expect(out).toContain("chip--stale");
    expect(out).toContain("border-dashed");
    expect(out).toContain("arcering");
    expect(out).toContain("lucide-clock-alert");
    expect(out).not.toContain("chip--healthy");
    expect(out).not.toContain("lucide-circle-question-mark");
    expect(html(<StatusChip tone="stale" icon={null}>Verouderd</StatusChip>)).toContain("lucide-clock-alert");
  });
});

describe("Switch en Segmented: de reden bij wat uit staat", () => {
  it("Switch zet aria-describedby alleen als er een reden is", () => {
    expect(html(<Switch label="Afspeeltest" checked={false} disabled describedBy="ms-role-note" />)).toContain('aria-describedby="ms-role-note"');
    expect(html(<Switch label="Afspeeltest" checked={false} />)).not.toContain("aria-describedby");
  });
  const opties = [{ value: "a", label: "Aan" }, { value: "b", label: "Uit", disabled: true }];
  it("Segmented: een zichtbaar label benoemt de groep; anders blijft het aria-label", () => {
    const met = html(<Segmented label="Soort server" labelledBy="ms-kind-new" value="a" onChange={() => undefined} options={opties} />);
    expect(met).toContain('aria-labelledby="ms-kind-new"');
    expect(met).not.toContain('aria-label="Soort server"');
    const zonder = html(<Segmented label="Soort server" value="a" onChange={() => undefined} options={opties} />);
    expect(zonder).toContain('aria-label="Soort server"');
    expect(zonder).not.toContain("aria-labelledby");
  });
  it("Segmented: alleen een uitgeschakelde keuze wijst naar de reden", () => {
    const out = html(<Segmented label="Keuze" describedBy="ms-role-note" value="a" onChange={() => undefined} options={opties} />);
    expect(out.match(/aria-describedby="ms-role-note"/g)).toHaveLength(1);
    expect(out).toMatch(/<button[^>]*disabled=""[^>]*aria-describedby="ms-role-note"/);
  });
});

describe("Button (shadcn + Peil-codemod)", () => {
  it("elke maat is minstens 44 px en houdt de .btn-haak", () => {
    for (const size of ["standaard", "klein", "icoon"] as const) {
      const cls = buttonVariants({ size });
      expect(cls).toContain("btn");
      expect(cls).toMatch(/min-h-touch|size-touch/);
    }
  });
  it("uitgeschakeld zonder opacity, pending = aria-busy", () => {
    const out = html(<Button pending>Opslaan</Button>);
    expect(out).toContain('aria-busy="true"');
    expect(out).toContain("disabled=\"\"");
    expect(buttonVariants({})).not.toMatch(/opacity-/);
  });
});

describe("InlineState", () => {
  it("success is neutraal (vinkje), geen gezondheid; gate = slot", () => {
    const ok = html(<InlineState tone="success">Klaar</InlineState>);
    expect(ok).toContain("lucide-check");
    expect(ok).not.toContain("healthy");
    expect(html(<InlineState tone="gate">Vergrendeld</InlineState>)).toContain("border-gate");
  });
});

describe("StatKaart", () => {
  it("bron onbereikbaar → Onbekend + reden (stippelpil), geen 'Nog niet beschikbaar'", () => {
    const out = html(<KpiCard label="AV1-besparing" icon={Zap} value="12" unknown="Onbekend — AV1-engine reageert niet" />);
    expect(out).toContain("Onbekend — AV1-engine reageert niet");
    expect(out).toContain('data-status="unknown"');
    expect(out).not.toContain("Nog niet beschikbaar");
    expect(out).not.toContain(">12<");
    expect(out, "Peil §4.4: geen '—' als waarde").not.toContain("—</span>");
    expect(out).not.toContain("kpi-value");
  });
  it("nog geen waarde = skelet, geen '—' (review stap 5)", () => {
    const out = html(<KpiCard label="Wachtrij" icon={Zap} value="5" unavailable />);
    expect(out).toContain("skeleton");
    expect(out).not.toContain(">—<");
  });
});

describe("lijngrafiek met een gat (review stap 5)", () => {
  it("vlak per stuk tot de basislijn, gearceerd vak waar geen enkele reeks meet, geen wig over het gat", () => {
    const rows = [1, 2, null, null, 3, 4].map((v, i) => ({ t: String(i), a: v, b: v === null ? null : v + 1 }));
    const out = html(<LineChart data={rows} xKey="t" max={10} series={[{ key: "a", label: "A", color: "var(--chart-1)", fill: true }, { key: "b", label: "B", color: "var(--chart-2)" }]} />);
    expect(out.match(/data-gat="1"/g)?.length).toBe(1);
    const area = /<path d="([^"]+)" fill="var\(--chart-1\)" fill-opacity="0.1"/.exec(out)?.[1] ?? "";
    expect(area.match(/Z/g)?.length, "twee afzonderlijke vlakken").toBe(2);
    expect(area).not.toMatch(/L0,\d/);
  });
});

describe("grafiek-tweeling", () => {
  it("tabel met caption en th scope", () => {
    const out = html(<DataTable table={{ caption: "Besparing per dag", columns: ["Dag", "Bespaard"], numeric: [false, true], rows: [{ key: "a", cells: ["4 nov", "52 GB"] }] }} />);
    expect(out).toContain("<caption");
    expect(out).toContain('scope="col"');
    expect(out).toContain('scope="row"');
  });
  it("doorlopende datumas: elke dag een plek, ook zonder meting", () => {
    expect(dayRange("2026-11-01", "2026-11-04")).toEqual(["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"]);
    expect(dayRange("2026-10-31", "2026-11-01")).toEqual(["2026-10-31", "2026-11-01"]);
  });
});
