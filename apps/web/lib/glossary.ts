/**
 * Woordenlijst voor 'Hoe werkt MediaGuard' en de <Term>-uitleg op de pagina's (rapport 5, M10).
 * Eén bron: de uitlegpagina en de tooltips tonen exact dezelfde tekst.
 */
import type { Locale } from "@/lib/i18n";

export type TermKey =
  | "vrijgavecontrole" | "overdracht" | "backfill" | "baan" | "quarantaine" | "prullenbak"
  | "herstelbundel" | "schaduwfase" | "autopilot" | "ondergrens" | "vmaf" | "aanvraag"
  | "identiteit" | "migratie";

interface Entry { term: string; short: string; long: string }

const NL: Record<TermKey, Entry> = {
  aanvraag: {
    term: "Aanvraag",
    short: "Een serie of film die je in Seerr hebt aangevraagd of zelf hebt toegevoegd.",
    long: "MediaGuard volgt elke aanvraag tot hij volgens jouw eisen in de bibliotheek staat: zoeken, downloaden, importeren, controleren en zo nodig omzetten naar AV1. Bij twijfel beslist de AI binnen je eisen.",
  },
  backfill: {
    term: "Backfill",
    short: "Rustig bijzoeken van ontbrekende afleveringen en betere versies, binnen een dagquotum.",
    long: "In plaats van alles in één keer te zoeken (wat indexers blokkeren), zoekt MediaGuard elke dag een begrensd aantal ontbrekende of verbeterbare items. Zo raakt je bibliotheek vanzelf compleet zonder dat je indexers overbelast.",
  },
  baan: {
    term: "Baan",
    short: "Een rij waarop encodes draaien: de CPU-baan (processor) of de GPU-baan (videokaart).",
    long: "Elke baan heeft een aantal plekken. Bestanden die de videokaart goed kan omzetten gaan naar de GPU-baan (snel), de rest naar de CPU-baan (langzamer, maar geschikt voor Dolby Vision en HDR10+). Staat een baan stil terwijl er werk wacht, dan staat de reden erbij.",
  },
  overdracht: {
    term: "Overdracht",
    short: "Het moment waarop de gecontroleerde AV1-versie het origineel in je bibliotheek vervangt.",
    long: "Pas na een geslaagde vrijgavecontrole zet MediaGuard het nieuwe bestand op de plek van het origineel en laat Sonarr/Radarr het opnieuw inlezen. Het origineel gaat naar de quarantaine (of wordt direct verwijderd als je dat zo hebt ingesteld).",
  },
  vrijgavecontrole: {
    term: "Vrijgavecontrole",
    short: "De controle vóór de overdracht: zitten alle sporen, de duur, HDR/Dolby Vision en de kwaliteit goed?",
    long: "MediaGuard vergelijkt de AV1-versie met het origineel: beeld (resolutie, HDR/Dolby Vision), audio- en ondertitelsporen volgens je beleid, de speelduur en de minimale kwaliteit. Klopt er iets niet, dan blijft het origineel staan. Mislukt de controle toch na de overdracht, dan wordt het origineel automatisch teruggezet.",
  },
  quarantaine: {
    term: "Quarantaine",
    short: "Een tijdelijke bewaarplek voor vervangen originelen, zodat je ze kunt terugzetten.",
    long: "Na een overdracht blijft het origineel een tijd in de quarantaine staan. Gaat er later iets mis, dan zet MediaGuard het met één tik terug. Na de bewaartermijn wordt het opgeruimd.",
  },
  prullenbak: {
    term: "Prullenbak",
    short: "Opgeruimde downloads blijven eerst 7 dagen hier staan voordat ze echt weg zijn.",
    long: "De opruimer verplaatst downloads die niemand meer nodig heeft (geïmporteerd, mislukt, verweesd of klaar met seeden) naar een prullenbak op dezelfde schijf. Na 7 dagen worden ze definitief verwijderd; bij een bijna volle schijf eerder, oudste eerst.",
  },
  herstelbundel: {
    term: "Herstelbundel",
    short: "Een versleutelde back-up van alle MediaGuard-instellingen om na een crash alles terug te zetten.",
    long: "De herstelbundel bevat database, instellingen en geheimen, beveiligd met jouw herstelcode. Bewaar er ook een kopie buiten de NAS.",
  },
  schaduwfase: {
    term: "Schaduwfase",
    short: "Tijdens de migratie draait de nieuwe installatie mee zonder iets te downloaden.",
    long: "Je oude Sonarr/Radarr blijven leidend. De nieuwe instanties kijken mee en worden vergeleken, zodat je pas overschakelt als alles klopt.",
  },
  autopilot: {
    term: "Autopilot",
    short: "Het deel dat zelf bepaalt welke bestanden naar AV1 gaan en wanneer.",
    long: "De autopilot kiest bestanden die winst opleveren, houdt de banen bezig zonder je systeem te overbelasten, en slaat bestanden over die al AV1 zijn of te weinig besparen. Staat hij langer dan een uur stil terwijl er werk is, dan meldt MediaGuard dat.",
  },
  ondergrens: {
    term: "Kwaliteitsondergrens",
    short: "De minimale bitrate per resolutie; daaronder blijft het origineel staan.",
    long: "Een AV1-versie mag niet te klein worden: voor 4K HDR/Dolby Vision geldt bijvoorbeeld ±8 Mbit/s, voor 1080p ±1,5 Mbit/s. Komt een encode lager uit, dan probeert de encoder het één keer met hogere kwaliteit; lukt dat niet, dan blijft het origineel.",
  },
  vmaf: {
    term: "VMAF",
    short: "Een meting (0–100) van hoeveel het omgezette beeld op het origineel lijkt.",
    long: "VMAF vergelijkt beeld voor beeld het origineel met de AV1-versie. Boven ±93 zie je doorgaans geen verschil. Waar de meting beschikbaar is, keurt MediaGuard versies onder de grens af.",
  },
  identiteit: {
    term: "Identiteitscontrole",
    short: "Controle of een bestand echt de juiste aflevering of film is.",
    long: "MediaGuard vergelijkt bestandsnaam, release, duur en kenmerken met wat er verwacht wordt. Een verkeerde aflevering wordt vervangen; bij twijfel beslist de AI.",
  },
  migratie: {
    term: "Migratie",
    short: "Het overzetten van je bestaande Sonarr/Radarr naar de door MediaGuard beheerde versies.",
    long: "Instellingen, bibliotheek en koppelingen worden via de API overgezet en vergeleken. Je oude installaties blijven draaien tot je zelf overschakelt.",
  },
};

