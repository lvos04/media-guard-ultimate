-- AI Decision Advisor (DEEL S). Bewaart nooit ruwe prompts met secrets of absolute paden:
-- ai_cases.payload_json bevat uitsluitend de geminimaliseerde payload (privacy.minimize).

CREATE TABLE ai_provider_configs (
    provider TEXT PRIMARY KEY,
    last_test_at TEXT,
    last_test_ok INTEGER NOT NULL DEFAULT 0,
    last_test_model TEXT,
    last_test_detail TEXT,
    last_test_latency_ms INTEGER,
    models_json TEXT,
    models_fetched_at TEXT,
    models_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE ai_cases (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    status TEXT NOT NULL,
    status_detail TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    minimization_json TEXT NOT NULL DEFAULT '{}',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    target_type TEXT,
    target_id TEXT,
    instance_id TEXT,
    source_module TEXT,
    analysis_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_analyzed_at TEXT
);
CREATE INDEX idx_ai_cases_hash ON ai_cases(payload_hash);
CREATE INDEX idx_ai_cases_status ON ai_cases(status);

CREATE TABLE ai_proposals (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES ai_cases(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    from_cache INTEGER NOT NULL DEFAULT 0,
    valid INTEGER NOT NULL,
    proposal_json TEXT,
    rejection_reasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_ai_proposals_case ON ai_proposals(case_id);
CREATE INDEX idx_ai_proposals_cache ON ai_proposals(cache_key, valid);

CREATE TABLE ai_policy_results (
    id INTEGER PRIMARY KEY,
    proposal_id INTEGER NOT NULL REFERENCES ai_proposals(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL REFERENCES ai_cases(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('log_only','await_approval','auto_execute','blocked')),
    action TEXT,
    executable INTEGER NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    execution_status TEXT NOT NULL DEFAULT 'none',
    execution_result_json TEXT,
    decided_by TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT
);
CREATE INDEX idx_ai_policy_case ON ai_policy_results(case_id);

CREATE TABLE ai_usage (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    period_month TEXT NOT NULL,
    period_day TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT,
    case_id TEXT,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reserved_cost REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL,
    cost_source TEXT,
    latency_ms INTEGER,
    error TEXT
);
CREATE INDEX idx_ai_usage_month ON ai_usage(period_month);
CREATE INDEX idx_ai_usage_day ON ai_usage(period_day);

CREATE TABLE ai_budgets (
    period_key TEXT PRIMARY KEY,
    period_type TEXT NOT NULL,
    timezone TEXT NOT NULL,
    limit_amount REAL,
    currency TEXT NOT NULL,
    spent REAL NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE ai_model_evaluations (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    benchmark_version TEXT NOT NULL,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    schema_valid INTEGER NOT NULL,
    accuracy REAL NOT NULL,
    baseline_accuracy REAL NOT NULL,
    improvement REAL NOT NULL,
    threshold REAL NOT NULL,
    passed INTEGER NOT NULL,
    cost REAL NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE ai_feedback (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES ai_cases(id) ON DELETE CASCADE,
    proposal_id INTEGER,
    verdict TEXT NOT NULL,
    comment TEXT,
    actor TEXT NOT NULL,
    ts TEXT NOT NULL
);

CREATE TABLE ai_consent_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    granted INTEGER NOT NULL,
    consent_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    text_nl TEXT NOT NULL
);

-- Lokale evidence-registry: B1/B2 melden evidence-ID's aan via mediaguard.ai.register_evidence.
CREATE TABLE ai_evidence_index (
    evidence_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    instance_id TEXT,
    source_module TEXT,
    created_at TEXT NOT NULL
);
