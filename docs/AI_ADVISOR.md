# AI Adviseur (AI Decision Advisor)

De AI Adviseur is een **optioneel**, budgetbegrensd hulpmiddel dat Claude (Anthropic API) of
OpenAI API alleen inzet voor gevallen die de vaste programmaregels niet eenduidig kunnen
oplossen. MediaGuard werkt 100% zonder AI. De AI is nooit de autoriteit over identiteit,
verwijderingen, taal-, HDR/Dolby Vision-beleid, snapshots, encodeprofielen of cutover.

Standaard staat de AI Adviseur **uit**.

## Wanneer wordt AI gebruikt?

Alleen als een deterministische classifier van MediaGuard een expliciete
*needs_advice*-reasoncode afgeeft. Normale, eenduidige gevallen (hashmatch, bekende
mapping, gewone release-ranking, streaminspectie) roepen de AI nooit aan.

| Taaktype | Triggercodes |
|---|---|
| Onduidelijke anime-identiteit (`anime_identity`) | `CONFLICTING_EPISODE_MAP`, `ABSOLUTE_NUMBERING_AMBIGUOUS`, `ALIAS_CONFLICT`, `SPECIAL_OVA_AMBIGUOUS`, `MULTI_EPISODE_AMBIGUOUS` |
| Hardnekkig ontbrekende aflevering (`missing_diagnosis`) | `PERSISTENT_MISSING_UNEXPLAINED`, `MULTIPLE_ROOT_CAUSES`, `CANDIDATES_REJECTED_MIXED` |
| Tegenstrijdige releaseclaims (`release_explanation`) | `CONFLICTING_RELEASE_CLAIMS`, `LANGUAGE_METADATA_CONFLICT`, `UNUSUAL_SPECIAL` |
| AV1-proefencodes interpreteren (`encode_advice`, standaard uit) | `ENCODE_TRIALS_INCONCLUSIVE` |

Elke casus toont in de UI **"Waarom werd AI ingezet?"** met de triggercode.

## Wat wordt er verstuurd?

Uitsluitend een compact JSON-document met:

- casus-ID (willekeurig), taaktype en triggercode;
- toegestane acties en reasoncodes;
- lokale evidence-ID's met korte samenvatting (bv. "TheXEM: scene S02E01 = TVDB S01E13");
- geminimaliseerde casusgegevens: titels, episode-mappings, releasenamen, rejection-codes,
  indexer*namen* (zonder tokens), proefencode-metingen, niet-gevoelige logregels.

Nooit verstuurd (automatische minimalisatie vóór opslag én verzending):

- videobestanden, frames, audio, afbeeldingen of andere binaire data;
- API-sleutels, tokens, wachtwoorden, cookies (velden worden verwijderd, waarden geredigeerd);
- absolute paden (ingekort tot alleen de bestandsnaam);
- URL's met pad/querystring (alleen schema + host; lokale/private hosts worden `[lokale-host]`);
- Sonarr/Radarr-configuratie of andere configdumps (`config`, `settings`, `headers`, `env`, …).

Via **AI Adviseur → casus → Voorbeeld verzending** (`GET /api/v1/ai/preview/{case_id}`) zie
je exact het HTTP-verzoek dat naar de provider zou gaan (sleutel gemaskeerd), plus een
rapport van wat er is verwijderd of ingekort.

MediaGuard bewaart geen ruwe prompts: alleen de geminimaliseerde casus, het
gevalideerde voorstel en de kosten. Casussen worden na de bewaartermijn
(`ai.retention_days`, standaard 90 dagen) verwijderd.

### Toestemming (opt-in)

Vóór de eerste externe overdracht is per provider expliciete toestemming nodig
(**AI Adviseur → Privacy → Toestemming geven**). Toestemming en intrekking worden gelogd
(`ai_consent_events`). Intrekken zet de AI-modus direct op **Uit**.

### Bescherming tegen prompt-injection

- Releasenamen, beschrijvingen, indexer-antwoorden en logs gaan uitsluitend als
  gegevensvelden in één JSON-blok tussen `<case_data>`-tags; `<`, `>` en `&` worden
  ge-escaped zodat data het blok niet kan "sluiten".
- De systeemprompt verklaart die data expliciet onbetrouwbaar.
- Er worden geen tools meegestuurd; een antwoord met een toolcall wordt afgewezen.
- Het antwoord moet exact het schema volgen. Onbekende velden, acties of reasoncodes,
  evidence-ID's die niet lokaal bestaan of niet bij de casus horen: afgewezen.

## Modi

