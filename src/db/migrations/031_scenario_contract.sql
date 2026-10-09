-- Execute only after the runner validates private preservation/restore evidence.
CREATE TABLE scenario_new (
    id INTEGER PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    subject VARCHAR(100),
    is_active INTEGER NOT NULL CHECK (is_active IN (0,1)),
    created_by INTEGER REFERENCES user(id),
    created_at DATETIME NOT NULL,
    deleted_at DATETIME,
    target_grade VARCHAR(100),
    status VARCHAR(10) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published')),
    config_schema_version INTEGER NOT NULL DEFAULT 1 CHECK (config_schema_version = 1),
    config_version INTEGER NOT NULL DEFAULT 1 CHECK (config_version >= 1),
    config_json JSON NOT NULL CHECK (json_valid(config_json) AND json_type(config_json) = 'object'),
    review_required BOOLEAN NOT NULL DEFAULT 0 CHECK (review_required IN (0,1)),
    review_reasons JSON NOT NULL DEFAULT '[]',
    conversion_provenance_json JSON,
    updated_at DATETIME
);
INSERT INTO scenario_new (id,title,subject,is_active,created_by,created_at,deleted_at,target_grade,status,config_schema_version,config_version,config_json,review_required,review_reasons,conversion_provenance_json,updated_at)
SELECT id,title,subject,is_active,created_by,created_at,deleted_at,target_grade,status,config_schema_version,config_version,config_json,review_required,review_reasons,conversion_provenance_json,updated_at FROM scenario;
DROP TABLE scenario;
ALTER TABLE scenario_new RENAME TO scenario;
CREATE INDEX idx_scenario_active ON scenario(is_active);
CREATE INDEX idx_scenario_deleted ON scenario(deleted_at);
DROP TABLE prompt_template;
DROP TABLE analysis_framework;
