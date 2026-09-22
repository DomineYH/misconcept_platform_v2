-- Migration 019: Add session_feedback_report table (Issue #28)
-- Stores structured LLM synthesis output separate from SessionSummary.feedback
-- which remains a human-readable one-line sentence.

CREATE TABLE IF NOT EXISTS session_feedback_report (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL UNIQUE REFERENCES session(id) ON DELETE CASCADE,
  version INTEGER NOT NULL DEFAULT 1,
  model VARCHAR(64) NOT NULL,
  prompt_hash VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('ok', 'degraded', 'failed')),
  payload_json TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_session_feedback_report_session
  ON session_feedback_report(session_id);

CREATE INDEX IF NOT EXISTS ix_session_feedback_report_status
  ON session_feedback_report(status);
