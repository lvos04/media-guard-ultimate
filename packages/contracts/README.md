# Contracten tussen MediaGuard-componenten

Bindende interfaces tussen werkstromen. Wijzigingen alleen via de integrator (hoofdsessie).

- `fork-hooks.md` — Sonarr/Radarr-fork ⇄ mediaguard-core (pre-grab, pre-import, webhooks).
- `encoder-bridge.md` — mediaguard-core ⇄ mediaguard-encode (AV1-engine uit `lvos04/av1`).
- `instances.md` — instance-registry en arr-client interface binnen core (Python).
