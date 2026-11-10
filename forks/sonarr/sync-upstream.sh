#!/usr/bin/env bash
# Upstream sync check for a MediaGuard fork (identical script in forks/sonarr and forks/radarr).
#
#   forks/<app>/sync-upstream.sh [--sha <sha>|--branch <branch>] [--build] [--integration] [--out <dir>]
#
# 1. resolves the new upstream SHA (default: stable branch tip: Sonarr main, Radarr master)
# 2. re-applies patches/*.patch onto it with `git am --3way` (old pinned base is fetched too, so
#    3-way merges work) and records per-patch result + conflicting files
# 3. writes a behaviour report: upstream changes in files our patches touch and in the hook
#    interfaces/pipelines they depend on, version/SDK/API-major changes (Sonarr v5 guard)
# 4. --build: builds the unit-test stage (MediaGuard + decision/import engine tests) and a
#    candidate image <repo>:<newshort>-<rev>-candidate from the refreshed patch series
# 5. --integration: runs tests/integration/forks/run.sh against the candidate image
#
# It NEVER changes forks/<app>/patches, the pinned SHA, pushes, tags as release or deploys.
# Promotion is a reviewed manual step (docs/FORK_UPSTREAM_SYNC.md).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(basename "$HERE")"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
UPSTREAM_REPO="$(sed -n 's/^ARG UPSTREAM_REPO=//p' "$HERE/Dockerfile")"
PINNED_SHA="$(sed -n 's/^ARG UPSTREAM_SHA=//p' "$HERE/Dockerfile")"
case "$APP" in
  sonarr) BRANCH=main;   SLN=src/Sonarr.sln; API_DIR=src/Sonarr.Api.V3; EP_IMPORT=EpisodeImport ;;
  radarr) BRANCH=master; SLN=src/Radarr.sln; API_DIR=src/Radarr.Api.V3; EP_IMPORT=MovieImport ;;
  *) echo "unknown fork dir $APP" >&2; exit 2 ;;
esac
TARGET_SHA=""; DO_BUILD=0; DO_IT=0; OUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) TARGET_SHA="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --build) DO_BUILD=1; shift ;;
    --integration) DO_IT=1; DO_BUILD=1; shift ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done
if [[ -z "$TARGET_SHA" ]]; then
  TARGET_SHA="$(git ls-remote "$UPSTREAM_REPO" "refs/heads/$BRANCH" | cut -f1)"
  [[ -n "$TARGET_SHA" ]] || { echo "cannot resolve $BRANCH on $UPSTREAM_REPO" >&2; exit 2; }
fi
SHORT="${TARGET_SHA:0:7}"
OUT="${OUT:-$(mktemp -d "${TMPDIR:-/tmp}/mg-sync-$APP-$SHORT-XXXX")}"
mkdir -p "$OUT"
WORK="$OUT/src"
REPORT="$OUT/REPORT.md"
exec > >(tee "$OUT/sync.log") 2>&1

echo ">> $APP: pinned $PINNED_SHA -> candidate $TARGET_SHA ($BRANCH)"
rm -rf "$WORK"; git init -q "$WORK"; cd "$WORK"
git config user.name "MediaGuard sync"; git config user.email "mediaguard@localhost"
git fetch -q --depth 1 "$UPSTREAM_REPO" "$PINNED_SHA" && git tag pinned FETCH_HEAD
git fetch -q --depth 1 "$UPSTREAM_REPO" "$TARGET_SHA" && git tag candidate FETCH_HEAD
git checkout -q -b mediaguard candidate

ver() {  # version of a tree
  if [[ "$APP" == sonarr ]]; then git show "$1:.github/workflows/build.yml" 2>/dev/null | sed -n 's/^ *VERSION: *//p' | head -1
  else git show "$1:azure-pipelines.yml" 2>/dev/null | sed -n "s/^ *majorVersion: *'\(.*\)'/\1/p" | head -1; fi
}
sdk() { git show "$1:global.json" 2>/dev/null | sed -n 's/.*"version": *"\(.*\)".*/\1/p'; }
OLD_VER="$(ver pinned)"; NEW_VER="$(ver candidate)"; OLD_SDK="$(sdk pinned)"; NEW_SDK="$(sdk candidate)"

