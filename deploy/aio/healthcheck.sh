#!/bin/sh
# Gezond = de supervisor schreef in de laatste 90 s 'ok' (core en web draaien en antwoorden).
# Geen Python-interpreter per controle meer (±20 MB / 50 ms CPU elke 30 s).
f="${MG_AIO_HEALTH_FILE:-/tmp/mediaguard-health}"
[ -f "$f" ] || exit 1
age=$(( $(date +%s) - $(stat -c %Y "$f") ))
[ "$age" -lt 90 ] || { echo "statusbestand $age s oud"; exit 1; }
read -r state rest < "$f"
[ "$state" = "ok" ] || { echo "$state $rest"; exit 1; }
exit 0
