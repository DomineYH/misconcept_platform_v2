-- Extend durable generation records for analysis; preserve child references.
CREATE TABLE generation_run_new (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    owner_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
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
    CHECK ((operation = 'analysis' AND status != 'completed') OR
        (operation != 'analysis' AND status NOT IN ('ok','degraded'))),
    CONSTRAINT uq_run_request UNIQUE (owner_id,session_id,request_id)
);
INSERT INTO generation_run_new (id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,partial_text,result_kind,error_code,started_at,first_output_at,finished_at,mentor_trigger,mentor_reason_summary) SELECT id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,partial_text,result_kind,error_code,started_at,first_output_at,finished_at,mentor_trigger,mentor_reason_summary FROM generation_run;
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
