-- Welke versie van de identiteitsregels een resultaat heeft gemaakt. Oudere twijfel-/
-- afwijkingsresultaten worden na een regelverbetering automatisch opnieuw beoordeeld.
ALTER TABLE identity_results ADD COLUMN engine_version INTEGER NOT NULL DEFAULT 1;
