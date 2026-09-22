-- Migration 020: Add operation column to api_usage_log (Issue #28)
-- Distinguishes 'classification', 'synthesis', 'greeting' calls for cost tracking.
-- NULL for pre-#28 rows (treated as 'classification' by code).

ALTER TABLE api_usage_log ADD COLUMN operation VARCHAR(32) NULL;
