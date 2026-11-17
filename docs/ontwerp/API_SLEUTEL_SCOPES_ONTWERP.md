# API-sleutels met rechten (scopes): ontwerp vóór de bouw (S-P3-600)

**Status:** ontwerp vóór de bouw, 17 november 2026. Er is nog niets van gebouwd. Code-HEAD bij dit ontwerp: `fe67e8b`.
**Scherm:** `apps/web/app/(app)/instellingen/api-sleutels/page.tsx` (`ApiKeysPage`). Teksten staan in
`apps/web/lib/i18n/messages/apikeys.ts` (nl) en `apps/web/lib/i18n/messages/en/apikeys.ts` (en-US).
**Ontwerptaal:** Peil 1.1.1, de gepinde kopie (ONTWERPTAAL §13.3, `design/ontwerptaal/`). De onderdelen komen uit
`apps/web/components/mg/primitives.tsx` en `components/mg/domain.tsx`.
**Besluit van de regisseur (ligt vast):** scopes kunnen alleen op rol `viewer`. Operator + scopes wordt bij het aanmaken
geweigerd met een Nederlandse fout. `NULL` of geen scopes betekent het oude gedrag. De enige scope in deze stap is
`emergency_stop:on`. `wanted:search` komt er niet (BG-05 is open).
**Gebruikte skills:** `design:design-system`, `design:ux-copy`, `design:accessibility-review` en `design:design-handoff`.

> **Eerlijkheid.** Dit is een ontwerp, geen gebouwd of gemeten scherm. Contrast, doelgrootte en focus volgen hier uit
> de bestaande Peil-onderdelen. Gemeten zijn ze pas na de bouw, door R-ONTWERP (§8).

---

## 1. Doel en hoofdtaak

Hoofdtaak: een beheerder maakt een **kijker-sleutel die precies één ding extra mag**, namelijk de noodstop activeren
(voor de Hub, S-P3-110). Hij ziet daarna in de lijst welke sleutel welk recht heeft.

Wat niet verandert: de route, de kaart, de tabel, de standaardrol **Operator** (de MSO-weg), de eenmalige weergave van
de sleutel, het intrekken, de toestanden laden, fout, leeg en "alleen beheerder", en alle bestaande testids.

## 2. Gebruikte Peil-onderdelen (geen nieuw onderdeel)

| Onderdeel | Waar | Wat is nieuw aan het gebruik |
|---|---|---|
| `Field` | het nieuwe veld "Rechten (scopes)" in de dialoog "Nieuwe sleutel" | `htmlFor="apikey-scopes"`, met hint. Dezelfde vorm als het bestaande veld Rol (`htmlFor="apikey-role"` op een `div`) |
| `Switch` | één schakelaar per bekende scope (nu alleen `emergency_stop:on`) | `id`, `label` en `describedBy` (zie §5) |
| `Segmented` | rol (bestaand) | ongewijzigd; alleen de hint eronder hangt af van de gekozen rechten (§4) |
| `InlineState` | de bestaande foutmelding bovenaan de dialoog (`tone="danger" role="alert"`) | toont ook de twee nieuwe serverfouten (§3.3) |
| `StatusChip` | de tabelcel Rol: een label per scope onder het rollabel | `tone="neutral" outline` met icoon `OctagonAlert`; onbekende scope `tone="unknown"` |
| `DangerDialog` (`facts`) | de intrekdialoog | één extra feit "Rechten (scopes)" als de sleutel scopes heeft |
| `Modal`, `Card`, `ResourceView`, `StateView` | bestaand | ongewijzigd |

Icoon: `OctagonAlert` (lucide). Dat is hetzelfde icoon als de noodstopknop in de schil (`components/shell/app-shell.tsx`),
zodat hetzelfde begrip overal hetzelfde icoon heeft (Peil §1, regel 8). Het is geen statusicoon: het label is neutraal.
Er komen geen eigen kleuren, maten of inline styles bij. Gebruik bestaande klassen en tokens: `flex`, `items-start`,
`justify-between`, `gap-3`, `min-w-0`, `text-sm`, `font-semibold`, `text-muted-foreground`, `mono`, `cell-sub`.

