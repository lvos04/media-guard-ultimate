# Radarr fork: upstream pin

| | |
|---|---|
| Upstream repo | https://github.com/Radarr/Radarr |
| Branch followed | `master` (stable) |
| Pinned SHA | `a96bf7c3ecb23cb76993770727285dc17a613fbc` (verified with `git ls-remote` on 2026-11-02: tip of `refs/heads/master`) |
| Version | **6.4.4.10685**: the SHA is exactly release tags `v6.4.4.10684` / `v6.4.4.10685` |
| .NET SDK / runtime | 8.0.421 (from `global.json`), runtime `mcr.microsoft.com/dotnet/runtime-deps:8.0-bookworm-slim` |
| Frontend | node 20.11.1 / yarn 1.22.19 (from `package.json` "volta") |
| API | `/api/v3`. Radarr v4/v5/v6 all keep API v3, which Seerr uses. |
| Patch series | `patches/0001..0003` (see `PATCHES.md`), revision `mg1` |
| Image | `mediaguard/radarr-fork:a96bf7c-mg1` (alias `mediaguard/radarr-fork:6.4.4.10685-mg1`) |

Note: Radarr's major version (6) is not an API version. Radarr has served `/api/v3` since v3, and the
integration harness proves that the Seerr-relevant endpoints work (`tests/integration/forks`).

## Build note: NuGet audit

The pinned lock references `SixLabors.ImageSharp 3.1.12`. NuGet's audit has published advisories for
that package since the pin (GHSA-gwg2-r3hj-4w44, GHSA-j3p4-wp97-rph4, …), and Radarr builds with
warnings-as-errors. The backend stage therefore sets `NuGetAudit=false` so it reproduces the
upstream release byte-for-byte. This is a known **upstream** finding: ImageSharp only resizes the
poster/fanart images Radarr downloads from its own metadata servers. Track it via `sync-upstream.sh`
and adopt the upstream bump. Do not patch it in the fork unless a fix is urgent.

## License

Radarr is licensed under the **GNU General Public License v3.0** (`LICENSE` upstream); the full text is in
[`forks/LICENSE`](../LICENSE). This fork
(upstream source + `patches/`) is GPL-3.0 as well:
- the images contain the complete corresponding source in reproducible form: the pinned SHA
  (label `io.mediaguard.upstream.sha`) plus the patch series, which is copied into the image at
  `/app/radarr/mediaguard-patches`
- distributors must offer that source
- the Radarr name and logo belong to Team Radarr. This build is not an official one
  (`packageAuthor: MediaGuard fork (upstream <sha>)`).

## Making this a real GitHub fork later

```bash
# fork https://github.com/Radarr/Radarr to <you>/Radarr first (no repositories were created automatically)
git clone https://github.com/<you>/Radarr.git radarr-fork && cd radarr-fork
git remote add upstream https://github.com/Radarr/Radarr.git
git fetch upstream master
git checkout -b mediaguard/master a96bf7c3ecb23cb76993770727285dc17a613fbc
git am /path/to/media-guard-ultimate/forks/radarr/patches/*.patch
git tag mediaguard-v6.4.4.10685-mg1
git push origin mediaguard/master --tags
```

Regenerate the patch files with `git format-patch a96bf7c..mediaguard/master -o forks/radarr/patches`.
Syncing follows `docs/FORK_UPSTREAM_SYNC.md`.