| Modus | Gedrag |
|---|---|
| **Uit** (standaard) | Geen calls. Casussen worden alleen genoteerd als "niet geanalyseerd". |
| **Schaduw** | Voorstel wordt gelogd en vergeleken; geen enkele workflowwijziging. |
| **Advies** | Voorstel met bewijs, kosten en PolicyGuard-uitkomst in de UI; knoppen *Accepteer*, *Wijs af*, *Markeer onjuist*, *Opnieuw onderzoeken*. |
| **Beperkt automatisch** | Alleen allowlisted, niet-destructieve acties, en pas na een geslaagde schaduwfase én benchmark. Anders valt het terug op Advies. |

### PolicyGuard en acties

De lokale PolicyGuard beslist, niet het model. Mogelijke acties:

| Actie | Uitvoerbaar | Automatisch mogelijk |
|---|---|---|
| `inspect_mapping` – mapping/metadata opnieuw ophalen | ja (via geregistreerde uitvoerder) | ja (standaard aan) |
| `retry_later` – later opnieuw controleren | ja | ja (standaard aan) |
| `request_search` – voorzichtige, rate-limited zoekpoging | ja | ja (standaard aan) |
| `compare_trials` – extra proefencodes tegen baseline | ja | alleen als je hem aanzet |
| `adjust_filter` | nee – alleen genoteerd; wijzig zelf via Instellingen | nooit |
| `manual_review` | nee | nooit |

Destructieve acties bestaan niet in het systeem: er is geen code om te verwijderen,
vervangen, identity-verdicts te zetten, taal/HDR/DV-beleid te wijzigen, snapshots te raken,
encodeprofielen te activeren of cutover te starten. Een uitvoerder krijgt alleen het lokale
doel van de casus (instantie + item), nooit parameters uit het modelantwoord. Ook bij
goedkeuring geldt: noodstop actief → niets uitgevoerd; dagmaximum
(`ai.bounded_auto.max_actions_per_day`) → niets uitgevoerd; time-out of weigering van de
uitvoerder → niets gewijzigd.

AV1-advies activeert nooit een profiel: hooguit worden echte korte proefencodes ingepland,
die de encodeermodule deterministisch tegen de baseline beoordeelt.

### Ontgrendelen van "Beperkt automatisch"

Alle voorwaarden moeten gelden voor de gekozen provider + model:

1. minimaal `ai.bounded_auto.min_shadow_cases` (standaard 20) geldige schaduwvoorstellen;
2. de laatste benchmark is gehaald: score ≥ `ai.bounded_auto.min_benchmark_accuracy`
   (0,80), verbetering t.o.v. de regels ≥ `ai.bounded_auto.min_improvement` (0,10) en
   100% schema-geldige antwoorden;
3. bij ≥ 5 feedbackitems is hooguit 20% als onjuist gemarkeerd.

### Benchmark

**AI Adviseur → Benchmark → Uitvoeren** (`POST /api/v1/ai/benchmark`) of op de host:

```bash
python -m mediaguard.ai.benchmark --provider anthropic --model claude-haiku-5-5
```

De gelabelde set (`mediaguard/ai/benchmark_cases_v1.json`) bevat ambiguë anime-identiteits-
en missing-diagnosecases (inclusief een prompt-injectiegeval). Elke run kost een klein bedrag
en telt mee in het budget. Resultaten staan in `ai_model_evaluations`. Haalt een model de
drempel niet, dan blijft de AI alleen een optionele uitleg/adviseur.

## Kosten en budget

- **Maandbudget** standaard **€5**, hard begrensd. Maandreset volgens de systeemtijdzone
  (`system.timezone`), niet UTC.
- **Kostenplafond per analyse** (standaard €0,05), **max analyses per dag** (25),
  **max antwoordtokens** (1500), **max invoertokens** (8000, geschat), **time-out** (30 s),
  **herhaalpogingen** (1 per analyse) en **retrybudget per dag** (10).
- Vóór elke call reserveert MediaGuard conservatief (geschatte invoer + maximale uitvoer
  × 1,25). Parallelle analyses kunnen samen het maandbudget niet overschrijden.
- Daarna wordt afgerekend met het **werkelijke tokengebruik** dat de API teruggeeft
  (`usage.input_tokens/output_tokens` bij Claude, `prompt_tokens/completion_tokens` bij
  OpenAI). Bij een time-out (provider kan al gerekend hebben) wordt de reservering als
  *geschatte* kosten geboekt. Na een crash/herstart worden openstaande reserveringen ook
  conservatief geboekt; een reboot zet het budget nooit terug.
- Identieke casussen gebruiken de cache (`ai.cache_ttl_days`) en kosten niets; cachehits
  staan in het verbruiksoverzicht.

### Prijstabel — controleer actuele providerprijzen