**Peil-wijziging nodig: nee.**

## 3. Dialoog "Nieuwe sleutel": opbouw en toestanden

### 3.1 Volgorde van de velden

1. Naam (bestaand)
2. Rol: `Segmented` Operator | Kijker (bestaand; standaard Operator)
3. **Rechten (scopes)**: alleen in de DOM als de rol `viewer` is (§3.2)
4. Vervaldatum (optioneel, bestaand)

Voet: Annuleren · **Sleutel aanmaken** (bestaand).

Bij rol Operator staat het veld er niet: geen uitgeschakelde schakelaar en geen extra tekst. Zo blijft de MSO-weg
precies zoals hij was. Dat het recht alleen bij Kijker kan, zegt de hint van het veld zelf zodra het verschijnt. De
serverfout (§3.3) vangt de combinatie af als iemand de API rechtstreeks gebruikt.

### 3.2 Het veld (rol Kijker)

```tsx
{form.role === "viewer" ? (
  <Field label={t("apikeys.form.scopes")} htmlFor="apikey-scopes" hint={t("apikeys.form.scopesHint")}>
    <div id="apikey-scopes" role="group" aria-label={t("apikeys.form.scopes")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <label className="font-semibold" htmlFor="apikey-scope-estop">{t("apikeys.scope.estopOn")}</label>
          <div className="cell-sub" id="apikey-scope-estop-hint">
            {t("apikeys.scope.estopOnHint")} <code className="mono">emergency_stop:on</code>
          </div>
        </div>
        <Switch id="apikey-scope-estop" checked={form.scopes.includes("emergency_stop:on")}
          label={t("apikeys.scope.estopOn")} describedBy="apikey-scope-estop-hint"
          onChange={(on) => setForm({ ...form, scopes: on ? ["emergency_stop:on"] : [] })} />
      </div>
    </div>
  </Field>
) : null}
```

Formulierstand: `form.scopes: Scope[]`, met `type Scope = "emergency_stop:on"`. De lijst bekende scopes staat op één
plek in de pagina (`KNOWN_SCOPES`). Een tweede scope wordt later een tweede rij in dezelfde groep.

| Toestand | Wat de gebruiker ziet | Gedrag |
|---|---|---|
| **Rol Operator** (standaard) | geen veld | `scopes` wordt niet verstuurd |
| **Kijker, leeg** (standaard bij Kijker) | schakelaar uit; rolhint `apikeys.form.hintViewer` (ongewijzigd) | `scopes` wordt niet verstuurd, dus `NULL`: een gewone kijker-sleutel zoals nu |
| **Kijker, gekozen** | schakelaar aan; de rolhint wordt `apikeys.form.hintViewerScoped` | body krijgt `"scopes": ["emergency_stop:on"]` |
| **Terug naar Operator** | veld verdwijnt | `form.scopes` wordt leeggemaakt (`[]`), niet alleen verborgen. Een operator-verzoek bevat dus nooit scopes |
| **Weer naar Kijker** | schakelaar staat uit | er wordt niets onthouden: minste rechten |
| **Dialoog opnieuw open** | rol Operator, scopes leeg | `open()` zet `scopes: []` |
| **Bezig** | knop "Sleutel aanmaken" met `pending` (bestaand) | — |

De schakelaar staat standaard uit. Peil schrijft "ja/nee zonder standaard" voor bij verplichte vragen in een
`SchemaFormulier`. Dit is geen verplichte vraag maar een optioneel extra recht, en uit betekent het oude gedrag met de
minste rechten. Dat is fail-closed, net als elke andere `Switch` in MediaGuard.

Versturen: `scopes` alleen meesturen als `role === "viewer"` én de lijst niet leeg is. Een lege lijst gaat nooit mee.
Voor A-BOUW: de server behandelt een binnenkomende `[]` als `NULL`.

