#!/usr/bin/env bash
# MediaGuard recovery fault injection in the REAL core image (roles core + recovery).
#   tests/fault-injection/run.sh [pytest args...]
# Scratch library only (.run/media, tmpfs volumes); never real media.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$HERE/.run"
export MG_CORE_IMAGE="${MG_CORE_IMAGE:-mediaguard/core:dev}"
COMPOSE=(docker compose -f "$HERE/compose.yaml")
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
rm -rf "$RUN/appdata" "$RUN/work" "$RUN/media"
mkdir -p "$RUN/appdata" "$RUN/work/staging" "$RUN/media/tv" "$RUN/fixtures"

# real (small) media: original = H.264, staged "AV1 result" = a different valid encode
gen() { ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc2=s=320x180:r=10" -f lavfi -i "sine=f=$2:r=8000" \
          -t "$3" -c:v libx264 -preset ultrafast -crf "$4" -c:a aac -b:a 16k -metadata:s:a:0 language=eng "$1"; }
[[ -s "$RUN/fixtures/orig.mkv" ]] || gen "$RUN/fixtures/orig.mkv" 440 60 30
[[ -s "$RUN/fixtures/new.mkv" ]] || gen "$RUN/fixtures/new.mkv" 880 60 38
# ~400 MB original for the timing-based kill of the running worker (cross-device copy)
[[ -s "$RUN/fixtures/big.bin" ]] || head -c 400M /dev/urandom > "$RUN/fixtures/big.bin"
chown -R 568:568 "$RUN/appdata" "$RUN/work" "$RUN/media"
chmod -R a+rX "$RUN/fixtures"

"${COMPOSE[@]}" up -d --wait core recovery
start=$(date +%s)
set +e
python3 -m pytest -v -rA --durations=0 -p no:cacheprovider --junitxml "$RUN/junit.xml" "$HERE/test_recovery_faults.py" "$@" 2>&1 | tee "$RUN/pytest.log"
rc=${PIPESTATUS[0]}
set -e
echo ">> pytest exit code $rc after $(( $(date +%s) - start ))s"
"${COMPOSE[@]}" logs --no-color recovery > "$RUN/logs-recovery.txt" 2>&1 || true
"${COMPOSE[@]}" logs --no-color core > "$RUN/logs-core.txt" 2>&1 || true
if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$RUN/appdata" "$RUN/work" "$RUN/media" "$RUN/big-new.bin" "$RUN/fixtures/big.bin"
fi
exit "$rc"
