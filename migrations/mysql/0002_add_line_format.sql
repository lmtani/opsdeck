-- Per-filter template used to render each log line from payload fields,
-- e.g. '{method} {resource} -> {status}'. Empty means automatic summary.

ALTER TABLE log_filters
  ADD COLUMN line_format VARCHAR(240) NOT NULL DEFAULT '' AFTER description;
