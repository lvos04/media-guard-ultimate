// Rechten (scopes) op API-sleutels (S-P3-600, docs/ontwerp/API_SLEUTEL_SCOPES_ONTWERP.md): wat het formulier meestuurt,
// hoe een recht in de lijst staat (bekend = neutraal label, onbekend = Onbekend met reden) en de teksten in nl en en-US.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { KNOWN_SCOPES, SCOPE_TEXT, ScopeChips, isKnownScope, scopesForRequest } from "@/components/mg/apikey-scopes";
import { I18nProvider } from "@/lib/i18n";
import { nl } from "@/lib/i18n/messages";
import { en } from "@/lib/i18n/messages/en";

const html = (node: React.ReactNode) => renderToStaticMarkup(<I18nProvider locale="nl-NL">{node}</I18nProvider>);

describe("rechten in het formulier", () => {
  it("kent alleen emergency_stop:on (wanted:search pas na BG-05)", () => {
    expect([...KNOWN_SCOPES]).toEqual(["emergency_stop:on"]);
    expect(isKnownScope("emergency_stop:on")).toBe(true);
    for (const s of ["wanted:search", "emergency_stop:off", "EMERGENCY_STOP:ON", ""]) expect(isKnownScope(s), s).toBe(false);
  });
  it("een operator-verzoek bevat nooit rechten; een lege lijst gaat niet mee (= geen rechten)", () => {
    expect(scopesForRequest("operator", ["emergency_stop:on"])).toBeUndefined();
    expect(scopesForRequest("operator", [])).toBeUndefined();
    expect(scopesForRequest("viewer", [])).toBeUndefined();
    expect(scopesForRequest("viewer", ["emergency_stop:on"])).toEqual(["emergency_stop:on"]);
  });
});

describe("ScopeChips (tabelcel Rol en intrekdialoog)", () => {
  it("geen of lege rechten: niets, de rij ziet eruit als altijd", () => {
    expect(html(<ScopeChips scopes={null} testId="x" />)).toBe("");
    expect(html(<ScopeChips scopes={[]} testId="x" />)).toBe("");
    expect(html(<ScopeChips testId="x" />)).toBe("");
  });
  it("bekend recht: neutraal label met tekst en het noodstopicoon, geen status en nooit groen", () => {
    const out = html(<ScopeChips scopes={["emergency_stop:on"]} testId="apikey-scopes-hub" />);
    expect(out).toContain('data-testid="apikey-scopes-hub"');
    expect(out).toContain("Noodstop activeren");
    expect(out).toContain("lucide-octagon-alert");
    expect(out).toContain("flex-wrap");
    expect(out).not.toContain("data-status");
    expect(out).not.toContain("chip--healthy");
    expect(out).not.toContain(nl["apikeys.scope.unknownHint"]);
  });
  it("onbekend recht: de vorm Onbekend (stippelrand) met de code en één regel uitleg", () => {
    const out = html(<ScopeChips scopes={["emergency_stop:on", "wanted:search"]} testId="t" />);
    expect(out).toContain('data-status="unknown"');
    expect(out).toContain("border-dashed");
    expect(out).toContain("Onbekend recht: wanted:search");
    expect(out.split(nl["apikeys.scope.unknownHint"]).length).toBe(2);
    expect(out).toContain("Noodstop activeren");
  });
});

describe("teksten nl en en-US (ontwerp §4.1)", () => {
  const keys = ["apikeys.form.scopes", "apikeys.form.scopesHint", "apikeys.form.hintViewerScoped", "apikeys.scope.estopOn",
    "apikeys.scope.estopOnHint", "apikeys.scope.unknown", "apikeys.scope.unknownHint"] as const;
  it("de zeven nieuwe teksten staan er letterlijk, in beide talen", () => {
    expect(keys.map((k) => nl[k])).toEqual([
      "Rechten (scopes)",
      "Optioneel. Zet alleen aan wat de koppeling echt nodig heeft. Alles uit = een gewone kijker-sleutel.",
      "Alleen lezen, plus precies de rechten hieronder. Verder kan deze sleutel niets laten zoeken of wijzigen.",
      "Noodstop activeren",
      "Mag de noodstop aanzetten, nooit opheffen. Bedoeld voor de Hub.",
      "Onbekend recht: {scope}",
      "Deze versie van MediaGuard kent dit recht niet en staat er niets mee toe.",
    ]);
    expect(keys.map((k) => en[k])).toEqual([
      "Permissions (scopes)",
      "Optional. Only switch on what the integration really needs. All off = a regular viewer key.",
      "Read only, plus exactly the permissions below. Otherwise this key cannot trigger searches or change anything.",
      "Activate emergency stop",
      "May switch the emergency stop on, never off. Intended for the Hub.",
      "Unknown permission: {scope}",
      "This version of MediaGuard does not know this permission and allows nothing with it.",
    ]);
  });
  it("de bestaande teksten veranderen niet", () => {
    expect(nl["apikeys.form.hintViewer"]).toBe("Alleen lezen; kan niets laten zoeken of wijzigen.");
    expect(nl["apikeys.form.hintOperator"]).toBe("Voor Media Source Optimizer: mag lezen en een herzoek laten starten.");
  });
  it("elk bekend recht heeft een naam en uitleg met 'nooit opheffen'", () => {
    for (const s of KNOWN_SCOPES) {
      expect(nl[SCOPE_TEXT[s].label]).toBe("Noodstop activeren");
      expect(nl[SCOPE_TEXT[s].hint]).toContain("nooit opheffen");
      expect(en[SCOPE_TEXT[s].hint]).toContain("never off");
    }
  });
});
