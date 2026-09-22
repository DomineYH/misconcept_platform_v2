-- Migration 014: Add tutor_sensitivity column to scenario table
-- Sensitivity levels: high, medium, low
-- Controls how aggressively the tutor bot intervenes

-- Note: SQLite ALTER TABLE does not support CHECK constraints.
-- The constraint (high/medium/low) is enforced at ORM and API level.
-- Fresh databases via init_schema.py include the CHECK constraint.
ALTER TABLE scenario
ADD COLUMN tutor_sensitivity VARCHAR(10) NOT NULL DEFAULT 'medium';
