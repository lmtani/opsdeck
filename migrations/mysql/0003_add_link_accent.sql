-- Preset cover chosen for the card when no image is set (e.g. 'crimson',
-- 'emerald'). Empty means the automatic title-hash gradient.

ALTER TABLE links
  ADD COLUMN accent VARCHAR(24) NOT NULL DEFAULT '' AFTER category;
