/**
 * Eén vertaaltabel voor statussen en codes uit core, de AV1-engine en Sonarr/Radarr.
 * Ruwe codes horen alleen onder 'Technische details'; in de interface staat altijd gewone taal.
 */
import type { Locale } from "@/lib/i18n";

type Pair = [nl: string, en: string];

const CODES: Record<string, Pair> = {
  // taken / jobs
  running: ["bezig", "running"], queued: ["in de rij", "queued"], pending: ["wacht", "pending"],
  succeeded: ["geslaagd", "succeeded"], success: ["geslaagd", "succeeded"], completed: ["klaar", "completed"], done: ["klaar", "done"],
  failed: ["mislukt", "failed"], error: ["fout", "error"], cancelled: ["geannuleerd", "cancelled"], canceled: ["geannuleerd", "cancelled"],
  skipped: ["overgeslagen", "skipped"], paused: ["gepauzeerd", "paused"], blocked: ["geblokkeerd", "blocked"], retrying: ["wordt opnieuw geprobeerd", "retrying"],
  waiting: ["wacht", "waiting"], searching: ["zoekt", "searching"], grabbed: ["gedownload", "grabbed"], "import-failed": ["import mislukt", "import failed"], dead: ["opgegeven", "given up"], stuck: ["vastgelopen", "stuck"],
  // bibliotheek
  available: ["beschikbaar", "available"], missing: ["ontbreekt", "missing"], wanted: ["gezocht", "wanted"], upcoming: ["verwacht", "expected"],
  cutoff_unmet: ["onder kwaliteitsgrens", "below quality cutoff"], unmonitored: ["niet gevolgd", "not monitored"], downloading: ["wordt gedownload", "downloading"],
  ignored: ["genegeerd", "ignored"], unavailable: ["niet beschikbaar", "unavailable"], complete: ["compleet", "complete"],
  ended: ["afgelopen", "ended"], continuing: ["loopt nog", "continuing"], released: ["uitgebracht", "released"], announced: ["aangekondigd", "announced"],
  inCinemas: ["in de bioscoop", "in cinemas"], in_cinemas: ["in de bioscoop", "in cinemas"], deleted: ["verwijderd", "deleted"], tba: ["nog onbekend", "to be announced"],
  // ontbrekend / oorzaken
  "searched-no-grab": ["gezocht, niets gevonden", "searched, nothing found"], "niet-gediagnosticeerd": ["nog niet onderzocht", "not investigated yet"],
  undiagnosed: ["nog niet onderzocht", "not investigated yet"], "no-results": ["geen resultaten", "no results"], "all-rejected": ["alles afgewezen", "all rejected"],
  "quality-cutoff": ["onder kwaliteitsgrens", "below quality cutoff"], "language-mismatch": ["verkeerde taal", "wrong language"], "not-released": ["nog niet uit", "not released yet"],
  "import-blocked": ["import geblokkeerd", "import blocked"], "download-failed": ["download mislukt", "download failed"], "indexer-down": ["zoekbron onbereikbaar", "indexer unreachable"],
  excluded: ["uitgesloten", "excluded"], recovered: ["hersteld", "recovered"],
  // weigeringen vóór het grabben (rapport 6, N3)
  GROUP_UNRELIABLE: ["releasegroep onbetrouwbaar (vaak mislukt)", "release group unreliable (often fails)"],
  RECENTLY_GRABBED: ["zelfde release net al gedownload", "same release grabbed moments ago"],
  CATCHUP_THROTTLE: ["afgeremd na pauze: niet te veel tegelijk", "throttled after pause: not too many at once"],
  ITEM_PAUSED: ["zoekpauze voor dit item", "searching paused for this item"],
  EMERGENCY_STOP: ["noodstop actief", "emergency stop active"],
  // identiteit
  unresolved: ["nog niet gecontroleerd", "not checked yet"], verified: ["gecontroleerd", "verified"], confirmed: ["bevestigd", "confirmed"], mismatch: ["komt niet overeen", "mismatch"],
  ambiguous: ["twijfelgeval", "ambiguous"], suspect: ["verdacht", "suspect"],
  // AV1 / quarantaine
  awaiting_activation: ["wacht op plaatsing", "awaiting placement"], activated: ["geplaatst", "placed"], verification_failed: ["controle mislukt", "check failed"],
  release_check_failed: ["vrijgavecontrole mislukt", "release check failed"], verifying: ["wordt gecontroleerd", "verifying"], encoding: ["wordt omgezet", "encoding"],
  staged: ["klaargezet", "staged"], rejected: ["afgekeurd", "rejected"], accepted: ["goedgekeurd", "accepted"], rolled_back: ["teruggedraaid", "rolled back"],
  quarantined: ["in quarantaine", "quarantined"], restored: ["teruggezet", "restored"], purged: ["opgeruimd", "purged"], pinned: ["vastgezet", "pinned"],
  degraded: ["werkt beperkt", "degraded"], healthy: ["gezond", "healthy"], ok: ["gecontroleerd", "checked"], warning: ["waarschuwing", "warning"], critical: ["actie nodig", "action needed"],
  committed: ["geplaatst", "placed"], probable: ["waarschijnlijk juist", "probably correct"], likely: ["waarschijnlijk juist", "probably correct"],
  auto_identity: ["automatisch gecontroleerd", "checked automatically"], not_applicable: ["niet van toepassing", "not applicable"],
  planned: ["gepland", "planned"], scheduled: ["gepland", "scheduled"], submitted: ["aangeboden", "submitted"], kept: ["behouden", "kept"],
  true: ["aan", "on"], false: ["uit", "off"], True: ["aan", "on"], False: ["uit", "off"],
  // migratie
  "NO-GO": ["niet klaar", "not ready"], GO: ["klaar", "ready"], active: ["actief", "active"], archived: ["gearchiveerd", "archived"],
};

