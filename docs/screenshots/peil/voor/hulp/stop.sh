#!/usr/bin/env bash
# Stopt wat start.sh startte (pid-bestanden in $MGP_WERK/logs).
S=${MGP_WERK:?zet MGP_WERK}
for p in web core nep-arr; do [ -f "$S/logs/$p.pid" ] && kill "$(cat "$S/logs/$p.pid")" 2>/dev/null; rm -f "$S/logs/$p.pid"; done
true
