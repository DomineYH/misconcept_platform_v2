-- Add turn identity and durable generation attempts; legacy links stay NULL.
CREATE TABLE generation_run (
    id VARCHAR(36) PRIMARY KEY NOT NULL,
    owner_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    turn_id VARCHAR(36) NOT NULL,
    operation VARCHAR(20) NOT NULL CHECK (operation IN ('student','mentor')),
    request_id VARCHAR(36) NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    config_hash VARCHAR(64) NOT NULL,
    provider VARCHAR(20) NOT NULL CHECK (provider = 'openai'),
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
CREATE UNIQUE INDEX uq_run_running_student ON generation_run(session_id)
    WHERE operation = 'student' AND status = 'running';
CREATE UNIQUE INDEX uq_run_running_mentor ON generation_run(session_id)
    WHERE operation = 'mentor' AND status = 'running';
CREATE UNIQUE INDEX uq_run_completed_turn ON generation_run(session_id,turn_id,operation)
    WHERE status = 'completed';
CREATE INDEX ix_run_turn ON generation_run(session_id,turn_id,operation);

ALTER TABLE message ADD COLUMN turn_id VARCHAR(36);
ALTER TABLE message ADD COLUMN turn_index INTEGER;
ALTER TABLE message ADD COLUMN generation_run_id VARCHAR(36) REFERENCES generation_run(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX uq_message_turn_role ON message(session_id,turn_id,role)
    WHERE turn_id IS NOT NULL;
CREATE UNIQUE INDEX uq_message_teacher_index ON message(session_id,turn_index)
    WHERE role = 'teacher' AND turn_index IS NOT NULL;
CREATE UNIQUE INDEX uq_message_run ON message(generation_run_id)
    WHERE generation_run_id IS NOT NULL;
CREATE INDEX ix_message_completed_student ON message(session_id,turn_index DESC)
    WHERE role = 'student' AND turn_id IS NOT NULL;
