-- Command shortcuts: a link can launch a local program instead of opening a
-- URL. kind is 'url' (default) or 'command'. The command column holds the
-- shell command line that is run locally when kind is command.

ALTER TABLE links
  ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'url' AFTER url;

ALTER TABLE links
  ADD COLUMN command VARCHAR(1024) NOT NULL DEFAULT '' AFTER kind;