const EN: Record<TermKey, Entry> = {
  aanvraag: { term: "Request", short: "A series or movie requested in Seerr or added yourself.", long: "MediaGuard follows each request until it is in the library meeting your requirements: search, download, import, verify and convert to AV1 where useful. When in doubt the AI decides within your requirements." },
  backfill: { term: "Backfill", short: "Gradually searching missing episodes and better versions within a daily quota.", long: "Instead of searching everything at once, MediaGuard searches a bounded number of items each day so your library completes itself without overloading indexers." },
  baan: { term: "Lane", short: "A queue where encodes run: the CPU lane or the GPU lane.", long: "Each lane has slots. Files the GPU handles well go to the GPU lane (fast); the rest go to the CPU lane (slower, suited for Dolby Vision and HDR10+). If a lane idles while work waits, the reason is shown." },
  overdracht: { term: "Handoff", short: "When the verified AV1 version replaces the original in your library.", long: "Only after a passed release check does MediaGuard put the new file in place and have Sonarr/Radarr rescan. The original goes to quarantine (or is deleted directly if configured)." },
  vrijgavecontrole: { term: "Release check", short: "The check before handoff: tracks, duration, HDR/Dolby Vision and quality.", long: "MediaGuard compares the AV1 version with the original. If anything is off the original stays. If the check fails after handoff, the original is restored automatically." },
  quarantaine: { term: "Quarantine", short: "Temporary storage for replaced originals so they can be restored.", long: "After a handoff the original stays in quarantine for a while and can be restored with one tap. After the retention period it is cleaned up." },
  prullenbak: { term: "Trash", short: "Cleaned-up downloads stay here for 7 days before they are really deleted.", long: "The cleaner moves downloads nobody needs anymore to a trash folder on the same disk. After 7 days they are deleted; earlier when the disk is nearly full." },
  herstelbundel: { term: "Recovery bundle", short: "An encrypted backup of all MediaGuard settings.", long: "Contains database, settings and secrets, protected by your recovery code. Keep a copy outside the NAS too." },
  schaduwfase: { term: "Shadow phase", short: "During migration the new installation runs alongside without downloading.", long: "Your old Sonarr/Radarr stay authoritative; the new instances are compared until everything matches." },
  autopilot: { term: "Autopilot", short: "Decides which files go to AV1 and when.", long: "It picks files that save space, keeps lanes busy without overloading the system and skips files that are already AV1 or save too little." },
  ondergrens: { term: "Quality floor", short: "Minimum bitrate per resolution; below it the original stays.", long: "An AV1 version may not get too small: e.g. ±8 Mbit/s for 4K HDR/Dolby Vision, ±1.5 Mbit/s for 1080p. Below that, one retry with higher quality; otherwise the original stays." },
  vmaf: { term: "VMAF", short: "A 0–100 score of how closely the converted picture matches the original.", long: "Above ±93 differences are usually invisible. Where available, MediaGuard rejects versions below the threshold." },
  identiteit: { term: "Identity check", short: "Checks that a file really is the right episode or movie.", long: "MediaGuard compares file name, release, runtime and traits with what is expected. Wrong files are replaced; when unsure the AI decides." },
  migratie: { term: "Migration", short: "Moving your existing Sonarr/Radarr to MediaGuard-managed versions.", long: "Settings, library and connections are copied via the API and compared. Old installs keep running until you switch over." },
};

export const TERM_ORDER: TermKey[] = [
  "aanvraag", "backfill", "identiteit", "baan", "autopilot", "ondergrens", "vmaf", "vrijgavecontrole",
  "overdracht", "quarantaine", "prullenbak", "herstelbundel", "migratie", "schaduwfase",
];

export function glossary(locale: Locale): Record<TermKey, Entry> {
  return locale.startsWith("en") ? EN : NL;
}
