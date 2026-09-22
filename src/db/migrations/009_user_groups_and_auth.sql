-- Migration 009: User Groups and Authentication
-- Adds user_group table, scenario_group join table,
-- and updates user table with username/password/group fields.

-- 1. Create user_group table
CREATE TABLE IF NOT EXISTS user_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Insert default group for existing users
INSERT INTO user_group (name, description)
VALUES ('default', '기본 그룹');

-- 3. Recreate user table (SQLite FK limitation)
CREATE TABLE user_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    nickname TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK(role IN ('teacher','student','admin'))
        DEFAULT 'teacher',
    group_id INTEGER REFERENCES user_group(id) ON DELETE SET NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 4. Migrate data: student_uid -> username
INSERT INTO user_new (
    id, username, nickname, password_hash,
    role, group_id, created_at
)
SELECT
    id, student_uid, nickname, '',
    role,
    (SELECT id FROM user_group WHERE name = 'default'),
    created_at
FROM user;

-- 5. Drop old, rename new
DROP TABLE user;
ALTER TABLE user_new RENAME TO user;

-- 6. Indexes
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_username
    ON user(username);
CREATE INDEX IF NOT EXISTS ix_user_group_id
    ON user(group_id);

-- 7. Create scenario_group join table
CREATE TABLE IF NOT EXISTS scenario_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL
        REFERENCES scenario(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL
        REFERENCES user_group(id) ON DELETE CASCADE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scenario_id, group_id)
);

CREATE INDEX IF NOT EXISTS ix_sg_scenario
    ON scenario_group(scenario_id);
CREATE INDEX IF NOT EXISTS ix_sg_group
    ON scenario_group(group_id);
