-- Verbeterplan: 'verify'/'repair' zijn geen handmatige taken meer (set-and-forget).
-- Bestaande 'review_needed'-regels gaan naar de automatische route of 'alleen info'.
UPDATE optimization_items SET status='auto_identity'
 WHERE status='review_needed' AND action='verify';
UPDATE optimization_items SET status='kept_info'
 WHERE status='review_needed';
