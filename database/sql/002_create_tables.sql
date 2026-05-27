-- Core application schema
-- Notes:
-- - Uses BIGSERIAL primary keys for simplicity.
-- - Uses timestamptz for audit timestamps.
-- - Keeps created_by/updated_by as BIGINT nullable (no FK) to avoid cyclic dependencies.
-- - Requires pgvector extension (see 001_enable_pgvector.sql).

-- ==============
-- Users & access
-- ==============

CREATE TABLE IF NOT EXISTS tbl_user (
  user_id        BIGSERIAL PRIMARY KEY,
  full_name      TEXT NOT NULL,
  email          TEXT NOT NULL UNIQUE,
  password       TEXT NOT NULL,
  mobile         TEXT,
  role_id        BIGINT,
  status         TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Rejected', 'Approved')),
  last_login_at  TIMESTAMPTZ,
  created_by     BIGINT,
  updated_by     BIGINT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tbl_role (
  role_id             BIGSERIAL PRIMARY KEY,
  title               TEXT NOT NULL,
  is_system_generated  BOOLEAN NOT NULL DEFAULT FALSE,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_by          BIGINT,
  updated_by          BIGINT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'fk_tbl_user_role'
  ) THEN
    ALTER TABLE tbl_user
      ADD CONSTRAINT fk_tbl_user_role
      FOREIGN KEY (role_id) REFERENCES tbl_role(role_id)
      ON UPDATE CASCADE ON DELETE SET NULL;
  END IF;
END$$;

-- ============
-- Pages & ACLs
-- ============

CREATE TABLE IF NOT EXISTS tbl_page (
  page_id     BIGSERIAL PRIMARY KEY,
  title       TEXT NOT NULL,
  slug        TEXT NOT NULL UNIQUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tbl_page_blocks (
  page_block_id   BIGSERIAL PRIMARY KEY,
  page_id         BIGINT NOT NULL,
  title           TEXT NOT NULL,
  slug            TEXT NOT NULL,
  description     TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_page_blocks_page
    FOREIGN KEY (page_id) REFERENCES tbl_page(page_id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT uq_tbl_page_blocks_page_slug UNIQUE (page_id, slug)
);

CREATE TABLE IF NOT EXISTS tbl_permission (
  permission_id  BIGSERIAL PRIMARY KEY,
  page_id        BIGINT,
  page_block_id  BIGINT,
  role_id        BIGINT NOT NULL,
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_by     BIGINT,
  updated_by     BIGINT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_permission_role
    FOREIGN KEY (role_id) REFERENCES tbl_role(role_id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT fk_tbl_permission_page
    FOREIGN KEY (page_id) REFERENCES tbl_page(page_id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT fk_tbl_permission_page_block
    FOREIGN KEY (page_block_id) REFERENCES tbl_page_blocks(page_block_id)
    ON UPDATE CASCADE ON DELETE CASCADE
);

-- =============
-- Models/prompt
-- =============

CREATE TABLE IF NOT EXISTS tbl_model (
  model_id      BIGSERIAL PRIMARY KEY,
  title         TEXT NOT NULL,
  provider      TEXT NOT NULL,
  private_key   TEXT,
  pricing       JSONB,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_by    BIGINT,
  updated_by    BIGINT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tbl_prompt (
  prompt_id     BIGSERIAL PRIMARY KEY,
  model_id      BIGINT,
  title         TEXT NOT NULL,
  description   TEXT,
  version       TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_by    BIGINT,
  updated_by    BIGINT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_prompt_model
    FOREIGN KEY (model_id) REFERENCES tbl_model(model_id)
    ON UPDATE CASCADE ON DELETE SET NULL
);

-- =========
-- Donors
-- =========

CREATE TABLE IF NOT EXISTS tbl_donor (
  donor_id     BIGSERIAL PRIMARY KEY,
  title        TEXT NOT NULL,
  logo         TEXT,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_by   BIGINT,
  updated_by   BIGINT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tbl_donor_contact (
  donor_content_id  BIGSERIAL PRIMARY KEY,
  donor_id          BIGINT NOT NULL,
  title             TEXT,
  mobile            TEXT,
  created_by        BIGINT,
  updated_by        BIGINT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_donor_contact_donor
    FOREIGN KEY (donor_id) REFERENCES tbl_donor(donor_id)
    ON UPDATE CASCADE ON DELETE CASCADE
);

-- =========
-- Programs
-- =========

CREATE TABLE IF NOT EXISTS tbl_program (
  program_id   BIGSERIAL PRIMARY KEY,
  donor_id     BIGINT NOT NULL,
  title        TEXT NOT NULL,
  start_date   DATE,
  end_date     DATE,
  remark       TEXT,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_by   BIGINT,
  updated_by   BIGINT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_program_donor
    FOREIGN KEY (donor_id) REFERENCES tbl_donor(donor_id)
    ON UPDATE CASCADE ON DELETE CASCADE
);

-- ==========
-- Documents
-- ==========

CREATE TABLE IF NOT EXISTS tbl_document (
  document_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  program_id    BIGINT,
  donor_id      BIGINT,
  file_name     TEXT NOT NULL,
  file_path     TEXT NOT NULL,
  file_type     TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_by    BIGINT,
  updated_by    BIGINT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_document_program
    FOREIGN KEY (program_id) REFERENCES tbl_program(program_id)
    ON UPDATE CASCADE ON DELETE SET NULL,
  CONSTRAINT fk_tbl_document_donor
    FOREIGN KEY (donor_id) REFERENCES tbl_donor(donor_id)
    ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tbl_document_metadata (
  document_metadata_id  BIGSERIAL PRIMARY KEY,
  document_id           UUID NOT NULL,
  meta_data             JSONB,
  vector_data           VECTOR(1536),
  is_active             BOOLEAN NOT NULL DEFAULT TRUE,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_document_metadata_document
    FOREIGN KEY (document_id) REFERENCES tbl_document(document_id)
    ON UPDATE CASCADE ON DELETE CASCADE
);

-- One metadata row per document for upsert workflows
CREATE UNIQUE INDEX IF NOT EXISTS uq_tbl_document_metadata_document_id
  ON tbl_document_metadata(document_id);

-- ==========
-- Templates
-- ==========

CREATE TABLE IF NOT EXISTS tbl_template (
  template_id   BIGSERIAL PRIMARY KEY,
  title         TEXT NOT NULL,
  description   TEXT,
  version       TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_by    BIGINT,
  updated_by    BIGINT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tbl_template_layout (
  template_layout_id  BIGSERIAL PRIMARY KEY,
  template_id         BIGINT NOT NULL,
  file_name           TEXT NOT NULL,
  file_path           TEXT NOT NULL,
  file_type           TEXT,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  status              TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Rejected', 'Approved')),
  created_by          BIGINT,
  updated_by          BIGINT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_template_layout_template
    FOREIGN KEY (template_id) REFERENCES tbl_template(template_id)
    ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_template_layout_metadata (
  template_layout_metadata_id  BIGSERIAL PRIMARY KEY,
  template_layout_id           BIGINT NOT NULL,
  meta_data                    JSONB,
  vector_data                  VECTOR(1536),
  version                      TEXT,
  is_active                    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_metadata                  BOOLEAN NOT NULL DEFAULT TRUE,
  created_by                   BIGINT,
  updated_by                   BIGINT,
  CONSTRAINT fk_tbl_template_layout_metadata_layout
    FOREIGN KEY (template_layout_id) REFERENCES tbl_template_layout(template_layout_id)
    ON UPDATE CASCADE ON DELETE CASCADE
);

-- One metadata row per template_layout for upsert workflows
CREATE UNIQUE INDEX IF NOT EXISTS uq_tbl_template_layout_metadata_template_layout_id
  ON tbl_template_layout_metadata(template_layout_id);

-- =================
-- Reporting pipeline
-- =================

CREATE TABLE IF NOT EXISTS tbl_report_pipeline (
  report_pipeline_id  BIGSERIAL PRIMARY KEY,
  title               TEXT NOT NULL,
  sub_title           TEXT,
  donor_id            BIGINT,
  program_id          BIGINT,
  template_layout_id  BIGINT,
  front_page_image    TEXT,
  orientataion        TEXT,
  location            TEXT,
  is_report           BOOLEAN NOT NULL DEFAULT FALSE,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  created_by          BIGINT,
  updated_by          BIGINT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_report_pipeline_donor
    FOREIGN KEY (donor_id) REFERENCES tbl_donor(donor_id)
    ON UPDATE CASCADE ON DELETE SET NULL,
  CONSTRAINT fk_tbl_report_pipeline_program
    FOREIGN KEY (program_id) REFERENCES tbl_program(program_id)
    ON UPDATE CASCADE ON DELETE SET NULL,
  CONSTRAINT fk_tbl_report_pipeline_template_layout
    FOREIGN KEY (template_layout_id) REFERENCES tbl_template_layout(template_layout_id)
    ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tbl_report (
  report_id    BIGSERIAL PRIMARY KEY,
  donor_id     BIGINT,
  program_id   BIGINT,
  title        TEXT NOT NULL,
  file_name    TEXT NOT NULL,
  file_path    TEXT NOT NULL,
  file_type    TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_report_donor
    FOREIGN KEY (donor_id) REFERENCES tbl_donor(donor_id)
    ON UPDATE CASCADE ON DELETE SET NULL,
  CONSTRAINT fk_tbl_report_program
    FOREIGN KEY (program_id) REFERENCES tbl_program(program_id)
    ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tbl_report_document (
  report_document_id  BIGSERIAL PRIMARY KEY,
  report_id           BIGINT NOT NULL,
  file_name           TEXT NOT NULL,
  file_path           TEXT NOT NULL,
  file_type           TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_report_document_report
    FOREIGN KEY (report_id) REFERENCES tbl_report(report_id)
    ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tbl_report_pipeline_model (
  report_pipeline_model_id  BIGSERIAL PRIMARY KEY,
  report_pipeline_id        BIGINT NOT NULL,
  prompt_id                 BIGINT,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_report_pipeline_model_pipeline
    FOREIGN KEY (report_pipeline_id) REFERENCES tbl_report_pipeline(report_pipeline_id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT fk_tbl_report_pipeline_model_prompt
    FOREIGN KEY (prompt_id) REFERENCES tbl_prompt(prompt_id)
    ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tbl_report_cost (
  report_cost_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  report_id                 UUID NOT NULL,
  report_pipeline_model_id  UUID,
  input_token_count         INTEGER NOT NULL DEFAULT 0,
  output_token_count        INTEGER NOT NULL DEFAULT 0,
  pricing                   DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_report_cost_report
    FOREIGN KEY (report_id) REFERENCES tbl_report(report_id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT fk_tbl_report_cost_pipeline_model
    FOREIGN KEY (report_pipeline_model_id) REFERENCES tbl_report_pipeline_model(report_pipeline_model_id)
    ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tbl_report_viewer (
  report_viewer_id  BIGSERIAL PRIMARY KEY,
  user_id           BIGINT NOT NULL,
  report_id         BIGINT NOT NULL,
  end_date          DATE,
  created_by        BIGINT,
  updated_by        BIGINT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_tbl_report_viewer_user
    FOREIGN KEY (user_id) REFERENCES tbl_user(user_id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT fk_tbl_report_viewer_report
    FOREIGN KEY (report_id) REFERENCES tbl_report(report_id)
    ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT uq_tbl_report_viewer UNIQUE (user_id, report_id)
);

-- ==========
-- System logs
-- ==========

CREATE TABLE IF NOT EXISTS tbl_system_log (
  system_log_id  BIGSERIAL PRIMARY KEY,
  event_type     TEXT NOT NULL,
  actor_type     TEXT,
  actor_id       TEXT,
  target_type    TEXT,
  target_id      TEXT,
  description    TEXT,
  metadata       JSONB,
  ip_address     TEXT,
  user_agent     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ==========
-- pgvector indexes (optional, safe to run even without data)
-- ==========
-- NOTE: ivfflat requires a training phase for best results; this is a starter index.
CREATE INDEX IF NOT EXISTS idx_tbl_document_metadata_vector
  ON tbl_document_metadata USING ivfflat (vector_data vector_cosine_ops) WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_tbl_template_layout_metadata_vector
  ON tbl_template_layout_metadata USING ivfflat (vector_data vector_cosine_ops) WITH (lists = 100);

