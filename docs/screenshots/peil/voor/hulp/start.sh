#!/usr/bin/env bash
# Peil-nulmeting MediaGuard: nep-Sonarr/Radarr + echte core (verse datamap) + echte web-productiebuild, alleen op
# 127.0.0.1. Alleen-lezen hulpscript bij docs/ontwerp/PEIL_MIGRATIE.md; hoort niet bij de app.
# Vooraf: (cd apps/web && npm ci && npm run build); een Python 3.12-venv met `pip install -e 'apps/core[test]'`.
# Omgeving: MGP_REPO (repo-root), MGP_WERK (werkmap buiten de repo), MGP_PY (python van die venv).
set -euo pipefail
REPO=${MGP_REPO:?zet MGP_REPO op de repo-root}
S=${MGP_WERK:?zet MGP_WERK op een werkmap buiten de repo}
PY=${MGP_PY:?zet MGP_PY op de python van een venv met apps/core[test]}
KEY=nep-sleutel-peil-0000000000000000
rm -rf "$S/data" && mkdir -p "$S/data" "$S/logs"
nohup "$PY" -I "$REPO/docs/screenshots/peil/voor/hulp/nep_arr.py" "$REPO" > "$S/logs/nep-arr.log" 2>&1 &
echo $! > "$S/logs/nep-arr.pid"
export MG_BIND=127.0.0.1 MG_DATA_DIR="$S/data" MG_PORT=18190 MG_START_BACKGROUND=0 TZ=Europe/Amsterdam
for i in SONARR_SERIES:18911 SONARR_ANIME:18912 RADARR_FILMS:18913 RADARR_ANIMEFILMS:18914; do
  export "MG_INSTANCE_${i%%:*}_URL=http://127.0.0.1:${i##*:}" "MG_INSTANCE_${i%%:*}_APIKEY=$KEY"; done
cd "$REPO/apps/core"; nohup "$PY" -m mediaguard > "$S/logs/core.log" 2>&1 &
echo $! > "$S/logs/core.pid"
cd "$REPO/apps/web"; MG_WEB_HOST=127.0.0.1 PORT=18200 MG_CORE_URL=http://127.0.0.1:18190 NODE_ENV=production nohup node server.mjs > "$S/logs/web.log" 2>&1 &
echo $! > "$S/logs/web.pid"
for u in http://127.0.0.1:18190/api/v1/health http://127.0.0.1:18200/login; do
  for _ in $(seq 1 60); do curl -fsS -o /dev/null "$u" && break; sleep 1; done; echo "klaar: $u"; done
