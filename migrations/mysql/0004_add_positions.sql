-- Manual ordering via drag and drop. Existing rows keep position 0 and
-- fall back to created_at order until the first reorder happens.

ALTER TABLE links
  ADD COLUMN position INT NOT NULL DEFAULT 0 AFTER accent;

ALTER TABLE log_filters
  ADD COLUMN position INT NOT NULL DEFAULT 0 AFTER line_format;
