-- Migration 013: Add subject column to scenario table
-- Purpose: Store subject/course name for chatbot usage guide
ALTER TABLE scenario ADD COLUMN subject VARCHAR(100) NULL;
