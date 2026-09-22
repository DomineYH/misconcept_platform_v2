-- Migration 018: Add greeting_message column to scenario table
-- Purpose: Store mentor-style greeting shown at chat start (template-only render)
ALTER TABLE scenario ADD COLUMN greeting_message TEXT NULL;
