-- Migration: Remove chatbot configuration tables
-- Reason: Settings moved to .env for simplification
-- Date: 2025-11-17

-- ============================================
-- Drop tables (audit first for FK constraint)
-- ============================================
DROP TABLE IF EXISTS chatbot_config_audit;
DROP TABLE IF EXISTS chatbot_config;

-- ============================================
-- Remove scenario bot config columns
-- ============================================
-- SQLite doesn't support DROP COLUMN directly
-- Need to recreate table without these columns

-- 1. Create new scenario table without bot config columns
CREATE TABLE scenario_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title VARCHAR(200) NOT NULL,
    prompt TEXT NOT NULL,
    student_profile TEXT,
    framework_id INTEGER REFERENCES framework(id),
    created_by INTEGER NOT NULL REFERENCES user(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
);

-- 2. Copy data from old table
INSERT INTO scenario_new (
    id, title, prompt, student_profile,
    framework_id, created_by, created_at, is_active
)
SELECT
    id, title, prompt, student_profile,
    framework_id, created_by, created_at, is_active
FROM scenario;

-- 3. Drop old table and rename new table
DROP TABLE scenario;
ALTER TABLE scenario_new RENAME TO scenario;

-- 4. Recreate indexes
CREATE INDEX IF NOT EXISTS idx_scenario_framework
    ON scenario(framework_id);
CREATE INDEX IF NOT EXISTS idx_scenario_active
    ON scenario(is_active);
