/**
 * Veelvoorkomende Engelse meldingen van Sonarr/Radarr → gewoon Nederlands (N9, N20).
 * Onbekende teksten blijven zoals ze zijn (liever Engels dan een verkeerde vertaling).
 */
import type { Locale } from "@/lib/i18n";

const RULES: Array<[RegExp, string]> = [
  [/^(.+?) is not wanted in profile$/i, "$1 staat niet in het kwaliteitsprofiel"],
  [/^Existing file on disk meets quality cutoff:?\s*(.*)$/i, "Het bestand op schijf haalt de kwaliteitsgrens al ($1)"],
  [/^Existing file on disk is of equal or higher preference:?\s*(.*)$/i, "Het bestand op schijf is even goed of beter ($1)"],
  [/^Existing file meets cutoff:?\s*(.*)$/i, "Het bestaande bestand haalt de kwaliteitsgrens al ($1)"],
  [/^([\d.,]+\s*[KMGT]?B) is larger than maximum allowed ([\d.,]+\s*[KMGT]?B).*$/i, "$1 is groter dan het maximum van $2"],
  [/^([\d.,]+\s*[KMGT]?B) is smaller than minimum allowed ([\d.,]+\s*[KMGT]?B).*$/i, "$1 is kleiner dan het minimum van $2"],
  [/^BR-DISK is not wanted.*$/i, "Volledige Blu-ray-schijf (BR-DISK): kan niet worden geïmporteerd"],
  [/^Custom Formats? .*score of (-?\d+) is below.*minimum.*?(-?\d+).*$/i, "Score $1 ligt onder het minimum van $2"],
  [/^Release is (?:not )?in the required languages?.*$/i, "Verkeerde taal voor dit profiel"],
  [/^Language is not wanted.*$/i, "Taal staat niet in het profiel"],
  [/^Not a preferred word upgrade.*$/i, "Geen verbetering ten opzichte van het huidige bestand"],
  [/^Not an upgrade for existing (?:episode|movie) file\(?s?\)?.*$/i, "Geen verbetering ten opzichte van het huidige bestand"],
  [/^Release is blocklisted.*$/i, "Release staat op de blokkeerlijst"],
  [/^Release in queue already meets cutoff.*$/i, "Er staat al een goede versie in de downloadwachtrij"],
  [/^Release in queue is of equal or higher preference.*$/i, "Er staat al een even goede versie in de downloadwachtrij"],
  [/^(?:Unknown|Single) episode.*not found.*$/i, "Aflevering niet herkend"],
  [/^Episode wasn't aired yet.*$/i, "Aflevering is nog niet uitgezonden"],
  [/^Not enough seeders.*$/i, "Te weinig seeders"],
  [/^Older than maximum age.*$/i, "Ouder dan de toegestane retentie"],
  [/^Indexer.*disabled.*$/i, "Zoekbron is uitgeschakeld"],
  [/^Found matching (?:movie|series) via grab history, but release was matched to (?:movie|series) by ID\.?\s*Manual Import required\.?$/i,
    "Herkend via de download-ID, maar Sonarr/Radarr wil dat de import wordt bevestigd (handmatige import)."],
  [/^Manual Import required\.?$/i, "Handmatige import nodig"],
  [/^(.*?meldt:\s*)?Category does not exist\.?$/i, "Categorie bestaat niet in SABnzbd — MediaGuard maakt hem automatisch aan"],
  [/^No files found are eligible for import.*$/i, "Geen bestanden gevonden die geïmporteerd kunnen worden"],
  [/^One or more episodes expected in this release were not imported or missing.*$/i, "Niet alle verwachte afleveringen uit deze download zijn geïmporteerd"],
  [/^Not a Custom Format upgrade.*$/i, "Geen verbetering in custom formats"],
  [/^Sample$/i, "Voorbeeldfragment (sample)"],
  [/^Unable to parse.*$/i, "Bestandsnaam niet te herkennen"],
  [/^Missing root folder for movie collection:\s*(\S.*?)\s*\((.+)\)\s*$/i, "Map van filmcollecties bestaat niet meer: $1"],
];

export function arrMessage(text: string | null | undefined, locale: Locale = "nl-NL"): string {
  if (!text) return "";
  if (locale === "en-US") return text;
  const trimmed = text.trim();
  for (const [re, nl] of RULES) if (re.test(trimmed)) return trimmed.replace(re, nl);
  return trimmed;
}
