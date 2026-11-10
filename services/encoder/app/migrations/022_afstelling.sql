-- Uitvoeringsafstelling die eerder alleen via omgevingsvariabelen kon (rapport 5): 4K-maxima,
-- nachtvenster, vastloper, VMAF-grens, bitrate-ondergrens, hardwaredecode, harde CPU-grens.
CREATE TABLE IF NOT EXISTS afstelling (
    sleutel TEXT PRIMARY KEY,
    waarde TEXT NOT NULL
);

-- Kwaliteitsmeting na de encode (rapport 5, M4): VMAF-score en vergelijkingsbeelden.
ALTER TABLE job ADD COLUMN vmaf_score REAL;
ALTER TABLE job ADD COLUMN vergelijking TEXT;

-- Kwaliteitsmeting staat voortaan standaard aan: zonder meting geen kwaliteitspoort.
UPDATE functie SET ingeschakeld=1 WHERE sleutel='vmaf';