Prijzen staan in de bewerkbare instelling `ai.price_table` (per `provider:model`, per
1 miljoen invoer-/uitvoertokens, in USD of EUR) en de wisselkoers in
`ai.budget.usd_to_eur`. De meegeleverde waarden voor `claude-haiku-5-5` en
`claude-sonnet-5-5` zijn **voorbeeldwaarden**: controleer de actuele prijzen op de
prijspagina van de provider en pas ze aan. Voor OpenAI-modellen moet je zelf een prijs
invullen. **Zonder prijs voor het gekozen model doet MediaGuard geen calls.**

Let op: een Claude- of ChatGPT-abonnement is géén API-tegoed. Je hebt een betaalde API-sleutel
nodig (console van Anthropic of OpenAI).

## Inschakelen vanuit de UI (geen config-edit nodig)

1. **AI Adviseur → Provider**: kies *Claude* of *OpenAI* en vul de API-sleutel in
   (write-only; wordt versleuteld opgeslagen als `ai:anthropic:apikey` /
   `ai:openai:apikey` en nooit teruggegeven).
2. **Privacy**: lees welke gegevens worden verstuurd en geef toestemming.
3. **Model**: klik *Modellen ophalen* (live `GET /v1/models` bij de provider) en kies een
   model. Claude: `claude-haiku-5-5` (goedkoop, standaard) of `claude-sonnet-5-5`.
   OpenAI: kies uit de live lijst van je account.
4. **Kosten**: controleer de prijstabel en het maandbudget.
5. **Testverbinding**: *Test* doet een echte gestructureerde testcall met een synthetische
   casus (geen gebruikersdata) en valideert het antwoord tegen het schema.
6. **Beleid**: zet de modus op *Schaduw* (aanbevolen start) of *Advies*. Zonder sleutel,
   toestemming, prijs en geslaagde test weigert MediaGuard het inschakelen met een lijst van
   wat ontbreekt.
7. Optioneel: draai de **benchmark**; na een geslaagde schaduwfase en benchmark kan
   *Beperkt automatisch* aan.

Uitschakelen kan altijd met één klik (beleid → *Uit*) of door toestemming in te trekken.

## Storingen

Providerstoringen, time-outs, rate limits en budgetgrenzen leiden alleen tot een casusstatus
("Mislukt (geen gevolgen)" of "Niet geanalyseerd"). Monitoring, zoeken, downloads,
imports, scans en bestandstoegang lopen gewoon door: de AI zit nooit in het kritieke pad.

## API

| Methode | Route | Rol |
|---|---|---|
| GET | `/api/v1/ai/providers` (`?refresh=true` = live modellijst) | viewer |
| GET | `/api/v1/ai/providers/{provider}/models` | viewer |
| POST | `/api/v1/ai/providers/{provider}/test` | admin |
| GET / PATCH | `/api/v1/ai/settings` | viewer / admin |
| GET / POST | `/api/v1/ai/consent` | viewer / admin |
| GET | `/api/v1/ai/usage` | viewer |
| GET | `/api/v1/ai/cases`, `/api/v1/ai/cases/{case_id}` | viewer |
| GET | `/api/v1/ai/preview/{case_id}` | viewer |
| POST | `/api/v1/ai/cases/{case_id}/analyze` (`{"force": true}` = opnieuw onderzoeken) | operator |
| POST | `/api/v1/ai/cases/{case_id}/feedback` / `approve` / `reject` | operator |
| POST | `/api/v1/ai/benchmark`, GET `/api/v1/ai/evaluations` | admin / viewer |

## Integratie voor andere MediaGuard-modules

```python
from mediaguard.ai import register_evidence, request_advice, register_action

# 1. Bewijs aanmelden (B1/B2 bewaren het in hun eigen tabellen):
register_evidence(ctx, "idn-ev-123", "thexem_mapping", "scene S02E01 = TVDB S01E13",
                  instance_id="sonarr-anime", source_module="identity")

# 2. Alleen bij een needs_advice-code vanuit de deterministische classifier:
ticket = request_advice(ctx, "anime_identity", case_payload, ["idn-ev-123"],
                        "CONFLICTING_EPISODE_MAP", target_type="episode",
                        target_id="sonarr-anime:4711", instance_id="sonarr-anime",
                        source_module="identity")
# → {"case_id": ..., "status": "queued|skipped|ignored|error", ...}; gooit nooit.

# 3. Niet-destructieve uitvoerder registreren (bv. in <module>/ai_integration.py):
def register_ai(ctx):
    register_action(ctx, "request_search", my_rate_limited_search,
                    precheck=lambda ctx, req: my_policy_allows_search(req))
```

Modules kunnen ook `register_evidence_provider(ctx, fn)` gebruiken om bestaan van
evidence-ID's in hun eigen tabellen te laten controleren. Bij opstart roept de AI-module
automatisch `register_ai(ctx)` aan in `mediaguard.<module>.ai_integration` als die bestaat.
