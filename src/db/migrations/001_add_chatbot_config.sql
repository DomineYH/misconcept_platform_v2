-- Migration: Add chatbot configuration tables
-- Phase 1: Global chatbot configuration management
-- Date: 2025-11-06

-- ============================================
-- Table: chatbot_config
-- Purpose: Global chatbot configuration settings
-- ============================================
CREATE TABLE IF NOT EXISTS chatbot_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_key VARCHAR(100) NOT NULL UNIQUE,
    config_value TEXT NOT NULL,
    config_type VARCHAR(20) NOT NULL CHECK(config_type IN ('string', 'float', 'int', 'bool')),
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by INTEGER REFERENCES user(id)
);

-- Index for faster config lookups
CREATE INDEX IF NOT EXISTS idx_chatbot_config_key
    ON chatbot_config(config_key);

-- ============================================
-- Table: chatbot_config_audit
-- Purpose: Audit trail for configuration changes
-- ============================================
CREATE TABLE IF NOT EXISTS chatbot_config_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_key VARCHAR(100) NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_by INTEGER NOT NULL REFERENCES user(id),
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(45)
);

-- Index for audit log queries
CREATE INDEX IF NOT EXISTS idx_chatbot_audit_key
    ON chatbot_config_audit(config_key);
CREATE INDEX IF NOT EXISTS idx_chatbot_audit_timestamp
    ON chatbot_config_audit(changed_at);

-- ============================================
-- Seed default configuration values
-- ============================================
INSERT INTO chatbot_config (config_key, config_value, config_type, description) VALUES
('student_bot.model', 'gpt-4-turbo', 'string', 'StudentBot LLM model'),
('student_bot.temperature', '0.7', 'float', 'StudentBot response creativity (0.0-2.0)'),
('student_bot.max_tokens', '150', 'int', 'StudentBot response length limit'),
('tutor_bot.model', 'gpt-3.5-turbo', 'string', 'TutorBot LLM model'),
('tutor_bot.temperature', '0.3', 'float', 'TutorBot response consistency (0.0-2.0)'),
('tutor_bot.max_tokens', '100', 'int', 'TutorBot response length limit'),
('tutor_bot.intervention_threshold', '3', 'int', 'TutorBot interventions per 10 teacher questions');
