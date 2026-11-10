# Sonarr fork patch series (`mg2`)

These patches apply with `git am` onto `bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a` and implement
`packages/contracts/fork-hooks.md`. Design rules:
- **Mostly additive.** All logic lives in new files: `src/NzbDrone.Core/MediaGuard/*` plus one new
  specification per hook. Only **four upstream files** change, each by appending enum members:
  `DownloadRejectionReason.cs`, `ImportRejectionReason.cs` and `SpecificationPriority.cs`.
- **No pipeline rewrites.** The hooks plug into Sonarr's own extension points: DI auto-registers every
  `IDownloadDecisionEngineSpecification` / `IImportDecisionEngineSpecification` implementation.
- **Off by default.** With `MEDIAGUARD_URL` unset and `MEDIAGUARD_SHADOW_MODE` not set to `1`, both
  specifications accept immediately with no network call, so behaviour matches upstream.
- Events use the stock Webhook connection (no patch).
- The API key and other settings are configured through upstream's `SONARR__*` environment
  variables (no patch).

| # | Patch | Upstream files touched | New files |
|---|---|---|---|
| 0001 | hook client and environment configuration | none | `MediaGuard/MediaGuardHookOptions.cs`, `MediaGuardHookClient.cs`, `MediaGuardPayloads.cs`, test `MediaGuardTests/MediaGuardHookClientFixture.cs` |
| 0002 | pre-grab download decision specification | `DownloadRejectionReason.cs` (+3 members), `SpecificationPriority.cs` (+`PolicyHook = 2`) | `DecisionEngine/Specifications/MediaGuardPreGrabSpecification.cs`, test fixture |
| 0003 | pre-import decision specification | `ImportRejectionReason.cs` (+3 members) | `MediaFiles/EpisodeImport/Specifications/MediaGuardPreImportSpecification.cs`, test fixture |
| 0004 | null-safe MediaInfo language/subtitle lists | `MediaFiles/MediaInfo/MediaInfoModel.cs` (two properties) | none |
| 0005 | target .NET 8 (LTS) | all `*.csproj`, `Directory.Build.props`, `global.json`, `build.sh`, 3 auth handlers, 2 header writes, `QueueController.cs` | none |

---

## 0001: MediaGuard hook client and environment configuration

**Motivation.** The contract requires a bounded, authenticated JSON call to MediaGuard core with a fail
policy. This must be configurable per container, so that one image can serve several instances.

**What it does.**
- `MediaGuardHookOptions.FromEnvironment` reads:

  | Variable | Default | Meaning |
  |---|---|---|
  | `MEDIAGUARD_URL` | empty (hooks off) | core base URL |
  | `MEDIAGUARD_INSTANCE_ID` | required when the URL is set | sent as `instance_id` |
  | `MEDIAGUARD_HOOK_KEY` | required when the URL is set | sent as `X-MediaGuard-Hook-Key` |
  | `MEDIAGUARD_HOOK_TIMEOUT_MS` | 3000 | timeout per call |
  | `MEDIAGUARD_HOOK_FAIL_POLICY` | `open` | `open` or `closed` |
  | `MEDIAGUARD_SHADOW_MODE` | 0 | `1`/`true`/`yes` rejects all grabs locally |
  | `MEDIAGUARD_HOOK_CIRCUIT_BREAK_SECONDS` | 30 | extension beyond the contract: after a failure, the next N s apply the fail policy without a network call, so one RSS sync with 100 candidates never waits 100× the timeout. `0` disables it. |

  Values are read once at startup. Change them by recreating the container.
- `MediaGuardHookClient.Send(hook, payload)` sends `POST {URL}/api/v1/hooks/{hook}` through Sonarr's own
  `IHttpClient`, so proxy settings and the user agent behave as for every other outgoing call. It uses
  snake_case JSON (Newtonsoft `SnakeCaseNamingStrategy`), the hook-key header and
  `RequestTimeout = MEDIAGUARD_HOOK_TIMEOUT_MS`.
  - A timeout, connection error, non-2xx status, unparseable body or unknown `decision` counts as
    *unavailable*. The client logs a **warning** to the fork log
    (`MediaGuard pre-grab hook unavailable (...); fail policy 'closed' applied`) and applies the fail
    policy. The result carries reason code `MEDIAGUARD_UNAVAILABLE`.
  - A missing instance id or key also counts as unavailable, and the client makes no call.