### 3.3 Fouten

Beide fouten komen van de server. Het scherm kan ze zelf niet veroorzaken: bij Operator is er geen veld en de schakelaar
kent alleen bekende scopes. Ze verschijnen dus alleen bij een rechtstreeks API-verzoek of een verschil tussen web- en
coreversie. Ze gebruiken de bestaande foutweg: `InlineState tone="danger" role="alert"` bovenaan de dialoog, met de
tekst van de server letterlijk (`ApiError.message`). De dialoog blijft open en de invoer blijft staan.

| Fout | HTTP | `detail` (een string, letterlijk) |
|---|---|---|
| onbekende scope (ook `wanted:search`) | 422 | `Onbekend recht (scope) '<scope>'. Toegestaan: emergency_stop:on.` |
| operator + scopes | 422 | `Rechten (scopes) kunnen alleen op een sleutel met rol kijker. Kies rol kijker of laat de rechten leeg.` |

Eis voor A-BOUW: `detail` is een Nederlandse **string** en geen lijst met pydantic-fouten. `messageFrom` in
`lib/api/client.ts` toont een string letterlijk. Een pydantic-lijst wordt een Engelse of omgezette tekst met een
veldlabel. Controleer daarom zelf in `apikeys.create_key` of in de route, en niet alleen via een `Literal` in
`NewApiKey`. `<scope>` is de ontvangen waarde, ingekort tot 64 tekens. Nooit de sleutel in een foutmelding.

## 4. Teksten (exacte strings)

### 4.1 Nieuwe sleutels in `apps/web/lib/i18n/messages/apikeys.ts` en `en/apikeys.ts`

| Sleutel | nl | en-US |
|---|---|---|
| `apikeys.form.scopes` | Rechten (scopes) | Permissions (scopes) |
| `apikeys.form.scopesHint` | Optioneel. Zet alleen aan wat de koppeling echt nodig heeft. Alles uit = een gewone kijker-sleutel. | Optional. Only switch on what the integration really needs. All off = a regular viewer key. |
| `apikeys.form.hintViewerScoped` | Alleen lezen, plus precies de rechten hieronder. Verder kan deze sleutel niets laten zoeken of wijzigen. | Read only, plus exactly the permissions below. Otherwise this key cannot trigger searches or change anything. |
| `apikeys.scope.estopOn` | Noodstop activeren | Activate emergency stop |
| `apikeys.scope.estopOnHint` | Mag de noodstop aanzetten, nooit opheffen. Bedoeld voor de Hub. | May switch the emergency stop on, never off. Intended for the Hub. |
| `apikeys.scope.unknown` | Onbekend recht: {scope} | Unknown permission: {scope} |
| `apikeys.scope.unknownHint` | Deze versie van MediaGuard kent dit recht niet en staat er niets mee toe. | This version of MediaGuard does not know this permission and allows nothing with it. |

Woordkeus: "Noodstop activeren" en "opheffen" zijn de woorden van de bestaande noodstopteksten (`estop.confirm`,
`estop.release` in `common.ts`). "Kijker" en "Operator" zijn de rolwoorden uit Peil §11.1. "Hub" is de naam die
MediaGuard al gebruikt (`hub.back`). `emergency_stop:on` staat alleen als code in `mono` naast de tekst en nooit als
enige uitleg.

### 4.2 Bestaande strings: ongewijzigd

`apikeys.form.hintViewer` ("Alleen lezen; kan niets laten zoeken of wijzigen.") blijft. Die tekst geldt voor Kijker
zonder rechten. `apikeys.form.hintOperator`, `apikeys.sub`, `adv.apiKeysSub` en `apikeys.may` ("Mag: {what}",
`r035.ts`) blijven ook.

### 4.3 Servertekst `permissions_nl` (in `apikeys.list_keys`, voor A-BOUW)

