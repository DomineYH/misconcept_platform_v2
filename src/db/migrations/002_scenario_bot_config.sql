-- Migration: Add scenario-level bot configuration overrides
-- Phase 2: Scenario-specific chatbot configuration management
-- Date: 2025-11-07
-- Purpose: Allow per-scenario customization of bot behavior

-- ============================================
-- Add bot configuration override columns to scenario table
-- NULL values indicate use of global chatbot_config settings
-- ============================================

-- 1. chat_model: Override StudentBot LLM model for this scenario
ALTER TABLE scenario ADD COLUMN chat_model VARCHAR(50) NULL;

-- 2. chat_temperature: Override StudentBot temperature (0.0-2.0)
ALTER TABLE scenario ADD COLUMN chat_temperature REAL NULL
  CHECK (chat_temperature IS NULL OR (chat_temperature >= 0 AND chat_temperature <= 2));

-- 3. tutor_enabled: Enable/disable TutorBot for this scenario
ALTER TABLE scenario ADD COLUMN tutor_enabled BOOLEAN NOT NULL DEFAULT 1;

-- 4. tutor_intervention_threshold: Override TutorBot intervention frequency (1-10)
ALTER TABLE scenario ADD COLUMN tutor_intervention_threshold INTEGER NULL
  CHECK (tutor_intervention_threshold IS NULL OR (tutor_intervention_threshold BETWEEN 1 AND 10));

-- ============================================
-- Column descriptions:
-- ============================================
-- chat_model: Scenario-specific LLM model (e.g., 'gpt-4-turbo', 'gpt-3.5-turbo')
--   NULL = use global student_bot.model setting
--
-- chat_temperature: Controls response creativity/randomness
--   NULL = use global student_bot.temperature setting
--   Range: 0.0 (deterministic) to 2.0 (highly creative)
--
-- tutor_enabled: Whether TutorBot should intervene in this scenario
--   1 = enabled (default), 0 = disabled
--   Always explicit, never NULL
--
-- tutor_intervention_threshold: How often TutorBot should intervene
--   NULL = use global tutor_bot.intervention_threshold setting
--   Range: 1 (very frequent) to 10 (very rare)
-- ============================================
