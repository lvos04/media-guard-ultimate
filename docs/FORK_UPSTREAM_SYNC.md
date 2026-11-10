# Fork upstream sync procedure (Sonarr / Radarr)

MediaGuard maintains **two forks** (Sonarr and Radarr) and builds **two images**. Each fork is a
pinned upstream SHA plus a small patch series. The four runtime instances (`sonarr-series`,
`sonarr-anime`, `radarr-films`, `radarr-animefilms`) all run one of those two images, so a sync is
always done per fork and never per instance.

```
forks/<app>/
  UPSTREAM.md        pinned SHA, version, license, how to publish as a GitHub fork
  PATCHES.md         per patch: motivation, tests, upstream-maintenance impact
  patches/000N-*.patch   git format-patch series (git am onto the pinned SHA)
  Dockerfile         upstream -> frontend (node/yarn) + backend (patched, .NET SDK from global.json)
                     -> unittest (optional target) -> runtime (runtime-deps, non-root, /config, HEALTHCHECK /ping)
  build.sh           builds mediaguard/<app>-fork:<shortsha>-mgN (+ <version>-mgN)
  sync-upstream.sh   candidate check against a new upstream SHA (never deploys)
```

## Principles

1. **Follow the stable lines only.** Use Sonarr `main` (v4) and Radarr `master`. Sonarr `develop` is
   v5 (.NET 10, `Sonarr.Api.V5`). Seerr supports Sonarr/Radarr v3/v4, so v5 is out of scope until a
   v3-compatibility proof exists (plan part B2, version rule). `sync-upstream.sh` flags a major-version
   change, an SDK change, a missing `Api.V3` and the presence of `Sonarr.Api.V5`.
2. **Patch impact stays small.** Each fork has 3 patches, mostly new files. Only 3 upstream files change
   (enum appends). A conflict there is expected now and then, and trivial to resolve.
3. **Never auto-merge and never auto-deploy.** The script produces a *candidate image* and a
   *report*. A human promotes the candidate.
4. **Reproducible.** The Dockerfile clones exactly `UPSTREAM_SHA`, checks it with
   `git rev-parse HEAD`, applies `patches/*.patch` with `git am` (the build fails on any conflict) and
   uses the SDK and node versions that upstream itself pins.

## Routine check (weekly or on an upstream release)

```bash
# report only: resolve the new branch tip, try the patch series with a 3-way merge, diff hook-relevant files
forks/sonarr/sync-upstream.sh --out /tmp/sync-sonarr
forks/radarr/sync-upstream.sh --out /tmp/sync-radarr

# full check: + unit tests (MediaGuard + all DecisionEngine/Import tests) + candidate image
BUILD_CA_BUNDLE=/path/ca.pem forks/sonarr/sync-upstream.sh --build --out /tmp/sync-sonarr

# full check + the 4-instance integration harness against the candidate image
MG_TEST_CA_BUNDLE=/path/ca.pem forks/sonarr/sync-upstream.sh --integration --out /tmp/sync-sonarr

# a specific SHA (e.g. a new release tag) instead of the branch tip
forks/radarr/sync-upstream.sh --sha <sha>
```

`<out>/REPORT.md` contains:
- the pinned and candidate SHAs, versions and SDKs, plus guard warnings
- per patch: ✅ applied or ❌ conflict, with the conflicting files (`conflict-*.diff`)
- the upstream `diff --stat` of every file the patches touch, and of the pipelines and interfaces the
  hooks depend on (`DownloadDecisionMaker`, `ImportDecisionMaker`, the specification interfaces,
  `CompletedDownloadService`, `HttpClient`), with the full diff in `interface-changes.diff`
- the diff stat of the `Api.V3` controllers and resources (a Seerr-compatibility signal)
- results of the unit tests, the candidate image build and the integration run
- a verdict: `CANDIDATE OK`, `REVIEW REQUIRED` or `BLOCKED`

Exit code 0 means the series applied; 1 means a conflict.

## Resolving a conflict

```bash
cd <out>/src                              # repo prepared by the script (tags: pinned, candidate)
git checkout -B mediaguard candidate
git am --3way /repo/forks/<app>/patches/*.patch   # stops at the conflicting patch
# fix the file(s); for enum appends keep upstream's new members AND ours, ours last
git add -A && git am --continue
git format-patch --no-signature -o /repo/forks/<app>/patches candidate   # review the diff in /repo
```

## Promoting a candidate (manual and reviewed)

1. Review `REPORT.md` and `interface-changes.diff`. Pay attention to changed decision or import
   pipelines, rejection handling and `Api.V3` resource changes.
2. Update `ARG UPSTREAM_SHA` / `ARG UPSTREAM_VERSION` in `forks/<app>/Dockerfile`. Copy the refreshed
   patches if they changed. Bump `MG_PATCH_REV` when the series content changed. Update the SDK and
   runtime image ARGs if `global.json` changed.
3. Update `UPSTREAM.md` (SHA, version, date verified).
4. Run `forks/<app>/build.sh`, then `docker build --target unittest forks/<app>`, then
   `tests/integration/forks/run.sh`. Record the results in
   `tests/integration/forks/RESULTS.md`.
5. Commit through the normal review. The images roll out through the MediaGuard update policy
   (plan part K4: backup, then per-instance rollout, health check, rollback to the previous tag). Old
   tags are kept so rollback is a tag switch. The `/config` volumes are forward-migrated by upstream
   migrations, so take a **backup before switching**, because an upstream DB migration cannot be undone
   by changing tags.

## Rollback

Each image tag contains its exact source (label `io.mediaguard.upstream.sha` and
`/app/<app>/mediaguard-patches`). Rolling back an instance means stopping it, restoring its
`/config` backup if the newer version migrated the database, and starting it with the previous tag.
The other three instances are not affected (separate containers and volumes; see harness test
`test_suspended_instance_does_not_affect_others`).

## CI suggestion

Nightly: `sync-upstream.sh --build` for both forks. Publish `REPORT.md` as an artifact and alert on
`BLOCKED` or `REVIEW REQUIRED`. Weekly: `--integration`. CI must never push images to the production
registry tag that instances follow.
