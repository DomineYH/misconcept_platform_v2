-- Migration 006: Add video fields to Scenario table
-- Author: AI Assistant
-- Date: 2025-01-17
-- Purpose: Enable storage of video URL and transcript for each scenario

-- Add video_url column to scenario table
-- Stores URL to the video resource (YouTube, Vimeo, etc.)
-- NULL allowed as not all scenarios require videos
ALTER TABLE scenario ADD COLUMN video_url VARCHAR(500);

-- Add video_transcript column to scenario table
-- Stores full transcript of the video content
-- NULL allowed as not all scenarios have transcripts
ALTER TABLE scenario ADD COLUMN video_transcript TEXT;

-- Note: No constraints or indices needed initially
-- video_url has a reasonable length limit (500 chars)
-- video_transcript is TEXT for unlimited length transcripts
