from __future__ import annotations

from pathlib import Path

from database.connection import connect
from database.fix_document_metadata_unique import fix_document_metadata_unique
from database.migrate_document_uuid import (
    fix_mismatched_report_documents_document_id,
    migrate_document_id_to_uuid,
)


def run_sql_files() -> None:
    """
    Runs all SQL files in database/sql in filename order.
    Skips files named migrate_*.sql (handled by Python migrators).
    """
    sql_dir = Path(__file__).parent / "sql"
    sql_files = sorted(
        p
        for p in sql_dir.glob("*.sql")
        if p.is_file() and not p.name.startswith("migrate_")
    )
    if not sql_files:
        raise RuntimeError(f"No .sql files found in {sql_dir}")

    for path in sql_files:
        sql = path.read_text(encoding="utf-8")
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
            print(f"applied: {path.name}")
        except Exception as e:
            # Existing environments can have partially migrated schemas
            # (e.g., bigint/uuid mismatch in pre-existing tables) that make
            # 002_create_tables.sql non-replayable. Skip known benign replay
            # errors for 002 and continue with repair migrators.
            err = str(e).lower()
            is_002 = path.name == "002_create_tables.sql"
            is_005 = path.name == "005_report_documents_pipeline_link.sql"
            known_replay_issue = (
                "datatype mismatch" in err
                or "incompatible types" in err
                or "duplicateobject" in err
                or "already exists" in err
                or "duplicate" in err
            )
            if is_002 and known_replay_issue:
                print(f"skipped replay of {path.name}: {e}")
                continue
            if is_005 and ("undefinedtable" in err or "does not exist" in err):
                print(
                    f"skipped {path.name} (legacy DB missing tbl_report_document): {e}"
                )
                continue
            raise


def setup_database() -> None:
    """Apply SQL migrations, align UUID schema, and enforce metadata uniqueness."""
    run_sql_files()
    with connect() as conn:
        fix_mismatched_report_documents_document_id(conn)
        migrate_document_id_to_uuid(conn)
        fix_document_metadata_unique(conn)


if __name__ == "__main__":
    setup_database()
    print("✓ Database setup complete (SQL + UUID alignment + metadata unique index).")

