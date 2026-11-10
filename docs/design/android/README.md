# Android-app — schermafdrukken (verplaatst)

Sinds de omzetting naar de ontwerptaal **Peil 1.1.0** (stap 4, 9 november 2026) staan de schermafdrukken van
`apps/android` hier niet meer:

- **Na Peil:** [`docs/screenshots/peil/android/`](../../screenshots/peil/android/README.md) — 18 schermen en toestanden
  × 360 dp / 393 dp / 393 dp-volledig × licht/donker, plus grote letter (1,3×).
- **Vóór Peil (nulmeting):** [`docs/screenshots/peil/voor/android/`](../../screenshots/peil/voor/android/).
- **Referentieset voor de test:** `apps/android/app/src/test/snapshots/images/` (Paparazzi; CI vergelijkt met
  `:app:verifyPaparazziDebug`).

De vroegere afdrukken in deze map (stijl `mg.css`, Inter) zijn verwijderd: ze zijn gelijk aan de nulmeting in
`docs/screenshots/peil/voor/android/`. Zie [ADR-0018](../../adr/ADR-0018-peil-android-bouwkeuzes.md).
