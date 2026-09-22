-- Rollback migration 020: Remove operation column from api_usage_log
-- SQLite does not support DROP COLUMN before 3.35.0.
-- For SQLite <3.35.0, recreate table without the column.

-- This down migration is a no-op for safety on production SQLite.
-- The extra NULL column is harmless. Only run manual cleanup if needed.