- `MediaGuardPayloads.cs` builds the contract payloads (`release`, `series`, `episodes`, `parsed`,
  `media_info`, …) from `RemoteEpisode` and `LocalEpisode`.

**Tests.** `MediaGuardHookClientFixture` (NUnit, 10 cases) covers: the snake_case body, the
URL/method/header/timeout, decision parsing (case-insensitive accept/reject/defer), fail-open on
timeout, fail-closed on connection error, an invalid decision counting as unavailable, the circuit
breaker suppressing the second call, the fail policy applying without a call when the key is missing,
and the environment parsing and defaults.

**Upstream-maintenance impact.** Very low. The patch only adds new files. It depends only on stable
public types: `IHttpClient`, `HttpRequestBuilder`, `RemoteEpisode`, `LocalEpisode`, `MediaInfoModel`
and Newtonsoft. A rename of model properties shows up as a compile error, and `sync-upstream.sh`
builds and tests each candidate.

## 0002: pre-grab download decision specification

**Motivation.** B2.1 / B2.4: MediaGuard must be able to veto releases before Sonarr grabs them, for
RSS, automatic search and interactive search. The reason must be visible to users (extended
rejection diagnostics).

**What it does.**
- Adds `MediaGuardPreGrabSpecification : IDownloadDecisionEngineSpecification`.
- Adds `SpecificationPriority.PolicyHook = 2`. `DownloadDecisionMaker` evaluates specifications in
  priority groups and stops at the first group that rejects, so the hook **only runs for releases
  that every upstream specification already accepted**: monitored, wanted, quality/size/CF OK, not in
  history or queue, disk checks done. Core therefore sees few calls, and those carry real candidates.
  The CF score is already computed at that point.
- `search_type`: `rss` without search criteria (also for pushed releases), `interactive` when
  `SearchCriteria.InteractiveSearch` is set, `search` otherwise.
- `reject` (and `defer`, which pre-grab treats as reject) produces a `DownloadRejection` with the message
  `MediaGuard: <message> [<reason_code>]`. Its reason is `MediaGuardRejected`, or
  `MediaGuardUnavailable` when the fail policy caused it. The message appears in `GET /api/v3/release`
  `rejections[]` and in the interactive search UI.
- `accept` with a non-zero `score_adjustment` adds the adjustment to `RemoteEpisode.CustomFormatScore`
  before prioritisation. This lets core influence the ranking (the "small interface in the ranking
  pipeline" of B2.1).
- `MEDIAGUARD_SHADOW_MODE=1` rejects every candidate with
  `MediaGuard: shadow mode actief, grabs worden lokaal geweigerd [MEDIAGUARD_SHADOW_MODE]`. It makes no
  network call and does not need a URL.
- The specification type is `Permanent`, which avoids feeding the delay/pending-release queue with
  policy rejections.

**Tests.** `MediaGuardPreGrabSpecificationFixture` (9 cases): disabled means accept with no call; the
spec runs after the Disk priority; shadow mode rejects locally; the reject message format; the
unavailable reason; the score adjustment; the contract payload (`search` and `interactive`); `rss`
without criteria. Integration: `tests/integration/forks` covers RSS accept, interactive reject/accept,
timeout fail-open and fail-closed, HTTP 500 and shadow mode against the real binaries.

**Upstream-maintenance impact.** Low:
- Appending enum members can conflict textually when upstream appends its own reasons (seen against
  `develop`/v5). The conflict is trivial to resolve: keep both.
- If upstream ever changes the priority-group semantics of `DownloadDecisionMaker`, the hook would
  still work but might run more often. `sync-upstream.sh` diffs that file explicitly.
- Known limitation (upstream behaviour): a user can still force-grab a rejected release from the
  interactive search UI, as with every other rejection.

## 0003: pre-import decision specification

**Motivation.** B2.2: intercept possibly mis-mapped files before they enter the library as the "correct"
episode. The default must be fail-safe when the identity is in doubt.

**What it does.**
- Adds `MediaGuardPreImportSpecification : IImportDecisionEngineSpecification`, which posts the
  contract `pre-import` payload. The payload includes the path, download client and id, the grabbed
  release title, episodes, parsed info, `is_upgrade`, and `media_info` (codec, audio and subtitle
  languages, runtime, resolution from ffprobe).
- `accept` imports normally.
- `reject` produces `MediaGuard: <message> [<code>]` with reason `MediaGuardRejected`.
- `defer` produces the same message with reason `MediaGuardDeferred`.
- Unavailable with fail policy `closed` produces reason `MediaGuardUnavailable`.
- In every rejected case the file stays in the download folder. Sonarr's normal completed-download
  handling shows the message in Activity → Queue (status *warning*,
  `trackedDownloadState = importPending/importBlocked`) and **retries on every
  `RefreshMonitoredDownloads` cycle (about 1 min)**. A later `accept` imports the file automatically.
  This was proven in the integration test: reject → reject → defer → accept leads to an import.
  The difference between reject and defer is therefore semantic (reason code and enum) for core and
  the UI. Core is expected to resolve a hard reject explicitly, by blocklisting or removing the
  download via `DELETE /api/v3/queue/{id}`.
- Files already inside the series folder (`LocalEpisode.ExistingFile`, e.g. disk rescans) are never
  sent, because the hook is about new imports.

**Tests.** `MediaGuardPreImportSpecificationFixture` (6 cases). Integration: `test_preimport_reject_prevents_import_then_accept_imports`.

**Upstream-maintenance impact.** Low. Import specifications have had a stable shape for years. The
patch touches the import pipeline only through the interface, plus the enum append.

---

## 0004: null-safe MediaInfo language and subtitle lists

**Motivation.** Databases migrated from older installs can contain media info whose
`AudioLanguages` or `Subtitles` is `null`. Upstream then throws
`ArgumentNullException (Parameter 'source')` in `MediaInfoResourceMapper.ToResource`
(`ConcatToString`), so `GET /api/v3/episodefile?seriesId=…` returns HTTP 500 for every series
with such a file. MediaGuard's library sync and optimisation stopped on that error (seen on a
real migrated TrueNAS install, 2026-11-03).