{
  echo "# Upstream sync report: $APP"
  echo
  echo "- generated: $(date -u +%FT%TZ)"
  echo "- upstream: $UPSTREAM_REPO ($BRANCH)"
  echo "- pinned: \`$PINNED_SHA\` (version $OLD_VER, SDK $OLD_SDK)"
  echo "- candidate: \`$TARGET_SHA\` (version ${NEW_VER:-unknown}, SDK $NEW_SDK)"
  echo
} > "$REPORT"

WARN=0
if [[ "${OLD_VER%%.*}" != "${NEW_VER%%.*}" ]]; then
  echo "- **MAJOR VERSION CHANGE ${OLD_VER%%.*} -> ${NEW_VER%%.*}**: Seerr compatibility (API v3) must be re-proven (B2 versieregel)." >> "$REPORT"; WARN=1
fi
[[ "$OLD_SDK" != "$NEW_SDK" ]] && { echo "- .NET SDK changed $OLD_SDK -> $NEW_SDK (Dockerfile SDK_IMAGE/RUNTIME_IMAGE must follow)." >> "$REPORT"; WARN=1; }
if [[ "$APP" == sonarr ]] && git cat-file -e candidate:src/Sonarr.Api.V5 2>/dev/null; then
  echo "- **Candidate contains src/Sonarr.Api.V5 (Sonarr v5 line)**: do not adopt without the v3 compatibility proof." >> "$REPORT"; WARN=1
fi
if ! git cat-file -e "candidate:$API_DIR" 2>/dev/null; then
  echo "- **$API_DIR missing in candidate**: API v3 removed." >> "$REPORT"; WARN=1
fi

