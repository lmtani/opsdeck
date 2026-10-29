-- Log sources: where a filter's logs are read from. kind picks the provider
-- (gcp = Cloud Logging) and config holds its settings as a JSON object,
-- e.g. {"project": "my-project"}. Every project already used by a filter
-- becomes a gcp source named after it, and filters point at sources by id.

CREATE TABLE IF NOT EXISTS log_sources (
  id CHAR(32) NOT NULL PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  config TEXT NOT NULL,
  created_at VARCHAR(48) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO log_sources (id, name, kind, config, created_at)
SELECT REPLACE(UUID(), '-', ''), project, 'gcp', JSON_OBJECT('project', project),
       DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%dT%H:%i:%sZ')
FROM (SELECT DISTINCT project FROM log_filters) AS projects;

ALTER TABLE log_filters
  ADD COLUMN source_id CHAR(32) NOT NULL DEFAULT '' AFTER name;

UPDATE log_filters f JOIN log_sources s ON s.name = f.project
SET f.source_id = s.id;

ALTER TABLE log_filters
  DROP COLUMN project;
