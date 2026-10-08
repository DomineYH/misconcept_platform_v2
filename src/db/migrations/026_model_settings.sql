-- Catalog caches are reference data, independent of role verification.
ALTER TABLE provider_connection ADD COLUMN catalog_models_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(catalog_models_json));
ALTER TABLE provider_connection ADD COLUMN catalog_fetched_at DATETIME;
ALTER TABLE provider_connection ADD COLUMN catalog_credential_revision INTEGER;

CREATE TABLE model_config (
    id INTEGER PRIMARY KEY,
    provider_connection_id INTEGER NOT NULL REFERENCES provider_connection(id),
    model_id TEXT NOT NULL CHECK (length(trim(model_id)) > 0),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
    enabled BOOLEAN NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    capabilities_json TEXT CHECK (capabilities_json IS NULL OR json_valid(capabilities_json)),
    capability_definition_version TEXT,
    default_options_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(default_options_json)),
    verification_state TEXT NOT NULL DEFAULT '{"student":{"status":"unverified"},"mentor":{"status":"unverified"},"analysis":{"status":"unverified"}}' CHECK (json_valid(verification_state)),
    config_version INTEGER NOT NULL DEFAULT 1 CHECK (config_version >= 1),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider_connection_id,model_id)
);

CREATE TABLE app_setting (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    settings_version INTEGER NOT NULL DEFAULT 1 CHECK (settings_version >= 1),
    student_model_config_id INTEGER REFERENCES model_config(id),
    mentor_model_config_id INTEGER REFERENCES model_config(id),
    analysis_model_config_id INTEGER REFERENCES model_config(id),
    limits_json TEXT NOT NULL CHECK (json_valid(limits_json)),
    timeouts_json TEXT NOT NULL CHECK (json_valid(timeouts_json)),
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO app_setting (id,limits_json,timeouts_json) VALUES (
    1,'{"total":8,"openai":4,"anthropic":4,"google":4,"admin":3}',
    '{"connect":5,"student_first_output":60,"student_total":180,"mentor_first_output":60,"mentor_total":180,"analysis_total":300,"model_list_total":30}'
);
