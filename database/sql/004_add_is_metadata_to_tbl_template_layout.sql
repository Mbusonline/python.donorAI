-- Ensure tbl_template_layout has an is_metadata flag to indicate metadata processing completion
ALTER TABLE IF EXISTS tbl_template_layout
  ADD COLUMN IF NOT EXISTS is_metadata BOOLEAN NOT NULL DEFAULT FALSE;

