#!/usr/bin/env bash
# MediaGuard ZERO-CONFIG installation test (ADR-011): the REAL production compose
# (deploy/truenas/compose.yaml) + dev overlay, started exactly as a user would on TrueNAS:
#   - the three dataset paths are the ONLY settings (MG_APPDATA / MG_WORK / MG_DATA_ROOT);
#   - appdata and work are EMPTY root-owned dirs (fresh datasets), no keys, no tokens;
#   - data/media/tv already exists, owned by ANOTHER uid, with a file in it (an existing library).
# mediaguard-init must create dirs + all secrets; then every component must authenticate with the
# generated keys (core⇄forks API keys, fork→core hook keys, core⇄recovery⇄encoder token), and a
# second `docker compose up` must change nothing.
#
#   tests/integration/stack/run_zero_config.sh [pytest args...]
#
# Uses the same host ports as run.sh: do not run both at the same time.
# The dev overlay only adds the fake indexer/SAB and 127.0.0.1 port bindings (MG_DEV_*);
# MG_TEST_CA_BUNDLE as in run.sh (forks need metadata from skyhook/api.radarr.video).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
RUN="$HERE/.run-zero"
PROJECT=mgzero
OTHER_UID=1234

: "${MG_CORE_IMAGE:=mediaguard/core:dev}"
: "${MG_SONARR_IMAGE:=mediaguard/sonarr-fork:bc54afb-mg1}"
: "${MG_RADARR_IMAGE:=mediaguard/radarr-fork:a96bf7c-mg1}"
: "${MG_ENCODE_IMAGE:=mediaguard/encode:dev}"

COMPOSE=(docker compose -p "$PROJECT" --env-file "$RUN/stack.env"
         -f "$ROOT/deploy/truenas/compose.yaml" -f "$ROOT/deploy/dev/compose.dev.yaml")

if [[ -f "$RUN/stack.env" ]]; then
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
fi
rm -rf "$RUN/appdata" "$RUN/work" "$RUN/data" "$RUN"/logs-*.txt
mkdir -p "$RUN/fixtures" "$RUN/appdata" "$RUN/work" "$RUN/data/media/tv/Bestaande Serie"
if [[ ! -s "$RUN/fixtures/sample.mkv" ]]; then
  for src in "$HERE/.run/fixtures/sample.mkv" "$ROOT/tests/integration/forks/.run/fixtures/sample.mkv"; do
    [[ -s "$src" ]] && { cp "$src" "$RUN/fixtures/sample.mkv"; break; }
  done
fi
if [[ ! -s "$RUN/fixtures/sample.mkv" ]]; then
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i color=c=darkblue:s=320x180:r=1 -f lavfi -i anullsrc=r=8000:cl=mono \
    -t 1200 -c:v libx264 -preset ultrafast -g 600 -c:a aac -b:a 8k -metadata:s:a:0 language=eng "$RUN/fixtures/sample.mkv"
fi
chmod a+r "$RUN/fixtures/sample.mkv"
# fresh datasets: root-owned and empty; existing library dir owned by someone else (must stay so)
chown root:root "$RUN/appdata" "$RUN/work" "$RUN/data"
chmod 0755 "$RUN/appdata" "$RUN/work" "$RUN/data" "$RUN/data/media"
echo "bestaand" > "$RUN/data/media/tv/Bestaande Serie/bestaand.mkv"
chown -R "$OTHER_UID:$OTHER_UID" "$RUN/data/media/tv"
chmod 0777 "$RUN/data/media/tv"                      # the fork may still write into it
chmod 0640 "$RUN/data/media/tv/Bestaande Serie/bestaand.mkv"
# een bestaande bibliotheek heeft al een downloadmap (init maakt op een niet-lege dataset niets
# aan); zelfde situatie als bij de gebruiker: downloads in dezelfde dataset als de media
mkdir -p "$RUN/data/downloads"/{tv,tv-anime,movies,movies-anime} "$RUN/data/media"/{anime,movies,animefilms}
chown -R 568:568 "$RUN/data/downloads" "$RUN/data/media/anime" "$RUN/data/media/movies" "$RUN/data/media/animefilms"

# ONLY the three paths + test-harness plumbing (images, dev ports, fakes). No keys, no tokens,
# no MG_HOST, no modes.
cat > "$RUN/stack.env" <<EOF
MG_APPDATA=$RUN/appdata
MG_WORK=$RUN/work
MG_DATA_ROOT=$RUN/data
MG_CORE_IMAGE=$MG_CORE_IMAGE
MG_SONARR_IMAGE=$MG_SONARR_IMAGE
MG_RADARR_IMAGE=$MG_RADARR_IMAGE
MG_ENCODE_IMAGE=$MG_ENCODE_IMAGE
MG_DEV_CORE_PORT=28100
MG_DEV_RECOVERY_PORT=28101
MG_DEV_ENCODE_PORT=28000
MG_DEV_INDEXER_PORT=29117
MG_DEV_SAB_PORT=28080
MG_DEV_FAKES_DIR=$ROOT/tests/integration/fakes
MG_DEV_FIXTURES_DIR=$RUN/fixtures
EOF

if [[ -n "${MG_TEST_CA_BUNDLE:-}" && -s "${MG_TEST_CA_BUNDLE}" ]]; then
  cp "$MG_TEST_CA_BUNDLE" "$RUN/ca-bundle.crt"; chmod a+r "$RUN/ca-bundle.crt"
  {
    echo "services:"
    for s in mediaguard-sonarr-series mediaguard-sonarr-anime mediaguard-radarr-films mediaguard-radarr-animefilms; do
      echo "  $s:"
      echo "    volumes: [\"$RUN/ca-bundle.crt:/etc/ssl/certs/ca-certificates.crt:ro\"]"
    done
  } > "$RUN/compose.ca.yml"
  COMPOSE+=(-f "$RUN/compose.ca.yml")
fi
SERVICES=(mediaguard-core mediaguard-recovery mediaguard-encode mediaguard-sonarr-series mediaguard-sonarr-anime
          mediaguard-radarr-films mediaguard-radarr-animefilms fakeindexer fakesab)
printf '%s\n' "${COMPOSE[@]}" > "$RUN/compose-cmd"
printf '%s\n' "${SERVICES[@]}" > "$RUN/services"

"${COMPOSE[@]}" config -q
start=$(date +%s)
"${COMPOSE[@]}" up -d "${SERVICES[@]}"
echo ">> zero-config stack up after $(( $(date +%s) - start ))s"

set +e
MG_STACK_RUN="$RUN" MG_STACK_PROJECT="$PROJECT" python3 -m pytest -v -rA --durations=0 -p no:cacheprovider \
  --junitxml "$RUN/junit.xml" "$HERE/test_zero_config.py" "$@" 2>&1 | tee "$RUN/pytest.log"
rc=${PIPESTATUS[0]}
set -e
echo ">> pytest exit code $rc after $(( $(date +%s) - start ))s"
for s in mediaguard-init "${SERVICES[@]}"; do
  "${COMPOSE[@]}" logs --no-color "$s" > "$RUN/logs-$s.txt" 2>&1 || true
done
if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$RUN/appdata" "$RUN/work" "$RUN/data"
fi
exit "$rc"