**What it does.** The two properties get a backing field and the setter turns `null` into an
empty list, so every reader in Sonarr (API, naming, custom formats) sees a list. Nothing else
changes; a later media-info rescan writes real values as usual.

---

## 0005: target .NET 8 (LTS)

**Motivation.** Sonarr v4 (`main`) still targets .NET 6, which receives no security fixes since
November 2024. Upstream moved to .NET 8+ only in the v5 line. Radarr (our other fork) already
runs on .NET 8.

**What it does.**
- `net6.0` → `net8.0` in every project and in `build.sh`; `global.json` pins SDK 8.0.421 (same as
  the Radarr fork); publishing is explicitly self-contained (`-p:SelfContained=True`), because on
  .NET 8 a runtime identifier no longer implies it.
- Framework packages (Microsoft.Extensions.*, ASP.NET Core, System.*) moved to their 8.0
  versions (NLog.Extensions.Logging pulls Microsoft.Extensions.Logging 8.0 on net8.0).
- API changes required by the .NET 8 SDK (warnings are errors): authentication handlers without
  the obsolete `ISystemClock`; response headers set via the indexer (ASP0019); SYSLIB0051
  (formatter-serialization constructors) suppressed.
- **Behaviour fix:** on .NET 8 an absent `[FromQuery]` array with default `null` stays `null`, so
  `GET /api/v3/queue` without filters returned HTTP 500. The queue filters are now null-safe, the
  same check Radarr uses (History and Blocklist already were).
- NuGet audit: SixLabors.ImageSharp 3.1.12 (the version Sonarr v4 already ships, used only to
  resize posters) has advisories; 3.2.0 fixes them but requires a commercial licence key. The
  specific advisories are suppressed in `Directory.Build.props`; update when a free version exists.

**Verified (2026-11-03).** MediaGuard unit tests 25/25; broad core subset (Parser, MediaInfo,
DecisionEngine, EpisodeImport, Datastore, CustomFormats: 2,551 tests) gives the same result as the
.NET 6 build, and no test fails on .NET 8 that passes on .NET 6; fork integration suite 12/12;
patch-0004 regression (null media-info languages) returns 200.

---

## Rebuilding the series after a change

```bash
git clone https://github.com/Sonarr/Sonarr && cd Sonarr && git checkout -b mediaguard bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a
git am ../media-guard-ultimate/forks/sonarr/patches/*.patch
# edit, commit (one logical change per commit), then:
git format-patch --no-signature -o ../media-guard-ultimate/forks/sonarr/patches bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a
```
Bump the image revision (`MG_PATCH_REV=mg2 ./build.sh`) whenever the series changes.

Unit tests, without building the full image:
`docker build --target unittest forks/sonarr` (filter `FullyQualifiedName~MediaGuard` by default;
override with `--build-arg TEST_FILTER=...`).