/** Statuscode → gewone taal. Onbekende codes: underscores/streepjes worden spaties. */
/** Eerste letter hoofdletter voor labels die los staan (chips, koppen): "uitgebracht" → "Uitgebracht" (T10). */
export function capFirst(text: string): string {
  return text ? text.charAt(0).toLocaleUpperCase("nl-NL") + text.slice(1) : text;
}

export function codeLabel(code: string | null | undefined, locale: Locale = "nl-NL"): string {
  if (code === null || code === undefined || code === "") return "—";
  const hit = CODES[code] ?? CODES[code.toLowerCase()];
  if (hit) return locale === "en-US" ? hit[1] : hit[0];
  return code.replace(/[_.-]+/g, " ").trim();
}

const INSTANCE: Record<string, Pair> = {
  "sonarr-series": ["Series", "Series"], "sonarr-anime": ["Anime", "Anime"],
  "radarr-films": ["Films", "Movies"], "radarr-movies": ["Films", "Movies"], "radarr-animefilms": ["Animefilms", "Anime movies"], "radarr-anime": ["Animefilms", "Anime movies"],
};

/** Instantie-ID (slug) → naam voor mensen ("Series", "Animefilms"). */
export function instanceLabel(id: string | null | undefined, name?: string | null, locale: Locale = "nl-NL"): string {
  if (!id) return name ?? "—";
  const hit = INSTANCE[id];
  if (hit) return locale === "en-US" ? hit[1] : hit[0];
  if (name && name !== id) return name;
  const bare = id.replace(/^(sonarr|radarr)-/, "");
  return bare.charAt(0).toUpperCase() + bare.slice(1);
}

/** "Show.Name.S03E10.1080p.WEB…" → "Show Name — S03E10" (leesbare titel uit een release-/bestandsnaam). */
export function readableRelease(name: string | null | undefined): string {
  if (!name) return "—";
  const base = name.split("/").pop()!.replace(/\.(mkv|mp4|avi|m4v|ts)$/i, "");
  const se = base.match(/^(.*?)[ ._-]*\(?(\d{4})?\)?[ ._-]*[Ss](\d{1,2})[Ee](\d{1,3})/);
  if (se) {
    const title = se[1].replace(/[._]+/g, " ").replace(/\s+-\s*$/, "").trim();
    return `${title || base} — S${se[3].padStart(2, "0")}E${se[4].padStart(2, "0")}`;
  }
  const movie = base.match(/^(.*?)[ ._(]+((?:19|20)\d{2})(?:[ ._)\]]|$)/);
  if (movie) return `${movie[1].replace(/[._]+/g, " ").trim()} (${movie[2]})`;
  return base.replace(/[._]+/g, " ").replace(/\s*\[.*$/, "").trim();
}

