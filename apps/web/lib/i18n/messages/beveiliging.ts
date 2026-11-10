// Beveiliging (verbeterlijst 4, 13, 34): admin-only instellingen, bibliotheekslot, onleesbare
// geheimen, eigen wachtwoord wijzigen en de aansporing om 2FA aan te zetten.
export const nl = {
  "guard.adminOnly": "Alleen een beheerder kan dit wijzigen.",
  "lockctl.open": "Vrijgegeven: MediaGuard mag bestanden in je bibliotheek vervangen (originelen gaan eerst naar quarantaine).",
  "lockctl.closed": "Vergrendeld: MediaGuard vervangt geen bestanden in je bibliotheek.",
  "lockctl.lock": "Weer vergrendelen",
  "lockctl.lockTitle": "Bibliotheek weer vergrendelen?",
  "lockctl.lockBody": "MediaGuard vervangt dan geen bestanden meer in je bibliotheek tot je hem opnieuw vrijgeeft. Analyses en proefencodes gaan gewoon door.",
  "lockctl.locked": "Bibliotheek vergrendeld",
  "secrets.unreadable": "Onleesbaar",
  "secrets.mismatchTitle": "secret.key past niet bij de database",
  "secrets.mismatchBody": "{count} opgeslagen sleutel(s) kunnen niet worden ontsleuteld; de koppelingen die ze gebruiken werken niet. Zet de juiste secret.key terug in de appdata-map (bijv. uit je herstelbundel) en herstart, of vul de sleutels hieronder opnieuw in.",
  "pw.title": "Wachtwoord wijzigen",
  "pw.sub": "Andere apparaten worden daarna uitgelogd; dit apparaat blijft ingelogd.",
  "pw.current": "Huidig wachtwoord",
  "pw.new": "Nieuw wachtwoord",
  "pw.repeat": "Herhaal nieuw wachtwoord",
  "pw.mismatch": "De nieuwe wachtwoorden zijn niet gelijk.",
  "pw.save": "Wachtwoord wijzigen",
  "pw.done": "Wachtwoord gewijzigd",
  "sec.nudgeTitle": "Tip: zet tweestapsverificatie aan.",
  "sec.nudgeBody": "MediaGuard is via internet te bereiken. Met een code van je telefoon is een geraden of gelekt wachtwoord niet genoeg om in te loggen. Het blijft je keuze.",
} as const;
