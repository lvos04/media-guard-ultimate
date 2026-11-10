-- AV1-autopilot: MediaGuard beslist welke bestanden naar de encoder gaan. Bestanden die een
-- poort (identiteit, slot, DV-beleid, …) tegenhield, worden een tijd niet opnieuw geprobeerd.
CREATE TABLE encoding_autopilot_skips (
    path TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    reason_nl TEXT NOT NULL,
    retry_after TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
