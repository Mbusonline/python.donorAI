-- Link source documents to tbl_report_pipeline; allow tbl_report_document rows before report_id exists.
-- CSV rows set report_type (bar, pie, line); application defaults to bar when null.

ALTER TABLE tbl_report_document
  ALTER COLUMN report_id DROP NOT NULL;

ALTER TABLE tbl_report_document
  ADD COLUMN IF NOT EXISTS report_pipeline_id BIGINT;

-- document_id: add UUID only if the column is missing. If an older migration created BIGINT
-- document_id, leave it — database/migrate_document_uuid.fix_mismatched_report_documents_document_id
-- replaces it after SQL files run.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'tbl_report_document'
      AND column_name = 'document_id'
  ) THEN
    ALTER TABLE tbl_report_document ADD COLUMN document_id UUID;
  END IF;
END$$;

ALTER TABLE tbl_report_document
  ADD COLUMN IF NOT EXISTS report_type TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_tbl_report_document_pipeline'
  ) THEN
    ALTER TABLE tbl_report_document
      ADD CONSTRAINT fk_tbl_report_document_pipeline
      FOREIGN KEY (report_pipeline_id) REFERENCES tbl_report_pipeline(report_pipeline_id)
      ON UPDATE CASCADE ON DELETE SET NULL;
  END IF;
END$$;

DO $$
DECLARE
  dt_parent text;
  dt_rd text;
BEGIN
  SELECT c.data_type INTO dt_parent
  FROM information_schema.columns c
  WHERE c.table_schema = 'public' AND c.table_name = 'tbl_document' AND c.column_name = 'document_id';

  SELECT c.data_type INTO dt_rd
  FROM information_schema.columns c
  WHERE c.table_schema = 'public' AND c.table_name = 'tbl_report_document' AND c.column_name = 'document_id';

  IF dt_parent IS NOT NULL AND dt_rd IS NOT NULL AND dt_parent = dt_rd
     AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_tbl_report_document_document') THEN
    ALTER TABLE tbl_report_document
      ADD CONSTRAINT fk_tbl_report_document_document
      FOREIGN KEY (document_id) REFERENCES tbl_document(document_id)
      ON UPDATE CASCADE ON DELETE SET NULL;
  END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_tbl_report_document_report_pipeline_id
  ON tbl_report_document(report_pipeline_id);

CREATE INDEX IF NOT EXISTS idx_tbl_report_document_document_id
  ON tbl_report_document(document_id);
