#!/usr/bin/env bash
# MediaGuard fork integration harness.
#
#   tests/integration/forks/run.sh [pytest args...]
#
# Env:
#   SONARR_IMAGE / RADARR_IMAGE  fork images (default mediaguard/sonarr-fork:bc54afb-mg1 / mediaguard/radarr-fork:a96bf7c-mg1)
#   MG_TEST_CA_BUNDLE            optional PEM bundle mounted as the system CA store of the four
#                                instances (needed only behind a TLS-inspecting proxy, so the
#                                instances can reach skyhook.sonarr.tv / api.radarr.video)
#   KEEP_RUNNING=1               do not tear the stack down afterwards
#   REUSE=1                      do not wipe volumes/state before starting
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
RUN="$HERE/.run"
COMPOSE=(docker compose -f docker-compose.yml)

if [[ "${REUSE:-0}" != "1" ]]; then
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$RUN/downloads" "$RUN/media"
fi
mkdir -p "$RUN/fixtures" "$RUN/downloads/complete" \
         "$RUN/media/sonarr-series" "$RUN/media/sonarr-anime" "$RUN/media/radarr-films" "$RUN/media/radarr-animefilms"

if [[ ! -s "$RUN/fixtures/sample.mkv" ]]; then
  echo ">> generating sample.mkv with ffmpeg"
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i color=c=darkblue:s=320x180:r=1 -f lavfi -i anullsrc=r=8000:cl=mono \
    -t 1200 -c:v libx264 -preset ultrafast -g 600 -c:a aac -b:a 8k -metadata:s:a:0 language=eng "$RUN/fixtures/sample.mkv"
fi
chown -R 1000:1000 "$RUN/downloads" "$RUN/media" 2>/dev/null || chmod -R a+rwX "$RUN/downloads" "$RUN/media"
chmod a+r "$RUN/fixtures/sample.mkv"

if [[ -n "${MG_TEST_CA_BUNDLE:-}" && -s "${MG_TEST_CA_BUNDLE}" ]]; then
  cp "$MG_TEST_CA_BUNDLE" "$RUN/ca-bundle.crt"
  {
    echo "services:"
    for s in sonarr-series sonarr-anime radarr-films radarr-animefilms; do
      echo "  $s:"
      echo "    volumes: [\"./.run/ca-bundle.crt:/etc/ssl/certs/ca-certificates.crt:ro\"]"
    done
  } > "$RUN/compose.ca.yml"
  COMPOSE+=(-f "$RUN/compose.ca.yml")
fi
printf '%s\n' "${COMPOSE[@]}" > "$RUN/compose-cmd"

echo ">> images: ${SONARR_IMAGE:-mediaguard/sonarr-fork:bc54afb-mg1} ${RADARR_IMAGE:-mediaguard/radarr-fork:a96bf7c-mg1}"
"${COMPOSE[@]}" up -d
start=$(date +%s)
set +e
python3 -m pytest -v -rA --durations=0 --junitxml "$RUN/junit.xml" "$HERE/test_forks.py" "$@" 2>&1 | tee "$RUN/pytest.log"
rc=${PIPESTATUS[0]}
set -e
echo ">> pytest exit code $rc after $(( $(date +%s) - start ))s"
for s in sonarr-series sonarr-anime radarr-films radarr-animefilms stub-mediaguard fakeindexer fakesab; do
  "${COMPOSE[@]}" logs --no-color "$s" > "$RUN/logs-$s.txt" 2>&1 || true
done
if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
fi
exit "$rc"
