-- Een code- of beleidswijziging krijgt een eigen meting. Bewaar de oorspronkelijke
-- staatrij voor het upgradebewijs en neem de bekende meettijd over als die er is.
CREATE TABLE ai_veiligheidsmeting (
    id INTEGER PRIMARY KEY,
    waarde TEXT NOT NULL,
    tijd TEXT DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO ai_veiligheidsmeting(waarde, tijd)
SELECT waarde, tijd FROM staat WHERE sleutel = 'ai_veiligheidsfingerprint';
