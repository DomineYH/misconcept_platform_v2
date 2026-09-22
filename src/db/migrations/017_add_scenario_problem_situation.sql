-- Migration 017: Add problem_situation column to scenario table
-- Purpose: Store learner-facing problem description (hidden system prompt from preservice teachers)
ALTER TABLE scenario ADD COLUMN problem_situation TEXT NULL;
