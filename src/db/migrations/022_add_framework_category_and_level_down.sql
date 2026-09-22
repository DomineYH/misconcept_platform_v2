-- Migration 022 DOWN: Remove framework category_name, grade, and level from labels_json
-- NOTE: PRAGMA foreign_keys must be set OUTSIDE a transaction (SQLite limitation).
-- If this migration runs inside an outer transaction (migrate.py uses engine.begin),
-- the PRAGMA below is a no-op. In that case, the operator MUST run
-- `PRAGMA foreign_keys=OFF;` against the DB before invoking this DOWN migration,
-- and `PRAGMA foreign_keys=ON;` after, to avoid FK violations from scenario.framework_id.

PRAGMA foreign_keys=OFF;

-- Remove level key from labels_json dict items
UPDATE analysis_framework
SET labels_json = (
    SELECT json_group_array(
        json_remove(je.value, '$.level')
    )
    FROM json_each(labels_json) AS je
)
WHERE labels_json IS NOT NULL
  AND json_valid(labels_json)
  AND json_type(labels_json, '$[0]') = 'object';

-- SQLite doesn't support DROP COLUMN before 3.35.0.
-- Use table-rebuild pattern for analysis_framework.
CREATE TABLE analysis_framework_backup AS SELECT * FROM analysis_framework;

DROP TABLE analysis_framework;

CREATE TABLE analysis_framework (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    labels_json TEXT NOT NULL,
    created_at DATETIME
);

INSERT INTO analysis_framework (id, name, description, labels_json, created_at)
    SELECT id, name, description, labels_json, created_at
    FROM analysis_framework_backup;

DROP TABLE analysis_framework_backup;

-- Rebuild question_analysis to drop grade column
CREATE TABLE question_analysis_backup AS SELECT * FROM question_analysis;

DROP TABLE question_analysis;

CREATE TABLE question_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL UNIQUE REFERENCES message(id) ON DELETE CASCADE,
    label VARCHAR(50) NOT NULL,
    confidence FLOAT,
    meta_json TEXT
);

INSERT INTO question_analysis (id, message_id, label, confidence, meta_json)
    SELECT id, message_id, label, confidence, meta_json
    FROM question_analysis_backup;

DROP TABLE question_analysis_backup;

-- Recreate indexes
CREATE INDEX IF NOT EXISTS ix_question_analysis_message_id
    ON question_analysis(message_id);
CREATE INDEX IF NOT EXISTS ix_question_analysis_label
    ON question_analysis(label);

PRAGMA foreign_keys=ON;