| Sleutel | `permissions_nl` |
|---|---|
| viewer, scopes `NULL` | `Alleen lezen` (ongewijzigd) |
| operator | ongewijzigd |
| viewer + `emergency_stop:on` | `Alleen lezen, plus de noodstop activeren (nooit opheffen); verder niets` |

## 5. Sleutellijst en intrekdialoog

**Tabel (`apikey-table`), cel Rol:** eerst het bestaande rollabel (`StatusChip` Kijker/Operator). Heeft de sleutel
scopes (geen `null` en niet leeg), dan komt daaronder per scope een label, in een rij die mag omlopen
(`flex flex-wrap gap-1`, `data-testid="apikey-scopes-<naam>"`):

- bekende scope: `StatusChip tone="neutral" outline icon={OctagonAlert}`, tekst `apikeys.scope.estopOn`;
- onbekende scope (de core kent hem, deze web-versie niet, of omgekeerd): `StatusChip tone="unknown"`, tekst
  `apikeys.scope.unknown` met de code. Daaronder komt één `cell-sub` met `apikeys.scope.unknownHint`. Peil §4.3:
  onbekend nooit groen, altijd met reden. De code wordt nooit weggelaten.

Sleutels zonder scopes zien er precies zo uit als nu: geen extra regel en geen leeg label. De regel "Mag: …" in de
naamcel toont de servertekst uit §4.3. Een ingetrokken of verlopen sleutel houdt zijn labels, want die zijn
geschiedenis. Het statuslabel zegt al dat hij niets meer mag.

Type: `ApiKeyRow.scopes?: string[] | null`. Dit is bewust `string[]` en niet `Scope[]`, zodat een onbekende waarde
zichtbaar blijft.

**Intrekdialoog (`DangerDialog`, `facts`):** na Rol en Begint met komt het feit
`{ label: t("apikeys.form.scopes"), value: <de labels zoals in de tabel> }`, alleen als de sleutel scopes heeft.

**Dialoog "Sleutel aangemaakt":** ongewijzigd. De rechten staan in de lijst.

**`/auth/me`:** dit scherm gebruikt het veld `scopes` niet. Het is voor afnemers zoals de Hub.

## 6. Breedtes en thema's

| Breedte | Opbouw |
|---|---|
| 320 / 360 / 390 | De dialoog is een sheet van onderen (bestaande `Modal`). De rij met recht en schakelaar blijft `flex`: de tekst neemt de rest van de breedte en loopt om, de schakelaar (44 × 44 px) blijft rechts. De code `emergency_stop:on` mag breken (`mono`, geen `nowrap`). De tabel is gestapeld (`table--stack`, cel met `data-label` "Rol"); de labels lopen om onder het rollabel. |
| 768 | De dialoog staat gecentreerd (`sm:w-[min(35rem,…)]`), de rij ongewijzigd. De tabel is een echte tabel; de scopelabels staan onder het rollabel in dezelfde cel. |
| 1440 | Als 768. |

Licht en donker: alleen tokens via de bestaande onderdelen. `neutral outline` en `unknown` zijn in beide thema's
Peil-tonen. Geen opacity en geen eigen kleur.

## 7. Toegankelijkheid

- **Namen.** Schakelaar: `role="switch"` met `aria-label` "Noodstop activeren", gelijk aan de zichtbare
  `<label for>`-tekst (WCAG 2.5.3). `aria-describedby` wijst naar `apikey-scope-estop-hint`, dus de uitleg en de code
  worden voorgelezen. De groep: `role="group"` met `aria-label` "Rechten (scopes)". `Field` geeft de groep
  `aria-describedby="apikey-scopes-hint"`. Geen `aria-labelledby` op een `div` zonder rol (Peil COMPONENTEN §29.3).
