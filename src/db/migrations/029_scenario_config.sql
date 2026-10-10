-- Add authoring storage; legacy content/status is converted explicitly in S2-04.
CREATE TABLE scenario_new (
	id INTEGER NOT NULL,
	title VARCHAR(200) NOT NULL,
	prompt TEXT,
	student_profile TEXT,
	student_name VARCHAR(50),
	subject VARCHAR(100),
	problem_situation TEXT,
	greeting_message TEXT,
	video_url VARCHAR(500),
	video_transcript TEXT,
	is_active INTEGER NOT NULL,
	chat_model VARCHAR(50),
	chat_temperature FLOAT,
	tutor_intervention_threshold INTEGER,
	tutor_sensitivity VARCHAR(10) NOT NULL,
	student_template_id INTEGER,
	tutor_template_id INTEGER,
	framework_id INTEGER,
	created_by INTEGER,
	created_at DATETIME NOT NULL,
	deleted_at DATETIME,
	target_grade VARCHAR(100),
    status VARCHAR(10) NOT NULL DEFAULT 'published' CHECK (status IN ('draft','published')),
    config_schema_version INTEGER NOT NULL DEFAULT 1 CHECK (config_schema_version = 1),
    config_version INTEGER NOT NULL DEFAULT 1 CHECK (config_version >= 1),
    config_json JSON,
    review_required BOOLEAN NOT NULL DEFAULT 0 CHECK (review_required IN (0,1)),
    review_reasons JSON NOT NULL DEFAULT '[]',
    conversion_provenance_json JSON,
    updated_at DATETIME,
    PRIMARY KEY (id),
	CONSTRAINT ck_scenario_active CHECK (is_active IN (0, 1)),
	CONSTRAINT ck_scenario_sensitivity CHECK (tutor_sensitivity IN ('high', 'medium', 'low')),
	FOREIGN KEY(student_template_id) REFERENCES prompt_template (id) ON DELETE SET NULL,
	FOREIGN KEY(tutor_template_id) REFERENCES prompt_template (id) ON DELETE SET NULL,
	FOREIGN KEY(framework_id) REFERENCES analysis_framework (id) ON DELETE RESTRICT,
	FOREIGN KEY(created_by) REFERENCES user (id)
);
INSERT INTO scenario_new (id,title,prompt,student_profile,student_name,subject,problem_situation,greeting_message,video_url,video_transcript,is_active,chat_model,chat_temperature,tutor_intervention_threshold,tutor_sensitivity,student_template_id,tutor_template_id,framework_id,created_by,created_at,deleted_at) SELECT id,title,prompt,student_profile,student_name,subject,problem_situation,greeting_message,video_url,video_transcript,is_active,chat_model,chat_temperature,tutor_intervention_threshold,tutor_sensitivity,student_template_id,tutor_template_id,framework_id,created_by,created_at,deleted_at FROM scenario;
DROP TABLE scenario;
ALTER TABLE scenario_new RENAME TO scenario;

CREATE INDEX IF NOT EXISTS idx_scenario_active ON scenario(is_active);
CREATE INDEX IF NOT EXISTS idx_scenario_deleted ON scenario(deleted_at);
CREATE INDEX IF NOT EXISTS idx_scenario_framework ON scenario(framework_id);
CREATE INDEX IF NOT EXISTS idx_scenario_student_template ON scenario(student_template_id);
CREATE INDEX IF NOT EXISTS idx_scenario_tutor_template ON scenario(tutor_template_id);

ALTER TABLE session ADD COLUMN config_snapshot_json JSON;
ALTER TABLE session ADD COLUMN config_hash VARCHAR(64);
ALTER TABLE session ADD COLUMN source_scenario_version INTEGER;
ALTER TABLE session ADD COLUMN snapshot_origin VARCHAR(24) CHECK (snapshot_origin IN ('native','legacy_reconstructed'));
ALTER TABLE session ADD COLUMN snapshot_created_at DATETIME;
