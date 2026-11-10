# Migraties

Bestanden worden op naam gesorteerd en precies één keer toegepast (`schema_migrations`).
Pas een toegepast bestand nooit aan; voeg een nieuw bestand toe.

Nummerreeksen per domein (voorkomt botsingen bij parallel werk):

| Reeks | Domein |
|---|---|
| 001–019 | kern (users, secrets, settings, audit, jobs, locks) |
| 020–039 | instances, arr-cache, library, wanted, backfill, seerr, ownership |
| 040–059 | identity, audio, policy, release candidates, hooks, DV-policy |
| 060–079 | recovery, quarantaine, replacement transactions, optimalisatie, encoding-bridge |
| 080–099 | migratiewizard (bron-instanties, backups, mapping, cutover) |
| 100–119 | AI Decision Advisor |
| 120–139 | systeem: back-ups, updates, diagnostics |
