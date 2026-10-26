-- Initial schema: links, saved log filters and key/value settings.
-- created_at columns hold ISO-8601 UTC strings (lexicographic order == chronological).

CREATE TABLE IF NOT EXISTS links (
  id CHAR(32) NOT NULL PRIMARY KEY,
  title VARCHAR(80) NOT NULL,
  url TEXT NOT NULL,
  description VARCHAR(240) NOT NULL DEFAULT '',
  image_url TEXT NOT NULL,
  category VARCHAR(40) NOT NULL DEFAULT '',
  created_at VARCHAR(48) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS log_filters (
  id CHAR(32) NOT NULL PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  project VARCHAR(80) NOT NULL,
  expression TEXT NOT NULL,
  freshness VARCHAR(8) NOT NULL DEFAULT '1h',
  `limit` INT NOT NULL DEFAULT 50,
  description VARCHAR(160) NOT NULL DEFAULT '',
  created_at VARCHAR(48) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS settings (
  name VARCHAR(64) NOT NULL PRIMARY KEY,
  value TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
