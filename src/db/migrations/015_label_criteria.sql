-- Migration 015: Convert labels_json format
-- From: ["label1", "label2"]
-- To: [{"name": "label1", "criteria": ""}, ...]
-- This is a data-only migration (column stays TEXT).
-- Run via Python: python -m src.db.migrations.migrate_015
SELECT 1;
