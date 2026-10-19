-- The installer creates the marker table; only the explicit seed builder fills it.
CREATE TABLE demo_metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    kind TEXT NOT NULL CHECK (kind IN ('demo_seed','demo_visitor')),
    contract_version TEXT NOT NULL,
    content_version TEXT NOT NULL,
    fixture_version TEXT NOT NULL,
    db_schema_version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64 AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'),
    visitor_id TEXT,
    generation INTEGER,
    CHECK ((kind = 'demo_seed' AND visitor_id IS NULL AND generation IS NULL) OR
        (kind = 'demo_visitor' AND visitor_id IS NOT NULL AND length(visitor_id) > 0 AND
         generation IS NOT NULL AND typeof(generation) = 'integer' AND generation >= 1))
);

-- Rebuild only CHECK contracts; the runner preserves dependent rows and schema objects.
CREATE TABLE provider_connection_new (
    id INTEGER PRIMARY KEY,
    provider VARCHAR(20) NOT NULL UNIQUE CHECK (provider IN ('openai','anthropic','google','demo')),
    encrypted_key BLOB,
    nonce BLOB,
    encryption_key_version VARCHAR(64),
    credential_revision INTEGER NOT NULL DEFAULT 0 CHECK (credential_revision >= 0),
    connection_version INTEGER NOT NULL DEFAULT 1 CHECK (connection_version >= 1),
    masked_hint VARCHAR(16),
    enabled BOOLEAN NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    verified_at DATETIME,
    error_code VARCHAR(50),
    updated_by INTEGER REFERENCES user(id) ON DELETE SET NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    catalog_models_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(catalog_models_json)),
    catalog_fetched_at DATETIME,
    catalog_credential_revision INTEGER,
    CONSTRAINT ck_provider_key_material CHECK (
        (provider = 'demo' AND encrypted_key IS NULL AND nonce IS NULL AND encryption_key_version IS NULL AND masked_hint IS NULL AND credential_revision = 0)
        OR (provider != 'demo' AND ((encrypted_key IS NULL AND nonce IS NULL AND encryption_key_version IS NULL AND masked_hint IS NULL AND enabled = 0)
        OR (encrypted_key IS NOT NULL AND length(encrypted_key) >= 17 AND nonce IS NOT NULL AND length(nonce) = 12 AND encryption_key_version IS NOT NULL AND length(encryption_key_version) > 0 AND masked_hint IS NOT NULL)))
    )
);
INSERT INTO provider_connection_new SELECT * FROM provider_connection;
DROP TABLE provider_connection;
ALTER TABLE provider_connection_new RENAME TO provider_connection;

CREATE TABLE provider_audit_log_new (
    id INTEGER PRIMARY KEY,
    actor_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    provider_connection_id INTEGER NOT NULL REFERENCES provider_connection(id),
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('openai','anthropic','google','demo')),
    change_kind VARCHAR(20) NOT NULL,
    previous_credential_revision INTEGER NOT NULL,
    credential_revision INTEGER NOT NULL,
    previous_connection_version INTEGER NOT NULL,
    connection_version INTEGER NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((provider = 'demo' AND change_kind = 'demo_seeded' AND actor_id IS NULL AND
        previous_credential_revision = 0 AND credential_revision = 0 AND
        previous_connection_version = 1 AND connection_version = 1) OR
        (provider != 'demo' AND change_kind IN ('key_saved','key_replaced','enabled','disabled','deleted')))
);
INSERT INTO provider_audit_log_new SELECT * FROM provider_audit_log;
DROP TABLE provider_audit_log;
ALTER TABLE provider_audit_log_new RENAME TO provider_audit_log;

CREATE TABLE generation_run_new (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    owner_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    session_id INTEGER REFERENCES session(id) ON DELETE CASCADE,
    turn_id VARCHAR(36) NOT NULL,
    operation VARCHAR(20) NOT NULL CHECK (operation IN ('student','mentor','analysis')),
    request_id VARCHAR(36) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    config_hash VARCHAR(64) NOT NULL,
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('openai','anthropic','google','demo')),
    model VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('running','completed','failed','cancelled','interrupted','ok','degraded')),
    partial_text TEXT,
    result_kind VARCHAR(20) CHECK (result_kind IN ('message','no_intervention')),
    error_code VARCHAR(50),
    started_at DATETIME NOT NULL,
    first_output_at DATETIME,
    finished_at DATETIME,
    mentor_trigger VARCHAR(10) CHECK (mentor_trigger IN ('manual','auto')),
    mentor_reason_summary TEXT,
    plan_json TEXT,
    outcome_json TEXT,
    accepted_report_id INTEGER,
    accepted_report_version INTEGER,
    comparison_run_id VARCHAR(36) REFERENCES comparison_run(id) ON DELETE CASCADE,
    panel_id VARCHAR(36),
    CHECK ((session_id IS NOT NULL AND comparison_run_id IS NULL AND panel_id IS NULL) OR
        (session_id IS NULL AND comparison_run_id IS NOT NULL AND panel_id IS NOT NULL)),
    CHECK ((operation = 'analysis' AND status != 'completed') OR
        (operation != 'analysis' AND status NOT IN ('ok','degraded'))),
    CONSTRAINT uq_run_request UNIQUE (owner_id,session_id,request_id)
);
INSERT INTO generation_run_new SELECT * FROM generation_run;
DROP TABLE generation_run;
ALTER TABLE generation_run_new RENAME TO generation_run;
CREATE UNIQUE INDEX uq_run_running_student ON generation_run(session_id)
    WHERE operation = 'student' AND status = 'running';
CREATE UNIQUE INDEX uq_run_running_mentor ON generation_run(session_id)
    WHERE operation = 'mentor' AND status = 'running';
CREATE UNIQUE INDEX uq_run_completed_turn ON generation_run(session_id,turn_id,operation)
    WHERE status = 'completed' AND (operation = 'student' OR result_kind = 'message');
CREATE INDEX ix_run_turn ON generation_run(session_id,turn_id,operation);

CREATE UNIQUE INDEX uq_run_running_analysis ON generation_run(session_id)
    WHERE operation = 'analysis' AND status = 'running';

CREATE UNIQUE INDEX uq_run_comparison_request ON generation_run(owner_id,comparison_run_id,panel_id,request_id)
    WHERE comparison_run_id IS NOT NULL;
CREATE UNIQUE INDEX uq_run_comparison_running ON generation_run(comparison_run_id,panel_id)
    WHERE comparison_run_id IS NOT NULL AND status = 'running';
