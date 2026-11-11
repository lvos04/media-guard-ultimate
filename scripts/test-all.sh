#!/usr/bin/env bash
# Draai alle MediaGuard-testsuites en toon een samenvattingstabel.
#
#   scripts/test-all.sh [--only a,b,...] [--skip a,b,...] [--fast]
#
# Suites (in volgorde):
#   core        apps/core: ruff + pytest
#   encoder     services/encoder: volledige suite in het toolchain-image (--target test)
#   web         apps/web: lint, typecheck, build
#   web-e2e     apps/web: Playwright tegen een echte core (vereist gebouwde web + core-venv)
#   forks       tests/integration/forks/run.sh (fork-images vereist)
#   stack       tests/integration/stack/run.sh (alle images vereist)
#   faults      tests/fault-injection/run.sh (core-image vereist)
#   seerr       tests/contract/seerr/run.sh (fork-images + Seerr/Jellyfin-images vereist)
#   mediaserver tests/contract/mediaserver/run.sh (echte Emby + Jellyfin 12.x, vorige release → kandidaat)
# --fast = alleen core, web (lint/typecheck/build).
#
# Env:
#   MG_CORE_PYTHON     python met core-deps (default: /tmp/corevenv/bin/python, anders python3)
#   MG_TEST_CA_BUNDLE  doorgegeven aan de containersuites (alleen achter TLS-inspecterende proxy)
#   BUILD_CA_BUNDLE    voor het bouwen van het encoder-testimage
#   FFMPEG_SRC_DIR     map met ffmpeg-8.1.3.tar.xz voor het encoder-testimage
#   LOG_DIR            logs per suite (default: mktemp -d)
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALL="core,encoder,web,web-e2e,forks,stack,faults,seerr,mediaserver"
ONLY="$ALL"; SKIP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --skip) SKIP="$2"; shift 2 ;;
    --fast) ONLY="core,web"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "onbekend argument: $1" >&2; exit 2 ;;
  esac
done
PY="${MG_CORE_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x /tmp/corevenv/bin/python ]]; then PY=/tmp/corevenv/bin/python; else PY=python3; fi
fi
LOG_DIR="${LOG_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/mediaguard-tests-XXXX")}"
mkdir -p "$LOG_DIR"
ORDER=(); declare -A RESULT TIME SUMMARY
want() { [[ ",$ONLY," == *",$1,"* && ",$SKIP," != *",$1,"* ]]; }

# laatste pytest-/playwright-samenvattingsregel uit een log
summary_of() {
  local main extra
  main="$(grep -aE '([0-9]+ (passed|failed|error|errors|skipped|xfailed)|All checks passed|Found [0-9]+ error|[0-9]+ (passed|failed|flaky) \(|^web: )' "$1" \
    | tail -1 | sed -E 's/\x1b\[[0-9;]*m//g; s/^=+ //; s/ =+$//' | cut -c1-100)"
  extra="$(grep -a '^ruff: ' "$1" | tail -1)"
  echo "${main}${extra:+; $extra}"
}

suite() {  # suite <naam> <commando...>
  local n="$1"; shift
  want "$n" || return 0
  ORDER+=("$n")
  local log="$LOG_DIR/$n.log" t0; t0=$(date +%s)
  echo ">> [$n] $* (log: $log)"
  ( "$@" ) >"$log" 2>&1
  local rc=$?
  TIME[$n]="$(( $(date +%s) - t0 ))s"
  RESULT[$n]=$([[ $rc == 0 ]] && echo OK || echo "FOUT ($rc)")
  SUMMARY[$n]="$(summary_of "$log")"
  echo "   ${RESULT[$n]} in ${TIME[$n]}: ${SUMMARY[$n]}"
}

core() {  # ruff en pytest allebei draaien; exitcode faalt als één van beide faalt
  cd "$ROOT/apps/core" || return 1
  local lint=0
  "$PY" -m ruff check . || lint=$?
  "$PY" -m pytest -q -rfEs -p no:cacheprovider || return $?
  [[ $lint == 0 ]] || { echo "ruff: FOUT (exit $lint)"; return 3; }
}
encoder() {
  local secret=() ctx=()
  [[ -n "${BUILD_CA_BUNDLE:-}" && -s "${BUILD_CA_BUNDLE}" ]] && secret=(--secret "id=ca_bundle,src=$BUILD_CA_BUNDLE")
  [[ -n "${FFMPEG_SRC_DIR:-}" ]] && ctx=(--build-context "ffmpeg-src=$FFMPEG_SRC_DIR")
  DOCKER_BUILDKIT=1 docker build "${secret[@]}" "${ctx[@]}" --target test \
    --build-arg "BASE_IMAGE=${BASE_IMAGE:-ubuntu:26.04}" -t mediaguard/encode:test "$ROOT/services/encoder" \
    && docker run --rm mediaguard/encode:test python3 -m pytest -q -rfEs -p no:cacheprovider
}
web() {
  cd "$ROOT/apps/web" && { [[ -d node_modules ]] || npm ci --no-audit --no-fund; } \
    && npm run lint && npm run typecheck && npm run build && echo "web: lint, typecheck en build geslaagd"
}
web_e2e() {
  cd "$ROOT/apps/web" && MG_E2E_PYTHON="$PY" npm run e2e
}

suite core core
suite encoder encoder
suite web web
suite web-e2e web_e2e
suite forks "$ROOT/tests/integration/forks/run.sh"
suite stack "$ROOT/tests/integration/stack/run.sh"
suite faults "$ROOT/tests/fault-injection/run.sh"
suite seerr env MG_CORE_PYTHON="$PY" "$ROOT/tests/contract/seerr/run.sh"
suite mediaserver env MG_CORE_PYTHON="$PY" "$ROOT/tests/contract/mediaserver/run.sh"

echo
echo "| suite | resultaat | duur | samenvatting |"
echo "|---|---|---|---|"
rc=0
for n in "${ORDER[@]}"; do
  echo "| $n | ${RESULT[$n]} | ${TIME[$n]} | ${SUMMARY[$n]:-} |"
  [[ "${RESULT[$n]}" == OK ]] || rc=1
done
echo; echo "logs: $LOG_DIR"
exit $rc
