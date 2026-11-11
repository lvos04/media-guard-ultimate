// Mediaservers naast elkaar (Δ-MG-DUAL, ADR-0022): de regels waarmee de web-UI van een server een status maakt.
// Peil §4: "Gezond" staat alleen bij een gemeten, geslaagd contact; zonder meting is het "Onbekend" met een reden.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ServerChips, ServerFacts } from "@/components/mediaserver/mediaserver-tab";
import { isResultKey, serverState, sourceKey, suggestions, type MsServer } from "@/components/mediaserver/server-state";
import { I18nProvider } from "@/lib/i18n";
import { en } from "@/lib/i18n/messages/en";
import { nl } from "@/lib/i18n/messages";

const html = (node: React.ReactNode) => renderToStaticMarkup(<I18nProvider locale="nl-NL">{node}</I18nProvider>);
const geleden = (minuten: number) => new Date(Date.now() - minuten * 60_000).toISOString();

function server(over: Partial<MsServer> = {}): MsServer {
  return {
    server_id: 1, kind: "emby", label: "Emby", url: "http://emby:8096", url_source: "ingesteld", key_set: true,
    enabled: true, followed: true, legacy: true, auth: "X-Emby-Token", connected: true,
    contact: { at: geleden(1), ok: true, last_ok_at: geleden(1), error: null }, hold: { active: false }, path_map: [],
    results: { open: 0, ok: 0, transcode: 0, failed: 0, not_found: 0, nl_visible: 0, nl_missing: 0 },
    ...over,
  };
}
const jelly = (over: Partial<MsServer> = {}) =>
  server({ server_id: 2, kind: "jellyfin", label: "Jelly Leon", url: "http://jellyfin:8096", legacy: false, auth: "Authorization: MediaBrowser", ...over });

describe("serverState: koppeling is ingesteld, gezondheid is gemeten", () => {
  it("gekoppeld en gemeten goed = gezond", () => {
    expect(serverState(server())).toEqual({ link: "linked", health: "healthy", why: null });
  });
  it("ontkoppeld, zonder adres of zonder sleutel heeft geen gezondheid", () => {
    expect(serverState(server({ enabled: false, followed: false, connected: false }))).toEqual({ link: "detached", health: null, why: null });
    expect(serverState(server({ url: "", key_set: false, followed: false, connected: false, contact: null }))).toEqual({ link: "noAddress", health: null, why: null });
    expect(serverState(server({ key_set: false, followed: false, connected: false, contact: null }))).toEqual({ link: "noKey", health: null, why: null });
  });
  it("een gemeten fout is een storing", () => {
    const s = server({ connected: false, contact: { at: geleden(1), ok: false, last_ok_at: geleden(30), error: "HTTP 401" } });
    expect(serverState(s)).toEqual({ link: "linked", health: "down", why: "error" });
  });
  it("zonder meting of met een te oude meting is de stand onbekend, met de reden erbij", () => {
    expect(serverState(server({ connected: false, contact: null }))).toEqual({ link: "linked", health: "unknown", why: "notMeasured" });
    const oud = server({ connected: false, contact: { at: geleden(45), ok: true, last_ok_at: geleden(45), error: null } });
    expect(serverState(oud)).toEqual({ link: "linked", health: "unknown", why: "stale" });
  });
});