- **Toetsenbord.** Tab-volgorde in de dialoog: Naam → Operator → Kijker → (bij Kijker) Noodstop activeren →
  Vervaldatum → Annuleren → Sleutel aanmaken. Spatie en Enter schakelen (het is een `button`). Een klik op het label
  schakelt ook. Kijker kiezen verplaatst de focus niet: het veld verschijnt ná de rolknoppen en de volgende Tab komt
  erop. Terug naar Operator haalt het veld weg terwijl de focus op de Operator-knop staat, dus de focus gaat nooit
  verloren. Esc sluit de dialoog en de focus gaat terug naar "Nieuwe sleutel" (bestaand gedrag van `Modal`).
- **Focus.** De tweekleurige Peil-ring (`focus-ring` in `Switch`), nooit bedekt (scroll-padding van de aliaslaag).
- **Doelen.** De schakelaar heeft een raakvlak van 44 × 44 px (`min-h-touch min-w-touch`), de rolknoppen 44 px
  (bestaand). Tussen de schakelaar en de tekst zit minstens 8 px (`gap-3` = 12 px).
- **Niet alleen kleur.** Aan of uit zit in `aria-checked` en in de positie van de knop. Een scopelabel heeft altijd
  tekst plus icoon. Onbekend heeft een stippelrand, `circle-help` en het woord "Onbekend".
- **Fout.** `role="alert"` alleen op de melding van dít verzoek (bestaand). Er komt geen veldfout op de schakelaar:
  het scherm kan de fout zelf niet veroorzaken (§3.3).
- **Contrast.** Alleen Peil-tonen: tekst ≥ 7:1, rand en schakelaar ≥ 3:1. R-ONTWERP meet dit na de bouw.

## 8. Meetpunten voor R-ONTWERP en A-BOUW (e2e)

Breedtes 320, 390, 768 en 1440, licht en donker, met testgegevens:

1. Rol Operator: er is geen element met id `apikey-scope-estop` in de DOM. De bestaande MSO-test blijft ongewijzigd groen.
2. Rol Kijker: de schakelaar is zichtbaar met `role=switch` en naam "Noodstop activeren", `aria-checked="false"`, en
   de beschrijving bevat "nooit opheffen".
3. Aanzetten, dan terug naar Operator, dan weer Kijker: de schakelaar staat uit. Aanmaken als Operator stuurt geen
   `scopes` mee (te zien aan de lijst: geen scopelabel).
4. Kijker plus recht aanmaken: in de lijst staan bij die naam "Kijker" en "Noodstop activeren" (`apikey-scopes-<naam>`),
   en de regel "Mag:" bevat "nooit opheffen".
5. Kijker zonder recht: de rij ziet eruit als nu, zonder `apikey-scopes-<naam>`.
6. API rechtstreeks, met de fouttekst letterlijk in de response: `role: "operator"` + `scopes` geeft 422 met de tekst
   uit §3.3; een onbekende scope en `wanted:search` geven 422 met "Onbekend recht (scope)".
7. Intrekdialoog van de gescopete sleutel: feit "Rechten (scopes)" met het label.
8. axe (Peil-set) = 0 met de dialoog open op Kijker; geen doel < 44 px; geen horizontale overloop op 320 px; geen
   tekst < 14 px in de nieuwe rij; een volledige Tab-doorloop op 390 en 1440 met een zichtbare ring op de schakelaar.
9. Forced colors: de schakelaar is te onderscheiden; het onbekend-label houdt zijn stippelrand.

Niet te meten zonder echte omgeving: een echte schermlezer (NVDA of TalkBack) en een echte Hub-sleutel op de echte NAS.
**NIET GEVALIDEERD (echte NAS)** (STAPPEN_P6, N-P6-05).

## 9. Open punten voor de bouw

- Een onbekende scope in de database (na een terug- of opwaardering) moet in de core niets toestaan, dus fail-closed.
  Het label in §5 belooft dat ("staat er niets mee toe"). Dekt de core het niet, dan klopt `unknownHint` niet.
- `scripts/platform-v2-routes.py` en de OpenAPI-export veranderen door `NewApiKey.scopes` en `/auth/me`. Dat valt
  buiten dit scherm (zie het dossier).
