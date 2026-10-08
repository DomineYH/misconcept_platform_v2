-- Provider secrets and public revisions; deleting a key preserves the row.
CREATE TABLE provider_connection (
    id INTEGER PRIMARY KEY,
    provider VARCHAR(20) NOT NULL UNIQUE CHECK (provider IN ('openai','anthropic','google')),
    encrypted_key BLOB,
    nonce BLOB,
    encryption_key_version VARCHAR(64),
    credential_revision INTEGER NOT NULL DEFAULT 0 CHECK (credential_revision >= 0),
    connection_version INTEGER NOT NULL DEFAULT 1 CHECK (connection_version >= 1),
    masked_hint VARCHAR(16),
    enabled BOOLEAN NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    verified_at DATETIME,
    error_code VARCHAR(50),
    updated_by INTEGER REFERENCES user(id) ON DELETE SET NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_provider_key_material CHECK (
        (encrypted_key IS NULL AND nonce IS NULL AND encryption_key_version IS NULL AND masked_hint IS NULL AND enabled = 0)
        OR (encrypted_key IS NOT NULL AND length(encrypted_key) >= 17 AND nonce IS NOT NULL AND length(nonce) = 12 AND encryption_key_version IS NOT NULL AND length(encryption_key_version) > 0 AND masked_hint IS NOT NULL)
    )
);
INSERT INTO provider_connection (id,provider) VALUES (1,'openai'), (2,'anthropic'), (3,'google');

CREATE TABLE provider_audit_log (
    id INTEGER PRIMARY KEY,
    actor_id INTEGER REFERENCES user(id) ON DELETE SET NULL,
    provider_connection_id INTEGER NOT NULL REFERENCES provider_connection(id),
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('openai','anthropic','google')),
    change_kind VARCHAR(20) NOT NULL CHECK (change_kind IN ('key_saved','key_replaced','enabled','disabled','deleted')),
    previous_credential_revision INTEGER NOT NULL,
    credential_revision INTEGER NOT NULL,
    previous_connection_version INTEGER NOT NULL,
    connection_version INTEGER NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
