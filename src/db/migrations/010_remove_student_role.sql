-- Migration 010: Remove student role from user table
-- Only teacher and admin roles are allowed.
-- Any existing student-role users are migrated to teacher.

-- 1. Convert existing student users to teacher
UPDATE user SET role = 'teacher' WHERE role = 'student';

-- 2. Recreate user table with updated constraint
CREATE TABLE user_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    nickname TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL CHECK(role IN ('teacher','admin'))
        DEFAULT 'teacher',
    group_id INTEGER REFERENCES user_group(id) ON DELETE SET NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 3. Copy data
INSERT INTO user_new (
    id, username, nickname, password_hash,
    role, group_id, created_at
)
SELECT
    id, username, nickname, password_hash,
    role, group_id, created_at
FROM user;

-- 4. Drop old, rename new
DROP TABLE user;
ALTER TABLE user_new RENAME TO user;

-- 5. Recreate indexes
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_username
    ON user(username);
CREATE INDEX IF NOT EXISTS ix_user_group_id
    ON user(group_id);
