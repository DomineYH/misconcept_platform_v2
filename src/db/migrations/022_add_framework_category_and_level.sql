-- Migration 022: Add framework category_name and label level support (Issue #30)
-- Adds category_name to analysis_framework, grade to question_analysis,
-- and backfills level: null into existing labels_json dict items.

ALTER TABLE analysis_framework ADD COLUMN category_name TEXT NULL;

ALTER TABLE question_analysis ADD COLUMN grade VARCHAR(10) NULL;

-- Step A: Transform flat string arrays to dict format with level: null
-- e.g. ["A","B"] → [{"name":"A","criteria":"","level":null}, ...]
UPDATE analysis_framework
SET labels_json = (
    SELECT json_group_array(
        json_object('name', je.value, 'criteria', '', 'level', json('null'))
    )
    FROM json_each(labels_json) AS je
)
WHERE labels_json IS NOT NULL
  AND json_valid(labels_json)
  AND json_type(labels_json, '$[0]') = 'text';

-- Step B: Add level: null to dict items that lack it (idempotent)
UPDATE analysis_framework
SET labels_json = (
    SELECT json_group_array(
        CASE
            WHEN json_extract(je.value, '$.level') IS NOT NULL THEN je.value
            ELSE json_insert(je.value, '$.level', json('null'))
        END
    )
    FROM json_each(labels_json) AS je
)
WHERE labels_json IS NOT NULL
  AND json_valid(labels_json)
  AND json_type(labels_json, '$[0]') = 'object'
  AND EXISTS (
    SELECT 1 FROM json_each(labels_json) AS je2
    WHERE json_extract(je2.value, '$.level') IS NULL
  );
