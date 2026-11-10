#!/usr/bin/env bash
# MediaGuard full-stack integration test: the REAL production compose
# (deploy/truenas/compose.yaml) + the dev overlay (deploy/dev/compose.dev.yaml).
#
#   tests/integration/stack/run.sh [pytest args...]
#
# Brings up: mediaguard-core, mediaguard-recovery, 4 fork instances (2 images), mediaguard-encode
# (if the image exists), mediaguard-web (only if its image exists), fake Newznab indexer and fake
# SABnzbd. No stub hook server: core IS the hook server. All data lives in ./.run (test media only).
# mediaguard-init runs first (compose dependency). The zero-config variant (nothing pre-seeded)
# is run_zero_config.sh.
#
# Env:
#   MG_CORE_IMAGE / MG_SONARR_IMAGE / MG_RADARR_IMAGE / MG_ENCODE_IMAGE / MG_WEB_IMAGE  image overrides
#   MG_TEST_CA_BUNDLE   PEM bundle mounted as CA store of the forks (TLS-inspecting proxy only;
#                       the forks need skyhook.sonarr.tv / api.radarr.video for metadata)
#   KEEP_RUNNING=1      leave the stack up afterwards (inspect with: docker compose -p mgstack ...)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
RUN="$HERE/.run"
PROJECT=mgstack
UIDGID=568

: "${MG_CORE_IMAGE:=mediaguard/core:dev}"
: "${MG_SONARR_IMAGE:=mediaguard/sonarr-fork:bc54afb-mg1}"
: "${MG_RADARR_IMAGE:=mediaguard/radarr-fork:a96bf7c-mg1}"
: "${MG_ENCODE_IMAGE:=mediaguard/encode:dev}"
: "${MG_WEB_IMAGE:=mediaguard/web:dev}"

COMPOSE=(docker compose -p "$PROJECT" --env-file "$RUN/stack.env"
         -f "$ROOT/deploy/truenas/compose.yaml" -f "$ROOT/deploy/dev/compose.dev.yaml"
         -f "$HERE/compose.old.yaml")

# ---- clean previous run -------------------------------------------------------------------
if [[ -f "$RUN/stack.env" ]]; then
  "${COMPOSE[@]}" --profile web down -v --remove-orphans >/dev/null 2>&1 || true
fi
rm -rf "$RUN/appdata" "$RUN/work" "$RUN/data" "$RUN"/logs-*.txt
mkdir -p "$RUN/fixtures" "$RUN/work/encode/staging" "$RUN/work/quarantine" \
         "$RUN/data/downloads/tv" "$RUN/data/downloads/tv-anime" "$RUN/data/downloads/movies" "$RUN/data/downloads/movies-anime" \
         "$RUN/data/media/tv" "$RUN/data/media/anime" "$RUN/data/media/movies" "$RUN/data/media/animefilms"
for d in core encode sonarr-series sonarr-anime radarr-films radarr-animefilms old-sonarr old-radarr; do mkdir -p "$RUN/appdata/$d"; done

if [[ ! -s "$RUN/fixtures/sample.mkv" ]]; then
  if [[ -s "$ROOT/tests/integration/forks/.run/fixtures/sample.mkv" ]]; then
    cp "$ROOT/tests/integration/forks/.run/fixtures/sample.mkv" "$RUN/fixtures/sample.mkv"
  else
    echo ">> generating sample.mkv with ffmpeg"
    ffmpeg -hide_banner -loglevel error -y -f lavfi -i color=c=darkblue:s=320x180:r=1 -f lavfi -i anullsrc=r=8000:cl=mono \
      -t 1200 -c:v libx264 -preset ultrafast -g 600 -c:a aac -b:a 8k -metadata:s:a:0 language=eng "$RUN/fixtures/sample.mkv"
  fi
fi
chmod a+r "$RUN/fixtures/sample.mkv"
# Sleutels vooraf vastleggen (test-only waarden) zodat de tests ze kennen; mediaguard-init
# laat bestaande sleutelbestanden ongemoeid (zelfde pad als in productie).
mkdir -p "$RUN/appdata/secrets/internal"
printf '%s\n' "stack-test-internal-token-0123456789abcdef" > "$RUN/appdata/secrets/internal/token"
seed_key() { mkdir -p "$RUN/appdata/secrets/$1"; printf '%s\n' "$2" > "$RUN/appdata/secrets/$1/apikey"; printf '%s\n' "$3" > "$RUN/appdata/secrets/$1/hookkey"; }
seed_key sonarr-series 5e1a0000000000000000000000000011 stack-hook-sonarr-series-0123456789abcdef
seed_key sonarr-anime 5e1a0000000000000000000000000012 stack-hook-sonarr-anime-0123456789abcdef
seed_key radarr-films 5e1a0000000000000000000000000013 stack-hook-radarr-films-0123456789abcdef
seed_key radarr-animefilms 5e1a0000000000000000000000000014 stack-hook-radarr-animefilms-0123456789ab
chown -R "$UIDGID:$UIDGID" "$RUN/appdata" "$RUN/work" "$RUN/data"