describe("ServerChips en ServerFacts", () => {
  it("toont Gekoppeld naast Gezond: twee assen, twee vormen", () => {
    const out = html(<ServerChips s={server()} />);
    expect(out).toContain("Gekoppeld");
    expect(out).toContain("Gezond");
    expect(out).toContain('data-status="healthy"');
  });
  it("groen staat er nooit zonder gemeten contact", () => {
    const zonderMeting: MsServer[] = [
      server({ connected: false, contact: null }),
      server({ connected: false, contact: { at: geleden(45), ok: true, last_ok_at: geleden(45), error: null } }),
      server({ connected: false, contact: { at: geleden(1), ok: false, last_ok_at: null, error: "time-out" } }),
      server({ enabled: false, followed: false, connected: false }),
      server({ key_set: false, followed: false, connected: false, contact: null }),
      server({ url: "", key_set: false, followed: false, connected: false, contact: null }),
    ];
    for (const s of zonderMeting) {
      const out = html(<ServerChips s={s} />);
      expect(out).not.toContain("Gezond");
      expect(out).not.toContain('data-status="healthy"');
    }
  });
  it("onbekend is gestippeld en noemt de reden en wat je kunt doen", () => {
    const s = server({ connected: false, contact: null });
    const chips = html(<ServerChips s={s} />);
    expect(chips).toContain("Onbekend");
    expect(chips).toContain("border-dashed");
    const feiten = html(<ServerFacts s={s} />);
    expect(feiten).toContain("Nog niet gemeten");
    expect(feiten).toContain("Verbinding testen");
  });
  it("een te oude goede meting is Verouderd: stippel, arcering en klok, nooit groen en nooit zomaar Onbekend", () => {
    const s = server({ connected: false, contact: { at: geleden(45), ok: true, last_ok_at: geleden(45), error: null } });
    const chips = html(<ServerChips s={s} />);
    expect(chips).toContain("Gekoppeld");
    expect(chips).toContain("Verouderd");
    expect(chips).toContain("lucide-clock-alert");
    expect(chips).toContain("border-dashed");
    expect(chips).toContain("arcering");
    expect(chips).toContain('data-status="stale"');
    expect(chips).not.toContain("Onbekend");
    expect(chips).not.toContain("Gezond");
    const feiten = html(<ServerFacts s={s} />);
    expect(feiten).toContain("Laatst bekend");
    expect(feiten).toContain("te oud om op te vertrouwen");
    expect(feiten).toContain("Verbinding testen");
  });
  it("de feiten staan als term en waarde (dl), het adres in vaste breedte met de herkomst erbij", () => {
    const feiten = html(<ServerFacts s={jelly({ path_map: [["/media", "/mnt/media"]] })} />);
    expect(feiten).toContain("<dl");
    expect(feiten).toMatch(/<dt[^>]*>Adres<\/dt>/);
    expect(feiten).toMatch(/<dt[^>]*>Aanmelden met<\/dt>/);
    expect(feiten).toMatch(/<dt[^>]*>Gekoppelde mappen<\/dt>/);
    expect(feiten).toContain("http://jellyfin:8096");
    expect(feiten).toContain("ingesteld");
  });
  it("een storing toont de fout van de server en het laatste goede contact", () => {
    const s = jelly({ connected: false, contact: { at: geleden(1), ok: false, last_ok_at: geleden(30), error: "HTTP 401" } });
    const chips = html(<ServerChips s={s} />);
    expect(chips).toContain("Storing");
    expect(chips).toContain("lucide-octagon-x");
    const feiten = html(<ServerFacts s={s} />);
    expect(feiten).toContain("Geen contact: HTTP 401");
    expect(feiten).toContain("Laatste goede contact");
    expect(feiten).toContain("Authorization: MediaBrowser");
  });
  it("een ontkoppelde server is Ontkoppeld en zegt dat adres en sleutel bewaard zijn", () => {
    const s = server({ enabled: false, followed: false, connected: false });
    expect(html(<ServerChips s={s} />)).toContain("Ontkoppeld");
    expect(html(<ServerFacts s={s} />)).toContain("Adres en sleutel zijn bewaard");
  });
  it("gevonden zonder sleutel is Niet gekoppeld en vraagt om de sleutel", () => {
    const s = server({ key_set: false, followed: false, connected: false, contact: null, url_source: "automatisch gevonden" });
    expect(html(<ServerChips s={s} />)).toContain("Niet gekoppeld");
    const feiten = html(<ServerFacts s={s} />);
    expect(feiten).toContain("automatisch gevonden");
    expect(feiten).toContain("Vul de API-sleutel in om te koppelen");
  });
  it("toont per server de afspeeltests, de Nederlandse ondertitels en dat er iemand kijkt", () => {
    const s = jelly({ hold: { active: true }, path_map: [["/media", "/mnt/media"]],
      results: { open: 1, ok: 3, transcode: 2, failed: 1, not_found: 1, nl_visible: 4, nl_missing: 2 } });
    const feiten = html(<ServerFacts s={s} />);
    expect(feiten).toContain("Hier kijkt nu iemand.");
    expect(feiten).toContain("3 direct");
    expect(feiten).toContain("2 mislukt");
    expect(feiten).toContain("4 zichtbaar");
    expect(feiten).toContain("/media → /mnt/media");
  });
});

describe("hulpfuncties", () => {
  it("sourceKey vertaalt de herkomst die de API in het Nederlands geeft", () => {
    expect(sourceKey("ingesteld")).toBe("ms.src.set");
    expect(sourceKey("automatisch gevonden")).toBe("ms.src.found");
    expect(sourceKey("niet gevonden")).toBe("ms.src.none");
    expect(sourceKey("iets anders")).toBe("ms.src.none");
  });
  it("suggestions laat alleen gevonden servers zien die nog niet in de lijst staan", () => {
    const gevonden = [{ url: "http://EMBY:8096/", label: "Emby" }, { url: "http://jellyfin:8096", label: "Jellyfin" }];
    expect(suggestions(gevonden, [server()])).toEqual([gevonden[1]]);
    expect(suggestions(gevonden, [server(), jelly()])).toEqual([]);
    expect(suggestions(gevonden, [])).toEqual(gevonden);
  });
  it("isResultKey kent alleen de uitkomsten met een eigen tekst", () => {
    expect(isResultKey("transcode")).toBe(true);
    expect(isResultKey("pending")).toBe(false);
  });
});

