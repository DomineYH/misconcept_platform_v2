CREATE TABLE comparison_conversation (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    admin_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    scenario_id INTEGER REFERENCES scenario(id) ON DELETE SET NULL,
    source_version INTEGER NOT NULL,
    source_snapshot JSON NOT NULL,
    source_hash VARCHAR(64) NOT NULL,
    panel_configs JSON NOT NULL,
    state_json JSON NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    request_id VARCHAR(36) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    created_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    CONSTRAINT uq_conversation_request UNIQUE(admin_id,request_id)
);
ALTER TABLE comparison_run ADD COLUMN conversation_id VARCHAR(36)
    REFERENCES comparison_conversation(id) ON DELETE CASCADE;
CREATE INDEX ix_comparison_run_conversation_id ON comparison_run(conversation_id);
