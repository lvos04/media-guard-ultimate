#!/usr/bin/env bash
# Seerr compatibility contract tests (DEEL H / R4 / R6.6 / Q3.12).
#
#   tests/contract/seerr/run.sh [pytest args...]
#
# Starts real Seerr + Jellyfin + the four MediaGuard fork instances, runs
# test_seerr_contract.py (which also starts MediaGuard core from the local venv) and tears
# everything down again (containers AND volumes).
#
# Env:
#   SEERR_IMAGE      default ghcr.io/seerr-team/seerr:v3.5.1
#   JELLYFIN_IMAGE   default ghcr.io/jellyfin/jellyfin:12.4
#   SONARR_IMAGE / RADARR_IMAGE  fork images (default ...:bc54afb-mg1 / ...:a96bf7c-mg1)
#   MG_TEST_CA_BUNDLE  optional PEM bundle mounted as CA store in the forks and Seerr
#                      (only needed behind a TLS-inspecting egress proxy; Seerr needs TMDB,
#                      the forks need skyhook.sonarr.tv / api.radarr.video)
#   MG_CORE_PYTHON   python with core's deps (default /tmp/corevenv/bin/python)
#   KEEP_RUNNING=1   leave the stack running afterwards
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
RUN="$HERE/.run"
COMPOSE=(docker compose -f docker-compose.yml)

"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
rm -rf "$RUN/media" "$RUN/core"
mkdir -p "$RUN/fixtures" "$RUN/media/sonarr-series" "$RUN/media/sonarr-anime" \
         "$RUN/media/radarr-films" "$RUN/media/radarr-animefilms"

# tiny but "real" media files: 20 min, 320x180 @ 1 fps (long enough not to count as a sample)
if [[ ! -s "$RUN/fixtures/sample.mkv" ]]; then
  echo ">> generating sample.mkv (H.264)"
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i color=c=darkblue:s=320x180:r=1 -f lavfi -i anullsrc=r=8000:cl=mono \
    -t 1200 -c:v libx264 -preset ultrafast -g 600 -c:a aac -b:a 8k -metadata:s:a:0 language=eng "$RUN/fixtures/sample.mkv"
fi
if [[ ! -s "$RUN/fixtures/sample-av1.mkv" ]]; then
  echo ">> generating sample-av1.mkv (AV1, simulates MediaGuard's AV1 conversion)"
  ffmpeg -hide_banner -loglevel error -y -i "$RUN/fixtures/sample.mkv" -c:v libsvtav1 -preset 12 -crf 50 \
    -c:a copy "$RUN/fixtures/sample-av1.mkv"
fi
chown -R 1000:1000 "$RUN/media" 2>/dev/null || chmod -R a+rwX "$RUN/media"
chmod a+r "$RUN/fixtures/"*.mkv

if [[ -n "${MG_TEST_CA_BUNDLE:-}" && -s "${MG_TEST_CA_BUNDLE}" ]]; then
  cp "$MG_TEST_CA_BUNDLE" "$RUN/ca-bundle.crt"
  chmod a+r "$RUN/ca-bundle.crt"
  {
    echo "services:"
    for s in sonarr-series sonarr-anime radarr-films radarr-animefilms; do
      echo "  $s:"
      echo "    volumes: [\"./.run/ca-bundle.crt:/etc/ssl/certs/ca-certificates.crt:ro\"]"
    done
    echo "  seerr:"
    echo "    volumes: [\"./.run/ca-bundle.crt:/etc/ssl/certs/mg-test-ca.crt:ro\"]"
    echo "    environment: {NODE_EXTRA_CA_CERTS: /etc/ssl/certs/mg-test-ca.crt}"
  } > "$RUN/compose.ca.yml"
  COMPOSE+=(-f "$RUN/compose.ca.yml")
else
  rm -f "$RUN/compose.ca.yml"
fi
printf '%s\n' "${COMPOSE[@]}" > "$RUN/compose-cmd"

echo ">> images: ${SEERR_IMAGE:-ghcr.io/seerr-team/seerr:v3.5.1} ${JELLYFIN_IMAGE:-ghcr.io/jellyfin/jellyfin:12.4}" \
     "${SONARR_IMAGE:-mediaguard/sonarr-fork:bc54afb-mg1} ${RADARR_IMAGE:-mediaguard/radarr-fork:a96bf7c-mg1}"
"${COMPOSE[@]}" up -d
start=$(date +%s)
set +e
python3 -m pytest -v -rA --durations=0 -p no:cacheprovider --junitxml "$RUN/junit.xml" \
  "$HERE/test_seerr_contract.py" "$@" 2>&1 | tee "$RUN/pytest.log"
rc=${PIPESTATUS[0]}
set -e
echo ">> pytest exit code $rc after $(( $(date +%s) - start ))s"
for s in seerr jellyfin sonarr-series sonarr-anime radarr-films radarr-animefilms stub-mediaguard; do
  "${COMPOSE[@]}" logs --no-color "$s" > "$RUN/logs-$s.txt" 2>&1 || true
done
if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
fi
exit "$rc"
