-- Migration 005: Add metadata field to Message table
-- Author: AI Assistant
-- Date: 2025-11-08
-- Purpose: Enable storage of misconception analysis results and other metadata in student messages

-- Add metadata column to message table
-- Stores JSON-formatted analysis data (misconception tracking, etc.)
-- NULL allowed for backward compatibility with existing messages
ALTER TABLE message ADD COLUMN metadata TEXT;

-- Note: No index needed initially as metadata is primarily for storage
-- If future queries filter/search by metadata content, consider adding:
-- CREATE INDEX IF NOT EXISTS idx_message_metadata ON message(metadata) WHERE metadata IS NOT NULL;
