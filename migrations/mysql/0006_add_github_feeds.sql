-- GitHub feeds: saved views over one org's activity. repos and event_kinds
-- are comma-separated lists (empty = whole org / every kind). last_seen_at
-- is the newest event already seen, as an ISO-8601 UTC string.

CREATE TABLE IF NOT EXISTS github_feeds (
  id CHAR(32) NOT NULL PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  org VARCHAR(40) NOT NULL,
  repos TEXT NOT NULL,
  event_kinds VARCHAR(160) NOT NULL DEFAULT '',
  hide_own TINYINT(1) NOT NULL DEFAULT 0,
  hide_bots TINYINT(1) NOT NULL DEFAULT 1,
  description VARCHAR(160) NOT NULL DEFAULT '',
  last_seen_at VARCHAR(48) NOT NULL DEFAULT '',
  position INT NOT NULL DEFAULT 0,
  created_at VARCHAR(48) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
