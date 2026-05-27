"""
Migrate tbl_document.document_id (and related FK columns) from BIGINT to UUID.

Safe to re-run: no-ops if tbl_document.document_id is already UUID.

Run after base SQL (001–005):
  python -m database.migrate_document_uuid

Or it runs automatically at the end of:
  python -m database.setup
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from database.connection import connect


def _col_type(cur, table: str, column: str) -> Optional[str]:
    cur.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return cur.fetchone() is not None


def fix_mismatched_report_documents_document_id(conn) -> None:
    """
    If tbl_document.document_id is UUID (new schema) but tbl_report_document.document_id
    is still BIGINT from an older 005, drop the integer column and add a nullable UUID FK.

    Any existing BIGINT values are removed (they cannot map to UUID PKs without a migration table).
    """
    with conn.cursor() as cur:
        if not _table_exists(cur, "tbl_document") or not _table_exists(cur, "tbl_report_document"):
            return
        pt = _col_type(cur, "tbl_document", "document_id")
        if pt != "uuid":
            return
        rt = _col_type(cur, "tbl_report_document", "document_id")
        if rt not in ("bigint", "integer", "smallint"):
            return

    print(
        "fix_mismatched_report_documents: tbl_report_document.document_id was integer "
        "while tbl_document.document_id is UUID — replacing column (old link values dropped)."
    )

    with conn.transaction():
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE tbl_report_document DROP CONSTRAINT IF EXISTS fk_tbl_report_document_document"
        )
        cur.execute("DROP INDEX IF EXISTS idx_tbl_report_document_document_id")
        cur.execute("ALTER TABLE tbl_report_document DROP COLUMN document_id")
        cur.execute("ALTER TABLE tbl_report_document ADD COLUMN document_id UUID")
        cur.execute(
            """
            ALTER TABLE tbl_report_document
            ADD CONSTRAINT fk_tbl_report_document_document
            FOREIGN KEY (document_id) REFERENCES tbl_document(document_id)
            ON UPDATE CASCADE ON DELETE SET NULL
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tbl_report_document_document_id
            ON tbl_report_document(document_id)
            """
        )

    print("fix_mismatched_report_documents: done.")


def migrate_document_id_to_uuid(conn) -> None:
    with conn.cursor() as cur:
        if not _table_exists(cur, "tbl_document"):
            print("migrate_document_uuid: tbl_document missing — skip")
            return

        doc_type = _col_type(cur, "tbl_document", "document_id")
        if doc_type == "uuid":
            print("migrate_document_uuid: already UUID — skip")
            return
        if doc_type not in ("bigint", "integer", "smallint"):
            raise RuntimeError(
                f"migrate_document_uuid: unexpected tbl_document.document_id type: {doc_type!r}"
            )

    print("migrate_document_uuid: BIGINT → UUID (single transaction)…")

    with conn.transaction():
        cur = conn.cursor()

        cur.execute(
            "ALTER TABLE tbl_document_metadata DROP CONSTRAINT IF EXISTS fk_tbl_document_metadata_document"
        )
        if _table_exists(cur, "tbl_report_document"):
            cur.execute(
                "ALTER TABLE tbl_report_document DROP CONSTRAINT IF EXISTS fk_tbl_report_document_document"
            )
        cur.execute("DROP INDEX IF EXISTS uq_tbl_document_metadata_document_id")

        cur.execute(
            """
            ALTER TABLE tbl_document
            ADD COLUMN IF NOT EXISTS document_uuid UUID
            """
        )
        cur.execute(
            """
            UPDATE tbl_document SET document_uuid = gen_random_uuid()
            WHERE document_uuid IS NULL
            """
        )

        cur.execute(
            """
            ALTER TABLE tbl_document_metadata
            ADD COLUMN IF NOT EXISTS document_uuid UUID
            """
        )
        cur.execute(
            """
            UPDATE tbl_document_metadata dm
            SET document_uuid = d.document_uuid
            FROM tbl_document d
            WHERE dm.document_id = d.document_id
            """
        )
        cur.execute(
            "DELETE FROM tbl_document_metadata WHERE document_uuid IS NULL"
        )
        cur.execute("ALTER TABLE tbl_document_metadata DROP COLUMN document_id")
        cur.execute(
            "ALTER TABLE tbl_document_metadata RENAME COLUMN document_uuid TO document_id"
        )
        cur.execute(
            "ALTER TABLE tbl_document_metadata ALTER COLUMN document_id SET NOT NULL"
        )

        rd_type = (
            _col_type(cur, "tbl_report_document", "document_id")
            if _table_exists(cur, "tbl_report_document")
            else None
        )
        if rd_type in ("bigint", "integer", "smallint"):
            cur.execute(
                """
                ALTER TABLE tbl_report_document
                ADD COLUMN IF NOT EXISTS document_uuid UUID
                """
            )
            cur.execute(
                """
                UPDATE tbl_report_document rd
                SET document_uuid = d.document_uuid
                FROM tbl_document d
                WHERE rd.document_id IS NOT NULL AND rd.document_id = d.document_id
                """
            )
            cur.execute(
                "ALTER TABLE tbl_report_document DROP COLUMN document_id"
            )
            cur.execute(
                "ALTER TABLE tbl_report_document RENAME COLUMN document_uuid TO document_id"
            )
        elif rd_type is None and _table_exists(cur, "tbl_report_document"):
            cur.execute(
                """
                ALTER TABLE tbl_report_document
                ADD COLUMN IF NOT EXISTS document_id UUID
                """
            )

        cur.execute("ALTER TABLE tbl_document DROP CONSTRAINT IF EXISTS tbl_document_pkey")
        cur.execute("ALTER TABLE tbl_document DROP COLUMN document_id CASCADE")
        cur.execute(
            "ALTER TABLE tbl_document RENAME COLUMN document_uuid TO document_id"
        )
        cur.execute("ALTER TABLE tbl_document ADD PRIMARY KEY (document_id)")

        cur.execute(
            """
            ALTER TABLE tbl_document_metadata
            ADD CONSTRAINT fk_tbl_document_metadata_document
            FOREIGN KEY (document_id) REFERENCES tbl_document(document_id)
            ON UPDATE CASCADE ON DELETE CASCADE
            """
        )
        if _table_exists(cur, "tbl_report_document"):
            cur.execute(
                """
                ALTER TABLE tbl_report_document
                ADD CONSTRAINT fk_tbl_report_document_document
                FOREIGN KEY (document_id) REFERENCES tbl_document(document_id)
                ON UPDATE CASCADE ON DELETE SET NULL
                """
            )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_tbl_document_metadata_document_id
            ON tbl_document_metadata(document_id)
            """
        )

    print("migrate_document_uuid: done.")


def main() -> None:
    with connect() as conn:
        fix_mismatched_report_documents_document_id(conn)
        migrate_document_id_to_uuid(conn)


if __name__ == "__main__":
    main()
