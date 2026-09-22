-- Migration: Add API usage tracking table
-- Task 3.1.1: API 사용량 추적 DB 스키마 설계 및 구현
-- Date: 2025-11-07
-- Purpose: Track OpenAI API token usage and costs for analytics

-- ============================================
-- Table: api_usage_log
-- Purpose: Track token usage and cost for each API call
-- ============================================
CREATE TABLE IF NOT EXISTS api_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES session(id),
    bot_type VARCHAR(20) NOT NULL CHECK(bot_type IN ('student', 'tutor')),
    model VARCHAR(50) NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================
-- Indexes for query optimization
-- ============================================

-- Index for session-based queries
CREATE INDEX IF NOT EXISTS ix_api_usage_session_id
    ON api_usage_log(session_id);

-- Index for time-range queries
CREATE INDEX IF NOT EXISTS ix_api_usage_timestamp
    ON api_usage_log(timestamp);

-- Index for bot-type filtering
CREATE INDEX IF NOT EXISTS ix_api_usage_bot_type
    ON api_usage_log(bot_type);

-- ============================================
-- Column descriptions:
-- ============================================
-- id: Primary key (auto-increment)
-- session_id: Foreign key to session table
-- bot_type: 'student' or 'tutor' for bot identification
-- model: OpenAI model name (e.g., 'gpt-4o', 'gpt-4o-mini')
-- prompt_tokens: Number of tokens in the prompt
-- completion_tokens: Number of tokens in the completion
-- total_tokens: Sum of prompt_tokens + completion_tokens
-- estimated_cost_usd: Calculated cost in USD (6 decimal places)
-- timestamp: When the API call was logged (UTC)
-- ============================================
