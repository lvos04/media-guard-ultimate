-- Kwaliteitsondergrens voor CPU-encodes (rapport 4): komt een SVT-AV1-resultaat onder de
-- bitrate-ondergrens voor zijn resolutie/dynamiek, dan één herkansing met een doelbitrate
-- op de ondergrens. Deze vlag voorkomt een lus; een tweede keer te laag = origineel houden.
ALTER TABLE job ADD COLUMN kwaliteit_herkansd INTEGER NOT NULL DEFAULT 0;
