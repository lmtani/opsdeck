-- Watched filters: watch turns on the background count of new entries and
-- last_seen_at (ISO-8601 UTC text, empty = never) is when the filter was
-- last opened. The live switch now covers logs too, so its setting loses
-- the github_ prefix.

ALTER TABLE log_filters
  ADD COLUMN watch TINYINT(1) NOT NULL DEFAULT 0 AFTER line_format,
  ADD COLUMN last_seen_at VARCHAR(48) NOT NULL DEFAULT '' AFTER watch;

UPDATE IGNORE settings SET name = 'polling' WHERE name = 'github_polling';
