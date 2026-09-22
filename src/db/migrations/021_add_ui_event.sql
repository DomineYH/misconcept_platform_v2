-- Migration 021: Add ui_event table for engagement instrumentation (Issue #28)
-- Separates UI interaction events from API cost logs (api_usage_log).

CREATE TABLE IF NOT EXISTS ui_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
  session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  event_type VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_ui_event_session
  ON ui_event(session_id);

CREATE INDEX IF NOT EXISTS ix_ui_event_event_type
  ON ui_event(event_type);