const EVENTS: Record<string, Pair> = {
  "library.synced": ["Bibliotheek gesynchroniseerd", "Library synced"], "arr.webhook": ["Melding van Sonarr/Radarr", "Notice from Sonarr/Radarr"],
  "job.queued": ["Taak ingepland", "Task queued"], "job.started": ["Taak gestart", "Task started"], "job.succeeded": ["Taak geslaagd", "Task succeeded"], "job.failed": ["Taak mislukt", "Task failed"],
  "encoding.submitted": ["AV1-omzetting gestart", "AV1 encode started"], "encoding.completed": ["AV1-omzetting klaar", "AV1 encode done"], "encoding.failed": ["AV1-omzetting mislukt", "AV1 encode failed"],
  "encoding.activated": ["AV1-bestand geplaatst", "AV1 file placed"], "quarantine.added": ["Origineel naar quarantaine", "Original quarantined"], "quarantine.restored": ["Origineel teruggezet", "Original restored"],
  "settings.changed": ["Instelling gewijzigd", "Setting changed"], "notification.created": ["Nieuwe melding", "New notification"], "wanted.updated": ["Ontbrekend bijgewerkt", "Missing updated"],
  // audit-acties (rapport 4, S12)
  "download.marked_failed": ["Download als mislukt gemarkeerd", "Download marked failed"], "download.refused_removed": ["Geweigerde download verwijderd en geblokkeerd", "Refused download removed and blocked"],
  "download.manual_import": ["Download zelf geïmporteerd", "Download imported"], "downloads.trashed": ["Oude download naar prullenbak", "Old download moved to trash"],
  "downloads.purged": ["Prullenbak geleegd", "Trash emptied"], "downloads.restored": ["Download teruggezet uit prullenbak", "Download restored from trash"],
  "downloads.temp_removed": ["Tijdelijk bestand opgeruimd", "Temporary file removed"], "downloads.torrent_removed": ["Torrent verwijderd (klaar met delen)", "Torrent removed (done seeding)"],
  "recovery.replace_committed": ["AV1-versie geplaatst", "AV1 version placed"], "recovery.conflict": ["Conflict bij vervangen", "Replace conflict"], "recovery.restored_missing": ["Ontbrekend origineel teruggezet", "Missing original restored"],
  "delivery.delivered": ["Aanvraag geleverd", "Request delivered"], "delivery.ai_decision": ["AI-besluit bij aanvraag", "AI decision for request"],
  "encoding.handoff": ["AV1: overdracht", "AV1: handoff"], "encoding.autopilot": ["AV1: autopilot-ronde", "AV1: autopilot round"], "encoding.submit": ["AV1-omzetting ingediend", "AV1 encode submitted"],
  "encoding.cancel": ["AV1-omzetting afgebroken", "AV1 encode cancelled"], "encoding.cancel_requested": ["Afbreken gevraagd", "Cancel requested"], "encoding.verification_failed": ["AV1-controle mislukt", "AV1 check failed"],
  "quarantine.purged": ["Quarantaine: opgeruimd", "Quarantine: purged"], "quarantine.auto_restore": ["Origineel automatisch teruggezet", "Original restored automatically"],
  "quarantine.use_quarantine": ["Quarantaine ingeschakeld", "Quarantine enabled"], "quarantine.restore_requested": ["Terugzetten gevraagd", "Restore requested"],
  "wanted.search_now": ["Ontbrekend: nu gezocht", "Missing: searched now"], "backfill.run": ["Achterstand: zoekronde", "Backfill: search round"], "backfill.enqueue": ["Achterstand: ingepland", "Backfill: queued"],
  "provisioning.qualityprofiles.update": ["Kwaliteitsprofiel bijgewerkt", "Quality profile updated"], "provisioning.customformats.create": ["Custom format aangemaakt", "Custom format created"],
  "provisioning.customformats.update": ["Custom format bijgewerkt", "Custom format updated"], "provisioning.customformats.failed": ["Custom format geweigerd", "Custom format refused"],
  "provisioning.qualitydefinitions.update": ["Bestandsgroottes bijgewerkt", "File sizes updated"],
  "identity.decision": ["Identiteit: besluit", "Identity: decision"], "identity.ai_verdict": ["Identiteit: AI-oordeel", "Identity: AI verdict"], "identity.auto_replaced": ["Verkeerd bestand automatisch vervangen", "Wrong file replaced automatically"],
  "routing.anime_moved": ["Anime naar de anime-instantie verplaatst", "Anime moved to the anime instance"], "routing.anime_reverted": ["Anime-verplaatsing teruggedraaid", "Anime move reverted"],
  "apikey.created": ["API-sleutel aangemaakt", "API key created"], "apikey.revoked": ["API-sleutel ingetrokken", "API key revoked"], "apikey.rejected": ["API-sleutel geweigerd", "API key rejected"],
  "ai.case.auto_resolved": ["AI: veilig afgehandeld", "AI: resolved safely"], "ai.benchmark.run": ["AI: benchmark", "AI: benchmark"],
  "storage.twin_removed": ["Lege tweelingmap verwijderd", "Empty twin folder removed"], "storage.typo_fixed": ["Mapnaam hersteld", "Folder name fixed"],
  "auth.login_failed": ["Mislukte inlogpoging", "Failed sign-in"], "settings.change": ["Instelling gewijzigd", "Setting changed"], "arr.removed_deleted_title": ["Verwijderde titel opgeruimd", "Deleted title cleaned up"],
  "system.emergency_stop": ["Noodstop gewijzigd", "Emergency stop changed"], "instance.status": ["Koppeling gewijzigd", "Connection changed"],
};

