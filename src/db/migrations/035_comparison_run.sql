CREATE TABLE comparison_run (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    admin_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    scenario_id INTEGER REFERENCES scenario(id) ON DELETE SET NULL,
    source_version INTEGER,
    source_snapshot JSON NOT NULL,
    source_hash VARCHAR(64) NOT NULL,
    target_role VARCHAR(20) NOT NULL CHECK (target_role IN ('student','mentor','analysis')),
    mode VARCHAR(20) NOT NULL CHECK (mode = 'same_context'),
    canonical_context JSON NOT NULL,
    current_message TEXT,
    context_hash VARCHAR(64) NOT NULL,
    panel_configs JSON NOT NULL,
    request_id VARCHAR(36) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    parent_run_id VARCHAR(36) REFERENCES comparison_run(id) ON DELETE SET NULL,
    created_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('running','finished')),
    CONSTRAINT uq_comparison_request UNIQUE (admin_id,request_id)
);
CREATE UNIQUE INDEX uq_comparison_running_admin ON comparison_run(admin_id)
    WHERE status = 'running';

-- Isolate comparison records while preserving classroom rows and child references.
CREATE TABLE generation_run_new (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    owner_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    session_id INTEGER REFERENCES session(id) ON DELETE CASCADE,
    turn_id VARCHAR(36) NOT NULL,
    operation VARCHAR(20) NOT NULL CHECK (operation IN ('student','mentor','analysis')),
    request_id VARCHAR(36) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    config_hash VARCHAR(64) NOT NULL,
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('openai','anthropic','google')),
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
INSERT INTO generation_run_new (id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,partial_text,result_kind,error_code,started_at,first_output_at,finished_at,mentor_trigger,mentor_reason_summary,plan_json,outcome_json,accepted_report_id,accepted_report_version) SELECT id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,partial_text,result_kind,error_code,started_at,first_output_at,finished_at,mentor_trigger,mentor_reason_summary,plan_json,outcome_json,accepted_report_id,accepted_report_version FROM generation_run;
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
ALTER TABLE api_usage_log ADD COLUMN usage_scope VARCHAR(20) CHECK (usage_scope IN ('classroom','comparison'));
