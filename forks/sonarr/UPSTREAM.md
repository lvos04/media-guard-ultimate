# Sonarr fork: upstream pin

| | |
|---|---|
| Upstream repo | https://github.com/Sonarr/Sonarr |
| Branch followed | `main` (stable v4 line) |
| Pinned SHA | `bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a` (verified with `git ls-remote` on 2026-11-02: tip of `refs/heads/main`) |
| Version | **4.0.20.3014**: the SHA is release tag `v4.0.20.3014` (`cab419a`) plus 3 CI-only commits (`5536953`, `a851def`, `bc54afb`); no product code changes |
| .NET SDK / runtime | 6.0.405 (from `global.json`), runtime `mcr.microsoft.com/dotnet/runtime-deps:6.0-bookworm-slim` |
| Frontend | node 20.11.1 / yarn 1.22.19 (from `package.json` "volta") |
| API | `/api/v3` (Seerr supports Sonarr v3/v4) |
| Patch series | `patches/0001..0005` (see `PATCHES.md`), revision `mg2` |
| Image | `mediaguard/sonarr-fork:bc54afb-mg2` (alias `mediaguard/sonarr-fork:4.0.20.3014-mg2`) |
| Runtime | .NET 8 (LTS) via patch 0005; upstream v4 `main` targets .NET 6 (end of support Nov 2024) |

## Why `main` and not `develop`

`develop` (`9a41dfc`, 2026-10-31) is the **Sonarr v5** line: `global.json` requires SDK 10.0.403 and
it adds `src/Sonarr.Api.V5`. Seerr documents support for Sonarr/Radarr v3/v4 only, and the MediaGuard
plan (part B2, version rule) forbids moving to v5 before v3 compatibility has been proven. `main`
is the v4 stable line (`.github/workflows/build.yml`: `SONARR_MAJOR_VERSION: 4`, `VERSION: 4.0.20`).
`sync-upstream.sh --branch develop` shows the v5 guard: it reports the major-version and SDK change,
the presence of `Sonarr.Api.V5`, and a conflict in `DownloadRejectionReason.cs`.

## License

Sonarr is licensed under the **GNU General Public License v3.0** (`LICENSE.md` upstream); the full text is in
[`forks/LICENSE`](../LICENSE). This fork
(upstream source + `patches/`) is GPL-3.0 as well:
- the images contain the complete corresponding source in reproducible form: the pinned upstream
  SHA (image label `io.mediaguard.upstream.sha`) plus the patch series, which is copied into the
  image at `/app/sonarr/mediaguard-patches`
- anyone distributing the image must also offer this source (this repository and the upstream SHA)
- the Sonarr name and logo belong to Team Sonarr. The fork reports itself as
  `packageAuthor: MediaGuard fork (upstream <sha>)` and is not an official Sonarr build. Do not
  report fork issues to Team Sonarr.

## Making this a real GitHub fork later

Nothing was created on GitHub (no permission was given). To publish the fork:

```bash
# 1. fork https://github.com/Sonarr/Sonarr to <you>/Sonarr (GitHub UI or: gh repo fork Sonarr/Sonarr --clone=false)
git clone https://github.com/<you>/Sonarr.git sonarr-fork && cd sonarr-fork
git remote add upstream https://github.com/Sonarr/Sonarr.git
git fetch upstream main
git checkout -b mediaguard/v4 bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a
git am /path/to/media-guard-ultimate/forks/sonarr/patches/*.patch
git tag mediaguard-v4.0.20.3014-mg2
git push origin mediaguard/v4 --tags
```

From then on, the patch files in this repo stay the review and build source of truth.
Regenerate them with `git format-patch bc54afb..mediaguard/v4 -o forks/sonarr/patches`.
Upstream syncs follow `docs/FORK_UPSTREAM_SYNC.md` (`sync-upstream.sh`). CI can build with
`UPSTREAM_REPO=https://github.com/<you>/Sonarr.git` and the fork-branch SHA, with an empty
`patches/` directory.