// veelvoorkomende woorden in actiecodes → Nederlands (terugval voor onbekende codes, S12)
const WORDS_NL: Record<string, string> = {
  created: "aangemaakt", create: "aanmaken", updated: "bijgewerkt", update: "bijwerken", deleted: "verwijderd", delete: "verwijderen", removed: "verwijderd", remove: "verwijderen",
  failed: "mislukt", succeeded: "geslaagd", completed: "voltooid", started: "gestart", requested: "gevraagd", run: "ronde", restore: "terugzetten", restored: "teruggezet",
  rollback: "terugdraaien", cancel: "afbreken", cancelled: "afgebroken", pause: "pauzeren", resume: "hervatten", search: "zoeken", now: "nu", marked: "gemarkeerd", refused: "geweigerd",
  committed: "vastgelegd", replace: "vervangen", confirmed: "bevestigd", confirm: "bevestigen", test: "test", set: "ingesteld", change: "wijziging", purged: "opgeruimd", trashed: "naar prullenbak",
  config: "configuratie", settings: "instellingen", decision: "besluit", delivered: "geleverd", import: "import", manual: "handmatig", accept: "accepteren", mismatch: "afwijking", blocked: "geblokkeerd",
  cutover: "overschakelen", source: "bron", upload: "uploaden", mapping: "koppeling", step: "stap", options: "opties", enable: "aanzetten", enabled: "aangezet", disable: "uitzetten", stuck: "vastgelopen",
};
function humanizeNl(code: string): string {
  const words = code.split(/[._-]+/).filter(Boolean).map((w) => WORDS_NL[w.toLowerCase()] ?? w);
  const text = words.join(" ");
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "—";
}

/** Heeft de web-app een eigen vertaling voor deze code? */
export function hasEventLabel(topic: string | null | undefined): boolean {
  return Boolean(topic && EVENTS[topic]);
}

/** Gebeurteniscode ("library.synced") → gewone taal. */
export function eventLabel(topic: string | null | undefined, locale: Locale = "nl-NL"): string {
  if (!topic) return "—";
  const hit = EVENTS[topic];
  if (hit) return locale === "en-US" ? hit[1] : hit[0];
  const [area, ...rest] = topic.split(".");
  const AREA: Record<string, Pair> = { library: ["Bibliotheek", "Library"], job: ["Taak", "Task"], encoding: ["AV1", "AV1"], quarantine: ["Quarantaine", "Quarantine"], wanted: ["Ontbrekend", "Missing"], instance: ["Koppeling", "Connection"], settings: ["Instellingen", "Settings"], system: ["Systeem", "System"], arr: ["Sonarr/Radarr", "Sonarr/Radarr"],
    download: ["Download", "Download"], downloads: ["Downloads", "Downloads"], recovery: ["Vervangen", "Replace"], delivery: ["Aanvraag", "Request"], provisioning: ["Inrichting", "Provisioning"], migration: ["Migratie", "Migration"],
    ai: ["AI", "AI"], apikey: ["API-sleutel", "API key"], storage: ["Opslag", "Storage"], optimization: ["Optimalisatie", "Optimisation"], seerr: ["Seerr", "Seerr"], identity: ["Identiteit", "Identity"], routing: ["Anime-routering", "Anime routing"],
    auth: ["Inloggen", "Sign-in"], dv: ["Dolby Vision", "Dolby Vision"], backup: ["Back-up", "Backup"], backfill: ["Achterstand", "Backfill"], policy: ["Beleid", "Policy"], user: ["Gebruiker", "User"], secret: ["Geheim", "Secret"], diagnostics: ["Diagnose", "Diagnostics"], imports: ["Imports", "Imports"], ownership: ["Eigenaarschap", "Ownership"] };
  const a = AREA[area];
  if (!a) return codeLabel(topic, locale);
  const restText = locale === "en-US" ? codeLabel(rest.join("."), locale) : humanizeNl(rest.join("."));
  return `${locale === "en-US" ? a[1] : a[0]}: ${restText}`;
}


