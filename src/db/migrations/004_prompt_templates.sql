-- Migration 004: Prompt Template Management System (Task 3.2.1)
-- Author: Backend Architect
-- Date: 2025-11-07
-- Purpose: Enable web-based management of StudentBot and TutorBot system prompts

-- Create prompt_template table
CREATE TABLE IF NOT EXISTS prompt_template (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_type TEXT NOT NULL CHECK(bot_type IN ('student', 'tutor')),
  template_name TEXT NOT NULL,
  template_text TEXT NOT NULL
    CHECK(LENGTH(template_text) >= 10 AND LENGTH(template_text) <= 10000),
  version INTEGER NOT NULL DEFAULT 1,
  is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1)),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by INTEGER REFERENCES user(id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS ix_prompt_bot_type
  ON prompt_template(bot_type);

-- Unique constraint: Only 1 active template per bot_type (SQLite partial index)
CREATE UNIQUE INDEX IF NOT EXISTS ix_prompt_active
  ON prompt_template(bot_type) WHERE is_active = 1;

-- Index for version history queries
CREATE INDEX IF NOT EXISTS ix_prompt_created_at
  ON prompt_template(created_at);

-- Optional: Seed initial data from existing prompt files
-- Run this manually after migration if needed:
-- python -c "import asyncio; from src.db.seed import seed_prompts; asyncio.run(seed_prompts())"
