-- Migration 007: Add scenario template foreign keys
-- Author: Backend Architect
-- Date: 2025-02-03
-- Purpose: Link scenarios to prompt templates, remove tutor_enabled column

-- ============================================
-- CRITICAL: SQLite doesn't support ADD COLUMN with FK constraints
-- Must recreate table to add foreign key columns
-- ============================================

-- 1. Create new scenario table with template foreign keys
CREATE TABLE scenario_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    student_profile TEXT,
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
    -- NEW: Template foreign keys
    student_template_id INTEGER NOT NULL
        REFERENCES prompt_template(id)
        ON DELETE RESTRICT,
    tutor_template_id INTEGER NULL
        REFERENCES prompt_template(id)
        ON DELETE RESTRICT,
    created_by INTEGER REFERENCES user(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at DATETIME NULL
);

-- 2. Migrate existing data with template references
-- Set student_template_id to active student template
-- Set tutor_template_id to active tutor template if tutor_enabled was true
INSERT INTO scenario_new (
    id, title, prompt, student_profile,
    video_url, video_transcript, is_active,
    framework_id, chat_model, chat_temperature,
    tutor_intervention_threshold,
    student_template_id, tutor_template_id,
    created_by, created_at, deleted_at
)
SELECT
    s.id, s.title, s.prompt, s.student_profile,
    s.video_url, s.video_transcript, s.is_active,
    s.framework_id, s.chat_model, s.chat_temperature,
    s.tutor_intervention_threshold,
    -- Get active student template ID
    (SELECT id FROM prompt_template
     WHERE bot_type = 'student' AND is_active = 1 LIMIT 1),
    -- Get active tutor template ID if tutor_enabled was true, else NULL
    CASE
        WHEN s.tutor_enabled = 1 THEN
            (SELECT id FROM prompt_template
             WHERE bot_type = 'tutor' AND is_active = 1 LIMIT 1)
        ELSE NULL
    END,
    s.created_by, s.created_at, s.deleted_at
FROM scenario s;

-- 3. Drop old table and rename new table
DROP TABLE scenario;
ALTER TABLE scenario_new RENAME TO scenario;

-- 4. Recreate indexes
CREATE INDEX IF NOT EXISTS idx_scenario_active
    ON scenario(is_active);
CREATE INDEX IF NOT EXISTS idx_scenario_deleted
    ON scenario(deleted_at);
CREATE INDEX IF NOT EXISTS idx_scenario_framework
    ON scenario(framework_id);

-- 5. Create new indexes for template foreign keys
CREATE INDEX IF NOT EXISTS idx_scenario_student_template
    ON scenario(student_template_id);
CREATE INDEX IF NOT EXISTS idx_scenario_tutor_template
    ON scenario(tutor_template_id);
