#!/usr/bin/env bash
# Bouwnummer (0–99) van de Android-app binnen de huidige MediaGuard-versie: het aantal commits
# sinds de versie in deploy/release-manifest.json voor het laatst veranderde.
#
#   scripts/android-build-number.sh [<commit>]      (standaard HEAD; vraagt volledige historie)
#
# build.gradle.kts maakt daarvan versionCode = versie*100 + bouwnummer. Zo is elke handmatige
# of tussentijdse build een echte update (Android, Obtainium), blijft de release-build van een
# versie hoger dan alles van de vorige versie, en levert dezelfde commit altijd hetzelfde getal.
set -euo pipefail
rev="${1:-HEAD}"
root="$(git rev-parse --show-toplevel)"
manifest="deploy/release-manifest.json"
# alleen de bovenste "version"-regel (twee spaties inspringing), niet die van onderdelen
bump="$(git -C "$root" log -n 1 --format=%H -G '^  "version": ' "$rev" -- "$manifest")"
if [ -z "$bump" ]; then
  echo "android-build-number: versiewijziging niet gevonden (ondiepe clone?); bouwnummer 0" >&2
  echo 0
  exit 0
fi
n="$(git -C "$root" rev-list --count "${bump}..${rev}")"
if [ "$n" -gt 99 ]; then n=99; fi
echo "$n"
