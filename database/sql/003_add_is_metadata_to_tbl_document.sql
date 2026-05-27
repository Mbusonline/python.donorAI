-- Ensure tbl_document has an is_metadata flag to indicate metadata processing completion
ALTER TABLE IF EXISTS tbl_document
  ADD COLUMN IF NOT EXISTS is_metadata BOOLEAN NOT NULL DEFAULT FALSE;