describe("teksten nl en en-US", () => {
  const keys = (Object.keys(nl) as Array<keyof typeof nl>).filter((k) => k.startsWith("ms."));
  const vars = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  it("elke ms-tekst bestaat in beide talen met dezelfde variabelen", () => {
    expect(keys.length).toBeGreaterThan(80);
    for (const key of keys) {
      expect(en[key], key).toBeTruthy();
      expect(vars(en[key]), key).toEqual(vars(nl[key]));
    }
  });
  it("gebruikt het koppel-vocabulaire van Peil: Gekoppeld/Linked, Niet gekoppeld/Not linked, Ontkoppeld/Unlinked", () => {
    expect([nl["ms.link.linked"], en["ms.link.linked"]]).toEqual(["Gekoppeld", "Linked"]);
    expect([nl["ms.status.none"], en["ms.status.none"]]).toEqual(["Niet gekoppeld", "Not linked"]);
    expect([nl["ms.link.detached"], en["ms.link.detached"]]).toEqual(["Ontkoppeld", "Unlinked"]);
  });
  it("zegt ontkoppelen (nooit losmaken) en noemt nergens een voorbeeldadres", () => {
    expect([nl["ms.detach"], en["ms.detach"]]).toEqual(["Ontkoppelen…", "Unlink…"]);
    expect([nl["ms.detach.confirm"], en["ms.detach.confirm"]]).toEqual(["Server ontkoppelen", "Unlink server"]);
    expect([nl["ms.remove.confirm"], en["ms.remove.confirm"]]).toEqual(["Server verwijderen", "Remove server"]);
    const eigen = (Object.keys(nl) as Array<keyof typeof nl>).filter((k) => k.startsWith("ms.") || k.startsWith("subs."));
    for (const key of eigen) {
      expect(nl[key], key).not.toMatch(/losma|losgem/i);
      // Peil §20: een plaatshouder of hulptekst is nooit een voorbeeldwaarde van een adres
      expect(`${nl[key]} ${en[key]}`, key).not.toMatch(/nas\.lan|\d+\.\d+\.\d+\.\d+/);
    }
  });
  it("de zichtbare knoptekst staat vooraan in de toegankelijke naam (WCAG 2.5.3)", () => {
    for (const taal of [nl, en]) expect(taal["ms.actionFor"].startsWith("{action}")).toBe(true);
  });
  it("Verouderd heet in en-US Stale (Peil §11.1) en noemt net als in het Nederlands de reden en wat je kunt doen", () => {
    expect([nl["state.staleShort"], en["state.staleShort"]]).toEqual(["Verouderd", "Stale"]);
    expect(nl["ms.why.stale"]).toMatch(/^Laatst bekend \{when\}.*Verbinding testen/);
    expect(en["ms.why.stale"]).toMatch(/^Last known \{when\}.*Test connection/);
    // de knop waar de reden naar verwijst, heet in beide talen precies zo
    expect([nl["ms.test"], en["ms.test"]]).toEqual(["Verbinding testen", "Test connection"]);
  });
  it("de termen van de feitenlijst (dl) en de hulpteksten bij het formulier bestaan in beide talen", () => {
    expect([nl["ms.url"], en["ms.url"]]).toEqual(["Adres", "Address"]);
    expect([nl["ms.fact.auth"], en["ms.fact.auth"]]).toEqual(["Aanmelden met", "Signs in with"]);
    expect([nl["ms.fact.tests"], en["ms.fact.tests"]]).toEqual(["Afspeeltests", "Playback tests"]);
    expect([nl["ms.fact.nl"], en["ms.fact.nl"]]).toEqual(["NL-ondertitels", "Dutch subtitles"]);
    expect([nl["ms.required"], en["ms.required"]]).toEqual(["(verplicht)", "(required)"]);
    // een term is geen zin: geen dubbele punt of variabele erin (de waarde staat in de dd)
    for (const key of ["ms.url", "ms.fact.auth", "ms.fact.tests", "ms.fact.nl", "ms.paths"] as const) {
      for (const taal of [nl, en]) expect(taal[key], key).not.toMatch(/[:{]/);
    }
  });
});