// ISO 639-2 → taalnaam (meest voorkomende talen in ondertitel- en audiosporen)
const LANG: Record<string, Pair> = {
  nld: ["Nederlands", "Dutch"], dut: ["Nederlands", "Dutch"], eng: ["Engels", "English"], jpn: ["Japans", "Japanese"], ger: ["Duits", "German"], deu: ["Duits", "German"],
  fre: ["Frans", "French"], fra: ["Frans", "French"], spa: ["Spaans", "Spanish"], ita: ["Italiaans", "Italian"], por: ["Portugees", "Portuguese"], pol: ["Pools", "Polish"],
  rus: ["Russisch", "Russian"], chi: ["Chinees", "Chinese"], zho: ["Chinees", "Chinese"], ara: ["Arabisch", "Arabic"], fil: ["Filipijns", "Filipino"], kor: ["Koreaans", "Korean"],
  swe: ["Zweeds", "Swedish"], dan: ["Deens", "Danish"], nor: ["Noors", "Norwegian"], fin: ["Fins", "Finnish"], tur: ["Turks", "Turkish"], hin: ["Hindi", "Hindi"],
  tha: ["Thai", "Thai"], vie: ["Vietnamees", "Vietnamese"], ind: ["Indonesisch", "Indonesian"], may: ["Maleis", "Malay"], heb: ["Hebreeuws", "Hebrew"], gre: ["Grieks", "Greek"],
  cze: ["Tsjechisch", "Czech"], hun: ["Hongaars", "Hungarian"], rum: ["Roemeens", "Romanian"], ukr: ["Oekraïens", "Ukrainian"], und: ["onbekende taal", "unknown language"],
};
export function languageName(code: string, locale: Locale = "nl-NL"): string {
  const hit = LANG[code.toLowerCase()];
  return hit ? (locale === "en-US" ? hit[1] : hit[0]) : code;
}

/** Ruwe controletekst ("ondertitels: ontbreekt: {'ara': 1, 'ger': 2}") → "Ondertitels ontbreken: Arabisch, Duits (2)". */
export function checkDetailText(detail: string | null | undefined, locale: Locale = "nl-NL"): string {
  if (!detail) return "";
  const dict = /\{([^{}]*)\}/.exec(detail);
  if (!dict) return detail.replace(/(\d)\.(\d)/g, locale === "en-US" ? "$1.$2" : "$1,$2");
  const pairs = [...dict[1].matchAll(/'([a-z]{2,3})'\s*:\s*(\d+)/gi)].map(([, code, n]) => `${languageName(code, locale)}${Number(n) > 1 ? ` (${n})` : ""}`);
  const head = detail.slice(0, dict.index).replace(/[:\s]+$/, "");
  const kind = /ondertitel|subtit/i.test(head) ? (locale === "en-US" ? "Missing subtitles" : "Ondertitels ontbreken")
    : /audio/i.test(head) ? (locale === "en-US" ? "Missing audio tracks" : "Audiosporen ontbreken") : head;
  return `${kind}: ${pairs.join(", ")}`;
}

/** ISO-tijdstip in tekst → "4 nov 13:01" (R6). */
function isoToLocal(s: string, locale: Locale): string {
  return s.replace(/\b(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\b/g, (m) => {
    const d = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(m) ? m : `${m}Z`);
    if (Number.isNaN(d.valueOf())) return m;
    return `${d.toLocaleDateString(locale, { day: "numeric", month: "short", timeZone: "Europe/Amsterdam" }).replace(".", "")} ${d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Amsterdam" })}`;
  });
}