# ---- env (test-only values, never real secrets) -------------------------------------------
# No keys/tokens/modes here (ADR-011): keys come from the pre-seeded files above; library writes
# are unlocked through the API by test_stack.py (POST /recovery/library-write).
cat > "$RUN/stack.env" <<EOF
TZ=Europe/Amsterdam
MG_UID=$UIDGID
MG_GID=$UIDGID
MG_HOST=127.0.0.1
MG_APPDATA=$RUN/appdata
MG_WORK=$RUN/work
MG_DATA_ROOT=$RUN/data
MG_HOOK_TIMEOUT_MS=5000
MG_HOOK_CIRCUIT_BREAK_SECONDS=0
MG_SONARR_ANIME_FAIL_POLICY=closed
MG_RADARR_ANIMEFILMS_FAIL_POLICY=closed
MG_WEB_PORT=30080
MG_ENCODE_MIN_STABLE_SECONDS=5
MG_PORT_SONARR_SERIES=38989
MG_PORT_SONARR_ANIME=38990
MG_PORT_RADARR_FILMS=37878
MG_PORT_RADARR_ANIMEFILMS=37879
MG_DEV_CORE_PORT=28100
MG_DEV_RECOVERY_PORT=28101
MG_DEV_ENCODE_PORT=28000
MG_DEV_INDEXER_PORT=29117
MG_DEV_SAB_PORT=28080
MG_DEV_FAKES_DIR=$ROOT/tests/integration/fakes
MG_DEV_FIXTURES_DIR=$RUN/fixtures
MG_CORE_IMAGE=$MG_CORE_IMAGE
MG_SONARR_IMAGE=$MG_SONARR_IMAGE
MG_RADARR_IMAGE=$MG_RADARR_IMAGE
MG_ENCODE_IMAGE=$MG_ENCODE_IMAGE
MG_WEB_IMAGE=$MG_WEB_IMAGE
EOF

# ---- optional overlays ------------------------------------------------------------------
if [[ -n "${MG_TEST_CA_BUNDLE:-}" && -s "${MG_TEST_CA_BUNDLE}" ]]; then
  cp "$MG_TEST_CA_BUNDLE" "$RUN/ca-bundle.crt"; chmod a+r "$RUN/ca-bundle.crt"
  {
    echo "services:"
    for s in mediaguard-sonarr-series mediaguard-sonarr-anime mediaguard-radarr-films mediaguard-radarr-animefilms old-sonarr old-radarr; do
      echo "  $s:"
      echo "    volumes: [\"$RUN/ca-bundle.crt:/etc/ssl/certs/ca-certificates.crt:ro\"]"
    done
  } > "$RUN/compose.ca.yml"
  COMPOSE+=(-f "$RUN/compose.ca.yml")
fi
SERVICES=(mediaguard-core mediaguard-recovery mediaguard-sonarr-series mediaguard-sonarr-anime
          mediaguard-radarr-films mediaguard-radarr-animefilms fakeindexer fakesab
          old-sonarr old-radarr old-sonarr-spy old-radarr-spy)
if docker image inspect "$MG_ENCODE_IMAGE" >/dev/null 2>&1; then
  SERVICES+=(mediaguard-encode); echo ">> encoder image $MG_ENCODE_IMAGE present: included"
else
  echo ">> encoder image $MG_ENCODE_IMAGE missing: mediaguard-encode left out"
fi
if docker image inspect "$MG_WEB_IMAGE" >/dev/null 2>&1; then
  COMPOSE+=(--profile web); SERVICES+=(mediaguard-web); echo ">> web image $MG_WEB_IMAGE present: included"
else
  echo ">> web image $MG_WEB_IMAGE missing: mediaguard-web left out"
fi
printf '%s\n' "${COMPOSE[@]}" > "$RUN/compose-cmd"
printf '%s\n' "${SERVICES[@]}" > "$RUN/services"

echo ">> compose config check"
"${COMPOSE[@]}" config -q
start=$(date +%s)
"${COMPOSE[@]}" up -d "${SERVICES[@]}"
echo ">> stack up after $(( $(date +%s) - start ))s"

set +e
python3 -m pytest -v -rA --durations=0 -p no:cacheprovider --junitxml "$RUN/junit.xml" "$HERE/test_stack.py" "$HERE/test_migration.py" "$@" 2>&1 | tee "$RUN/pytest.log"
rc=${PIPESTATUS[0]}
set -e
echo ">> pytest exit code $rc after $(( $(date +%s) - start ))s"
for s in "${SERVICES[@]}"; do
  "${COMPOSE[@]}" logs --no-color "$s" > "$RUN/logs-$s.txt" 2>&1 || true
done
if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$RUN/appdata" "$RUN/work" "$RUN/data"
fi
exit "$rc"
