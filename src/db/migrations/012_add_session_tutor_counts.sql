-- Migration 012: Add tutor state columns to session table
-- These columns track TutorBot intervention/question counts per session.
-- ORM model and init_schema already define them, but no migration existed.

ALTER TABLE session ADD COLUMN tutor_intervention_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE session ADD COLUMN tutor_question_count INTEGER NOT NULL DEFAULT 0;
