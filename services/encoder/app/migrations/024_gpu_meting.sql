-- Rapport 7: kleine sleutel-waardetabel voor zelfgeleerde engine-toestand (GPU-plekkenmeting,
-- proef met een tweede CPU-plek, teller voor HDR-metadata uit de GPU-runtime).
CREATE TABLE IF NOT EXISTS staat (
    sleutel TEXT PRIMARY KEY,
    waarde TEXT NOT NULL,
    tijd TEXT
);
