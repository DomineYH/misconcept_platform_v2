-- Preserve every legacy usage value; new invocations leave unknown values NULL.
CREATE TABLE api_usage_log_new (
    id INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES session(id),
    bot_type VARCHAR(20),
    model VARCHAR(50),
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    estimated_cost_usd FLOAT,
    timestamp DATETIME NOT NULL,
    operation VARCHAR(32),
    invocation_id TEXT,
    request_id TEXT,
    run_id VARCHAR(36) REFERENCES generation_run(id),
    owner_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    attempt_no INTEGER CHECK (attempt_no IS NULL OR attempt_no >= 1),
    provider TEXT,
    role TEXT,
    probe_step TEXT,
    credential_revision INTEGER,
    status TEXT,
    error_code TEXT,
    started_at DATETIME,
    first_output_at DATETIME,
    finished_at DATETIME,
    retry_wait_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens INTEGER,
    raw_usage_json TEXT CHECK (raw_usage_json IS NULL OR json_valid(raw_usage_json)),
    pricing_as_of TEXT,
    pricing_source TEXT,
    usage_complete BOOLEAN,
    UNIQUE(invocation_id, attempt_no)
);
INSERT INTO api_usage_log_new (id,session_id,bot_type,model,prompt_tokens,completion_tokens,total_tokens,estimated_cost_usd,timestamp,operation)
SELECT id,session_id,bot_type,model,prompt_tokens,completion_tokens,total_tokens,estimated_cost_usd,timestamp,operation FROM api_usage_log;
DROP TABLE api_usage_log;
ALTER TABLE api_usage_log_new RENAME TO api_usage_log;
CREATE INDEX ix_api_usage_session_id ON api_usage_log(session_id);
CREATE INDEX ix_api_usage_timestamp ON api_usage_log(timestamp);
CREATE INDEX ix_api_usage_bot_type ON api_usage_log(bot_type);

CREATE TABLE model_probe (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES user(id) ON DELETE RESTRICT,
    model_config_id INTEGER NOT NULL REFERENCES model_config(id),
    role TEXT NOT NULL CHECK (role IN ('student','mentor','analysis')),
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    credential_revision INTEGER NOT NULL,
    connection_version INTEGER NOT NULL,
    config_version INTEGER NOT NULL,
    capability_definition_version TEXT NOT NULL,
    role_contract_version TEXT NOT NULL,
    options_json TEXT NOT NULL CHECK (json_valid(options_json)),
    status TEXT NOT NULL CHECK (status IN ('verifying','succeeded','failed','stale')),
    error_code TEXT,
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    UNIQUE(owner_id,request_id)
);
CREATE UNIQUE INDEX ix_probe_active_role ON model_probe(model_config_id,role) WHERE status='verifying';
