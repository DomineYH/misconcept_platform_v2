-- Migration 008: Remove is_active from prompt_template
-- Date: 2026-02-06
-- Reason: Templates are now selected per-scenario, activation is unnecessary

-- Step 1: Drop the unique partial index on is_active
DROP INDEX IF EXISTS ix_prompt_active;

-- Step 2: Recreate prompt_template without is_active column
-- (SQLite doesn't support DROP COLUMN, must recreate table)

CREATE TABLE prompt_template_new (
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

-- Copy data (excluding is_active)
INSERT INTO prompt_template_new (id, bot_type, template_name, template_text, version, created_at, updated_at, updated_by)
SELECT id, bot_type, template_name, template_text, version, created_at, updated_at, updated_by
FROM prompt_template;

-- Swap tables
DROP TABLE prompt_template;
ALTER TABLE prompt_template_new RENAME TO prompt_template;

-- Recreate remaining indexes
CREATE INDEX IF NOT EXISTS ix_prompt_bot_type ON prompt_template(bot_type);
CREATE INDEX IF NOT EXISTS ix_prompt_created_at ON prompt_template(created_at);
