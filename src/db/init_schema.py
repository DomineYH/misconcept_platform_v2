"""Database schema initialization script."""

import asyncio

from sqlalchemy import text

from src.db.connection import engine

# SQL schema from data-model.md
SCHEMA_SQL = """
-- Enable WAL mode for better concurrent performance
PRAGMA journal_mode=WAL;

-- User Group table
CREATE TABLE IF NOT EXISTS user_group (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- User table
CREATE TABLE IF NOT EXISTS user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  nickname TEXT NOT NULL,
  password_hash TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK(role IN
    ('teacher','admin')) DEFAULT 'teacher',
  group_id INTEGER REFERENCES user_group(id)
    ON DELETE SET NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_user_group_id
  ON user(group_id);

-- Analysis Framework table
CREATE TABLE IF NOT EXISTS analysis_framework (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  category_name TEXT,
  labels_json TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Scenario table
CREATE TABLE IF NOT EXISTS scenario (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  prompt TEXT NOT NULL,
  student_profile TEXT,
  student_name VARCHAR(50) NULL,
  subject VARCHAR(100) NULL,
  problem_situation TEXT NULL,
  greeting_message TEXT NULL,
  video_url VARCHAR(500) NULL,
  video_transcript TEXT NULL,
  is_active INTEGER NOT NULL DEFAULT 1
    CHECK(is_active IN (0, 1)),
  framework_id INTEGER NOT NULL
    REFERENCES analysis_framework(id),
  -- Bot configuration overrides (optional)
  chat_model VARCHAR(50) NULL,
  chat_temperature REAL NULL
    CHECK(chat_temperature IS NULL OR
      (chat_temperature >= 0 AND chat_temperature <= 2)),
  tutor_intervention_threshold INTEGER NULL
    CHECK(tutor_intervention_threshold IS NULL OR
      (tutor_intervention_threshold BETWEEN 1 AND 10)),
  tutor_sensitivity VARCHAR(10) NOT NULL DEFAULT 'medium'
    CHECK(tutor_sensitivity IN ('high', 'medium', 'low')),
  -- Template foreign keys
  student_template_id INTEGER NOT NULL REFERENCES prompt_template(id),
  tutor_template_id INTEGER NULL REFERENCES prompt_template(id),
  created_by INTEGER REFERENCES user(id),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  deleted_at DATETIME NULL
);

CREATE INDEX IF NOT EXISTS idx_scenario_active
  ON scenario(is_active);
CREATE INDEX IF NOT EXISTS idx_scenario_deleted
  ON scenario(deleted_at);
CREATE INDEX IF NOT EXISTS idx_scenario_framework
  ON scenario(framework_id);
CREATE INDEX IF NOT EXISTS idx_scenario_student_template
  ON scenario(student_template_id);
CREATE INDEX IF NOT EXISTS idx_scenario_tutor_template
  ON scenario(tutor_template_id);

-- Session table
CREATE TABLE IF NOT EXISTS session (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario_id INTEGER NOT NULL REFERENCES scenario(id)
    ON DELETE CASCADE,
  teacher_id INTEGER REFERENCES user(id)
    ON DELETE SET NULL,
  started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  ended_at DATETIME,
  deleted_at DATETIME NULL,
  tutor_intervention_count INTEGER NOT NULL DEFAULT 0,
  tutor_question_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_session_teacher_started
  ON session(teacher_id, started_at);
CREATE INDEX IF NOT EXISTS idx_session_ended
  ON session(ended_at);
CREATE INDEX IF NOT EXISTS idx_session_deleted
  ON session(deleted_at);

-- Message table
CREATE TABLE IF NOT EXISTS message (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL
    REFERENCES session(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN
    ('student','teacher','tutor')),
  content TEXT NOT NULL,
  metadata TEXT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_message_session_created
  ON message(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_message_session_role
  ON message(session_id, role);

-- Question Analysis table
CREATE TABLE IF NOT EXISTS question_analysis (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL UNIQUE
    REFERENCES message(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  grade VARCHAR(10),
  confidence REAL CHECK(confidence BETWEEN 0.0 AND 1.0
    OR confidence IS NULL),
  meta_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_question_analysis_label
  ON question_analysis(label);

-- Session Summary table
CREATE TABLE IF NOT EXISTS session_summary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL UNIQUE
    REFERENCES session(id) ON DELETE CASCADE,
  distribution_json TEXT NOT NULL,
  feedback TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Chatbot Configuration table (Phase 1 - P0)
CREATE TABLE IF NOT EXISTS chatbot_config (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_key TEXT NOT NULL UNIQUE,
  config_value TEXT NOT NULL,
  config_type TEXT NOT NULL CHECK(config_type IN
    ('string','float','int','bool')),
  description TEXT,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_by INTEGER REFERENCES user(id)
);

-- Chatbot Configuration Audit Log (Phase 1 - P0 Security)
CREATE TABLE IF NOT EXISTS chatbot_config_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  config_key TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT NOT NULL,
  changed_by INTEGER NOT NULL REFERENCES user(id),
  changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  ip_address TEXT
);

CREATE INDEX IF NOT EXISTS idx_config_audit_changed_at
  ON chatbot_config_audit(changed_at);
CREATE INDEX IF NOT EXISTS idx_config_audit_key
  ON chatbot_config_audit(config_key);

-- API Usage Log table (Task 3.1.1)
CREATE TABLE IF NOT EXISTS api_usage_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES session(id),
  bot_type TEXT NOT NULL CHECK(bot_type IN ('student','tutor')),
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL,
  estimated_cost_usd REAL NOT NULL,
  operation VARCHAR(32) NULL,
  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_api_usage_session
  ON api_usage_log(session_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_timestamp
  ON api_usage_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_api_usage_bot_type
  ON api_usage_log(bot_type);

-- Prompt Template table (Task 3.2.1)
CREATE TABLE IF NOT EXISTS prompt_template (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_type TEXT NOT NULL CHECK(bot_type IN ('student', 'tutor')),
  template_name TEXT NOT NULL,
  template_text TEXT NOT NULL
    CHECK(LENGTH(template_text) >= 10 AND LENGTH(template_text) <= 10000),
  version INTEGER NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by INTEGER REFERENCES user(id)
);

CREATE INDEX IF NOT EXISTS ix_prompt_bot_type
  ON prompt_template(bot_type);
CREATE INDEX IF NOT EXISTS ix_prompt_created_at
  ON prompt_template(created_at);

-- Scenario Group join table (access control)
CREATE TABLE IF NOT EXISTS scenario_group (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario_id INTEGER NOT NULL
    REFERENCES scenario(id) ON DELETE CASCADE,
  group_id INTEGER NOT NULL
    REFERENCES user_group(id) ON DELETE CASCADE,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(scenario_id, group_id)
);

CREATE INDEX IF NOT EXISTS ix_sg_scenario
  ON scenario_group(scenario_id);
CREATE INDEX IF NOT EXISTS ix_sg_group
  ON scenario_group(group_id);

-- Contributor table (About page - developer/maintainer info)
CREATE TABLE IF NOT EXISTS contributor (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  affiliation TEXT NOT NULL,
  bio TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_contributor_sort_order
  ON contributor(sort_order);

-- Session Feedback Report table (Issue #28)
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

-- UI Event table (Issue #28)
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
"""


async def init_schema():
    """Execute schema creation SQL."""
    async with engine.begin() as conn:
        # Split SQL by semicolon and execute each statement
        statements = [
            stmt.strip() for stmt in SCHEMA_SQL.split(";") if stmt.strip()
        ]
        for stmt in statements:
            await conn.execute(text(stmt))
    print("Database schema initialized successfully")


if __name__ == "__main__":
    asyncio.run(init_schema())