# ---- apply patch series
echo -e "\n## Patch series\n" >> "$REPORT"
APPLY_OK=1
for p in "$HERE"/patches/*.patch; do
  name="$(basename "$p")"
  if git am -q --3way "$p" >/dev/null 2>"$OUT/am-$name.log"; then
    echo "- ✅ \`$name\` applied" >> "$REPORT"
  else
    APPLY_OK=0
    echo "- ❌ \`$name\` CONFLICT in: $(git diff --name-only --diff-filter=U | tr '\n' ' ')" >> "$REPORT"
    git diff --diff-filter=U > "$OUT/conflict-$name.diff" || true
    git am --abort >/dev/null 2>&1 || true
    break
  fi
done
if [[ $APPLY_OK == 1 ]]; then
  mkdir -p "$OUT/patches"
  git format-patch -q --no-signature -o "$OUT/patches" candidate
  if diff -q <(cat "$HERE"/patches/*.patch | grep -v '^From [0-9a-f]\{40\}' | grep -v '^index ' | grep -v '^@@') \
             <(cat "$OUT"/patches/*.patch | grep -v '^From [0-9a-f]\{40\}' | grep -v '^index ' | grep -v '^@@') >/dev/null; then
    echo -e "\nRefreshed patches are content-identical (only offsets/hashes differ)." >> "$REPORT"
  else
    echo -e "\nRefreshed patch series written to \`$OUT/patches\` (content changed by 3-way merge: review!)." >> "$REPORT"; WARN=1
  fi
fi

# ---- behaviour report: upstream changes to what our patches touch / depend on
echo -e "\n## Upstream changes relevant to the patches\n" >> "$REPORT"
TOUCHED="$(cat "$HERE"/patches/*.patch | sed -n 's#^+++ b/##p' | sort -u)"
DEPS="src/NzbDrone.Core/DecisionEngine/DownloadDecisionMaker.cs
src/NzbDrone.Core/DecisionEngine/Specifications/IDownloadDecisionEngineSpecification.cs
src/NzbDrone.Core/DecisionEngine/DownloadSpecDecision.cs
src/NzbDrone.Core/MediaFiles/$EP_IMPORT/ImportDecisionMaker.cs
src/NzbDrone.Core/MediaFiles/$EP_IMPORT/IImportDecisionEngineSpecification.cs
src/NzbDrone.Core/MediaFiles/$EP_IMPORT/ImportSpecDecision.cs
src/NzbDrone.Core/Download/CompletedDownloadService.cs
src/NzbDrone.Common/Http/HttpClient.cs
src/NzbDrone.Common/Http/HttpRequest.cs"
echo '```' >> "$REPORT"
# shellcheck disable=SC2086
git diff --stat pinned candidate -- $TOUCHED $DEPS >> "$REPORT" 2>/dev/null || true
echo '```' >> "$REPORT"
echo -e "\nAPI v3 controllers/resources changed:\n\n\`\`\`" >> "$REPORT"
git diff --stat pinned candidate -- "$API_DIR" | tail -n 25 >> "$REPORT" 2>/dev/null || true
echo '```' >> "$REPORT"
git diff pinned candidate -- $DEPS > "$OUT/interface-changes.diff" 2>/dev/null || true
echo -e "\nFull diff of hook-relevant upstream files: \`$OUT/interface-changes.diff\` ($(wc -l < "$OUT/interface-changes.diff") lines)." >> "$REPORT"

# ---- build + unit tests + candidate image
BUILD_RC=skipped; UT_RC=skipped; IT_RC=skipped
if [[ $DO_BUILD == 1 && $APPLY_OK == 1 ]]; then
  CTX="$OUT/context"; rm -rf "$CTX"; mkdir -p "$CTX"
  cp "$HERE/Dockerfile" "$CTX/"; cp -r "$OUT/patches" "$CTX/patches"
  sed -i "s/^ARG UPSTREAM_SHA=.*/ARG UPSTREAM_SHA=$TARGET_SHA/; s/^ARG UPSTREAM_VERSION=.*/ARG UPSTREAM_VERSION=${NEW_VER}.0/" "$CTX/Dockerfile"
  SECRET=(); [[ -n "${BUILD_CA_BUNDLE:-}" && -s "${BUILD_CA_BUNDLE}" ]] && SECRET=(--secret "id=ca_bundle,src=$BUILD_CA_BUNDLE")
  TEST_FILTER="FullyQualifiedName~MediaGuard|FullyQualifiedName~DecisionEngine|FullyQualifiedName~${EP_IMPORT}"
  docker build "${SECRET[@]}" --progress=plain --target unittest --build-arg "TEST_FILTER=$TEST_FILTER" \
    -t "mediaguard/$APP-fork:$SHORT-sync-unittest" "$CTX" > "$OUT/unittest.log" 2>&1; UT_RC=$?
  docker build "${SECRET[@]}" --progress=plain --target runtime \
    -t "mediaguard/$APP-fork:$SHORT-${MG_PATCH_REV:-mg1}-candidate" "$CTX" > "$OUT/build.log" 2>&1; BUILD_RC=$?
  if [[ $DO_IT == 1 && $BUILD_RC == 0 ]]; then
    var="$(echo "$APP" | tr a-z A-Z)_IMAGE"
    env "$var=mediaguard/$APP-fork:$SHORT-${MG_PATCH_REV:-mg1}-candidate" "$REPO_ROOT/tests/integration/forks/run.sh" > "$OUT/integration.log" 2>&1; IT_RC=$?
  fi
fi
{
  echo -e "\n## Build and tests\n"
  echo "| step | result | log |"; echo "|---|---|---|"
  echo "| unit tests (MediaGuard + decision/import engines) | $UT_RC | unittest.log |"
  echo "| candidate image mediaguard/$APP-fork:$SHORT-${MG_PATCH_REV:-mg1}-candidate | $BUILD_RC | build.log |"
  echo "| integration harness | $IT_RC | integration.log |"
  echo -e "\n## Verdict\n"
  if [[ $APPLY_OK != 1 ]]; then echo "**BLOCKED**: patch conflict, resolve manually (see conflict-*.diff)."
  elif [[ "$UT_RC" != 0 && "$UT_RC" != skipped ]] || [[ "$BUILD_RC" != 0 && "$BUILD_RC" != skipped ]] || [[ "$IT_RC" != 0 && "$IT_RC" != skipped ]]; then echo "**BLOCKED**: build/test failure."
  elif [[ $WARN == 1 ]]; then echo "**REVIEW REQUIRED**: applies, but see warnings above."
  else echo "**CANDIDATE OK**: may be promoted after human review (never auto-deployed)."; fi
} >> "$REPORT"
echo ">> report: $REPORT"
cat "$REPORT"
[[ $APPLY_OK == 1 ]]
