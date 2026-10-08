-- Widen only provider identity; preserve every run and child reference.
CREATE TABLE generation_run_new (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    owner_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    turn_id VARCHAR(36) NOT NULL,
    operation VARCHAR(20) NOT NULL CHECK (operation IN ('student','mentor')),
    request_id VARCHAR(36) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    config_hash VARCHAR(64) NOT NULL,
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('openai','anthropic','google')),
    model VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('running','completed','failed','cancelled','interrupted')),
    partial_text TEXT,
    result_kind VARCHAR(20) CHECK (result_kind IN ('message','no_intervention')),
    error_code VARCHAR(50),
    started_at DATETIME NOT NULL,
    first_output_at DATETIME,
    finished_at DATETIME,
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
    WHERE status = 'completed';
CREATE INDEX ix_run_turn ON generation_run(session_id,turn_id,operation);

