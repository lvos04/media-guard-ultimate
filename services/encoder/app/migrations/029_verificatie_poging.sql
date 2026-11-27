-- De bestaande tabel blijft de laatste structurele controle voor oudere lezers.
-- Een historische controle bevatte geen pogingnummer of bestandsidentiteit.
CREATE TABLE verificatie_poging (
    job_id INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    hdr_bron INTEGER,
    hdr_resultaat INTEGER,
    dv_bron INTEGER,
    dv_resultaat INTEGER,
    md_gelijk INTEGER,
    audio_bron INTEGER,
    audio_resultaat INTEGER,
    attachments_bron INTEGER,
    attachments_resultaat INTEGER,
    geslaagd INTEGER NOT NULL,
    toelichting TEXT,
    poging INTEGER,
    hervat_sha256 TEXT
);
CREATE INDEX idx_verificatie_poging_bestand ON verificatie_poging(job_id, hervat_sha256);
INSERT INTO verificatie_poging
SELECT v.*, NULL, NULL FROM verificatie v;
