-- De geredigeerde stukken hebben een eigen levensduur. De operationele audit
-- en wachtrij kunnen hun normale bewaartermijn blijven volgen.
CREATE TABLE connector_audit_evidence (
    id INTEGER PRIMARY KEY,
    job_target TEXT,
    ts TEXT NOT NULL,
    package TEXT NOT NULL
);
CREATE INDEX idx_connector_audit_job ON connector_audit_evidence(job_target, id);
CREATE INDEX idx_audit_job ON audit_events(target_type, target_id, id);

CREATE TABLE connector_evidence_origin (schema_version TEXT NOT NULL);

CREATE TABLE connector_job_evidence (
    id INTEGER PRIMARY KEY,
    job_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    evidence_app_version TEXT,
    payload TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result TEXT,
    audit_id INTEGER REFERENCES connector_audit_evidence(id)
);

-- Ook wanneer de hoogste operationele rij vervalt mag een volgende handeling
-- geen eerder uitgegeven bewijs-ID krijgen.
INSERT INTO system_state(key,value_json,updated_at)
SELECT 'evidence:audit_floor', CAST(MAX(audit_cutoff) AS TEXT), CURRENT_TIMESTAMP
FROM job_evidence_snapshot
HAVING MAX(audit_cutoff) > (SELECT COALESCE(MAX(id),0) FROM audit_events);

CREATE TRIGGER evidence_deleted_audit AFTER DELETE ON audit_events BEGIN
    INSERT INTO system_state(key,value_json,updated_at)
    VALUES ('evidence:audit_floor',CAST(OLD.id AS TEXT),CURRENT_TIMESTAMP)
    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
        WHERE CAST(system_state.value_json AS INTEGER) < OLD.id;
END;

CREATE TRIGGER evidence_deleted_job AFTER DELETE ON jobs BEGIN
    INSERT INTO system_state(key,value_json,updated_at)
    VALUES ('evidence:job_floor',CAST(OLD.id AS TEXT),CURRENT_TIMESTAMP)
    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
        WHERE CAST(system_state.value_json AS INTEGER) < OLD.id;
END;
