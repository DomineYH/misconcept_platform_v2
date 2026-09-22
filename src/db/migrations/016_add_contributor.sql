CREATE TABLE IF NOT EXISTS contributor (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  affiliation TEXT NOT NULL,
  bio TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_contributor_sort_order
  ON contributor(sort_order);