/** Core-/engine-tekst leesbaar maken: True/False → aan/uit, 0.1% → 0,1 %, ruwe dicts → talen (N22). */
export function plainText(text: string | null | undefined, locale: Locale = "nl-NL"): string {
  if (text === null || text === undefined) return "";
  let s = isoToLocal(String(text), locale);
  if (/\{[^{}]*'[a-z]{2,3}'\s*:/i.test(s)) s = checkDetailText(s, locale);
  // MaxCLL/MaxFALL 0,0 betekent: niet aanwezig in de bron
  s = s.replace(/(MaxCLL(?:\s*\/\s*MaxFALL)?)\s*[:=]?\s*0\s*[,/]\s*0\b/gi, locale === "en-US" ? "$1: not present" : "$1: niet aanwezig");
  if (locale === "en-US") return s.replace(/\bTrue\b/g, "on").replace(/\bFalse\b/g, "off");
  return s.replace(/\bTrue\b/g, "aan").replace(/\bFalse\b/g, "uit")
    .replace(/(\d)\.(\d+)\s?%/g, "$1,$2 %")
    .replace(/\bprobable\b/g, "waarschijnlijk juist").replace(/\bcommitted\b/g, "geplaatst");
}

/** Oorzaak van een mislukte import/download zonder releasenaam of datum, om te kunnen groeperen (R6). */
export function failureCause(message: string | null | undefined): string {
  if (!message) return "";
  return message
    .replace(/\([^)]*\)/g, "")
    .replace(/['"‘’“”][^'"‘’“”]{6,}['"‘’“”]/g, "")
    .replace(/\b\d{4}-\d{2}-\d{2}T[\d:.]+Z?\b/g, "")
    .replace(/\s{2,}/g, " ").replace(/\s+([,.;:])/g, "$1").trim();
}

// Sonarr/Radarr geven taalnamen in het Engels ("English", "Chinese"): toon ze in het Nederlands (R20)
const ARR_LANG: Record<string, string> = {
  english: "Engels", dutch: "Nederlands", flemish: "Vlaams", german: "Duits", french: "Frans", spanish: "Spaans", "spanish (latino)": "Spaans (Latijns-Amerika)",
  italian: "Italiaans", japanese: "Japans", chinese: "Chinees", korean: "Koreaans", russian: "Russisch", portuguese: "Portugees",
  "portuguese (brazil)": "Portugees (Brazilië)", polish: "Pools", danish: "Deens", swedish: "Zweeds", norwegian: "Noors", finnish: "Fins",
  turkish: "Turks", arabic: "Arabisch", hindi: "Hindi", thai: "Thai", vietnamese: "Vietnamees", czech: "Tsjechisch", hungarian: "Hongaars",
  greek: "Grieks", hebrew: "Hebreeuws", romanian: "Roemeens", ukrainian: "Oekraïens", indonesian: "Indonesisch", malay: "Maleis",
  icelandic: "IJslands", bulgarian: "Bulgaars", lithuanian: "Litouws", latvian: "Lets", estonian: "Ests", slovak: "Slowaaks", slovenian: "Sloveens",
  croatian: "Kroatisch", serbian: "Servisch", catalan: "Catalaans", persian: "Perzisch", tamil: "Tamil", telugu: "Telugu", bengali: "Bengaals",
  filipino: "Filipijns", tagalog: "Tagalog", original: "Originele taal", unknown: "Onbekend", any: "Elke taal", multi: "Meerdere talen",
};
export function arrLanguageName(name: string, locale: Locale = "nl-NL"): string {
  if (locale === "en-US") return name;
  return ARR_LANG[name.trim().toLowerCase()] ?? (/^[a-z]{3}$/i.test(name) ? languageName(name, locale) : name);
}

/** Wie deed het: "apikey:mso" → "Media Source Optimizer (API-sleutel)", "svc:…" → "MediaGuard" (S17). */
export function actorLabel(actor: string | null | undefined, locale: Locale = "nl-NL"): string {
  if (!actor) return "—";
  const en = locale === "en-US";
  if (actor.startsWith("apikey:")) {
    const name = actor.slice(7);
    const app = /^mso$|source.?optimizer/i.test(name) ? "Media Source Optimizer" : name;
    return en ? `${app} (API key)` : `${app} (API-sleutel)`;
  }
  if (actor.startsWith("ai:")) return en ? "AI advisor" : "AI-adviseur";
  if (actor.startsWith("svc:") || actor === "system" || actor === "mediaguard") return "MediaGuard";
  if (actor.startsWith("hook:")) return en ? `Notice from ${actor.slice(5)}` : `Melding van ${actor.slice(5)}`;
  return actor;
}
