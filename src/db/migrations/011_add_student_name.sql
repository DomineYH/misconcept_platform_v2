-- Migration 011: Add student_name column to scenario table
-- Purpose: Allow per-scenario student character name display in chat UI
ALTER TABLE scenario ADD COLUMN student_name VARCHAR(50) NULL;
