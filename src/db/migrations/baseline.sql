-- Frozen schema 023. Future changes belong in numbered migrations.

CREATE TABLE analysis_framework (
	id INTEGER NOT NULL,
	name VARCHAR(100) NOT NULL,
	description TEXT,
	category_name TEXT,
	labels_json TEXT NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE contributor (
	id INTEGER NOT NULL,
	name VARCHAR(100) NOT NULL,
	affiliation VARCHAR(200) NOT NULL,
	bio VARCHAR(2000) NOT NULL,
	phone VARCHAR(50),
	email VARCHAR(200),
	sort_order INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE user_group (
	id INTEGER NOT NULL,
	name VARCHAR(100) NOT NULL,
	description TEXT,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
);

CREATE TABLE user (
	id INTEGER NOT NULL,
	username VARCHAR(50) NOT NULL,
	nickname VARCHAR(30) NOT NULL,
	password_hash VARCHAR(128) NOT NULL,
	role VARCHAR(20) NOT NULL,
	group_id INTEGER,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_user_role CHECK (role IN ('teacher', 'admin')),
	UNIQUE (username),
	FOREIGN KEY(group_id) REFERENCES user_group (id) ON DELETE SET NULL
);

CREATE TABLE prompt_template (
	id INTEGER NOT NULL,
	bot_type VARCHAR(20) NOT NULL CONSTRAINT ck_prompt_bot_type CHECK (bot_type IN ('student', 'tutor')),
	template_name VARCHAR(100) NOT NULL,
	template_text TEXT NOT NULL CONSTRAINT ck_prompt_text_length CHECK (LENGTH(template_text) >= 10 AND LENGTH(template_text) <= 10000),
	version INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	updated_by INTEGER,
	PRIMARY KEY (id),
	FOREIGN KEY(updated_by) REFERENCES user (id)
);

CREATE INDEX ix_prompt_bot_type ON prompt_template (bot_type);

CREATE INDEX ix_prompt_created_at ON prompt_template (created_at);

CREATE TABLE scenario (
	id INTEGER NOT NULL,
	title VARCHAR(200) NOT NULL,
	prompt TEXT NOT NULL,
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
	framework_id INTEGER NOT NULL,
	created_by INTEGER,
	created_at DATETIME NOT NULL,
	deleted_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT ck_scenario_active CHECK (is_active IN (0, 1)),
	CONSTRAINT ck_scenario_sensitivity CHECK (tutor_sensitivity IN ('high', 'medium', 'low')),
	FOREIGN KEY(student_template_id) REFERENCES prompt_template (id) ON DELETE SET NULL,
	FOREIGN KEY(tutor_template_id) REFERENCES prompt_template (id) ON DELETE SET NULL,
	FOREIGN KEY(framework_id) REFERENCES analysis_framework (id) ON DELETE RESTRICT,
	FOREIGN KEY(created_by) REFERENCES user (id)
);

CREATE TABLE scenario_group (
	id INTEGER NOT NULL,
	scenario_id INTEGER NOT NULL,
	group_id INTEGER NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_scenario_group UNIQUE (scenario_id, group_id),
	FOREIGN KEY(scenario_id) REFERENCES scenario (id) ON DELETE CASCADE,
	FOREIGN KEY(group_id) REFERENCES user_group (id) ON DELETE CASCADE
);

CREATE TABLE session (
	id INTEGER NOT NULL,
	scenario_id INTEGER NOT NULL,
	teacher_id INTEGER,
	started_at DATETIME NOT NULL,
	ended_at DATETIME,
	deleted_at DATETIME,
	tutor_intervention_count INTEGER NOT NULL,
	tutor_question_count INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(scenario_id) REFERENCES scenario (id) ON DELETE CASCADE,
	FOREIGN KEY(teacher_id) REFERENCES user (id) ON DELETE SET NULL
);

CREATE INDEX idx_session_deleted ON session (deleted_at);

CREATE INDEX ix_session_ended ON session (ended_at);

CREATE INDEX ix_session_teacher_started ON session (teacher_id, started_at);

CREATE TABLE api_usage_log (
	id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	bot_type VARCHAR(20) NOT NULL,
	model VARCHAR(50) NOT NULL,
	prompt_tokens INTEGER NOT NULL,
	completion_tokens INTEGER NOT NULL,
	total_tokens INTEGER NOT NULL,
	estimated_cost_usd FLOAT NOT NULL,
	timestamp DATETIME NOT NULL,
	operation VARCHAR(32),
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES session (id)
);

CREATE INDEX ix_api_usage_bot_type ON api_usage_log (bot_type);

CREATE INDEX ix_api_usage_session_id ON api_usage_log (session_id);

CREATE INDEX ix_api_usage_timestamp ON api_usage_log (timestamp);

CREATE TABLE message (
	id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	role VARCHAR(20) NOT NULL,
	content TEXT NOT NULL,
	metadata TEXT,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_message_role CHECK (role IN ('teacher', 'student', 'tutor')),
	FOREIGN KEY(session_id) REFERENCES session (id) ON DELETE CASCADE
);

CREATE INDEX ix_message_session_created ON message (session_id, created_at);

CREATE INDEX ix_message_session_role ON message (session_id, role);

CREATE TABLE session_feedback_report (
	id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	version INTEGER NOT NULL,
	model VARCHAR(64) NOT NULL,
	prompt_hash VARCHAR(64) NOT NULL,
	status VARCHAR(16) NOT NULL,
	payload_json TEXT NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_session_feedback_report_status CHECK (status IN ('ok', 'degraded', 'failed')),
	UNIQUE (session_id),
	FOREIGN KEY(session_id) REFERENCES session (id) ON DELETE CASCADE
);

CREATE INDEX ix_session_feedback_report_session ON session_feedback_report (session_id);

CREATE INDEX ix_session_feedback_report_status ON session_feedback_report (status);

CREATE TABLE session_summary (
	id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	distribution_json TEXT NOT NULL,
	feedback TEXT,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES session (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_session_summary_session_id ON session_summary (session_id);

CREATE TABLE ui_event (
	id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	event_type VARCHAR(32) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES user (id) ON DELETE CASCADE,
	FOREIGN KEY(session_id) REFERENCES session (id) ON DELETE CASCADE
);

CREATE INDEX ix_ui_event_event_type ON ui_event (event_type);

CREATE INDEX ix_ui_event_session ON ui_event (session_id);

CREATE TABLE question_analysis (
	id INTEGER NOT NULL,
	message_id INTEGER NOT NULL,
	label VARCHAR(50) NOT NULL,
	grade VARCHAR(10),
	confidence FLOAT,
	meta_json TEXT,
	PRIMARY KEY (id),
	CONSTRAINT ck_confidence_range CHECK ((confidence IS NULL) OR (confidence >= 0.0 AND confidence <= 1.0)),
	FOREIGN KEY(message_id) REFERENCES message (id) ON DELETE CASCADE
);

CREATE INDEX ix_question_analysis_label ON question_analysis (label);

CREATE UNIQUE INDEX ix_question_analysis_message_id ON question_analysis (message_id);
