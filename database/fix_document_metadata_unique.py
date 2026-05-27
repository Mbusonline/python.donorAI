"""
Repair tbl_document_metadata uniqueness for ON CONFLICT(document_id).

What this script does:
1) Detect duplicate document_id rows in tbl_document_metadata
2) Keep the newest row per document_id (largest document_metadata_id)
3) Delete older duplicates
4) Ensure unique index uq_tbl_document_metadata_document_id exists

Run:
  python -m database.fix_document_metadata_unique
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from database.connection import connect


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return cur.fetchone() is not None


def _duplicates_count(cur) -> int:
    cur.execute(
        """
        SELECT COUNT(*)::int
        FROM (
          SELECT document_id
          FROM tbl_document_metadata
          GROUP BY document_id
          HAVING COUNT(*) > 1
        ) x
        """
    )
    row = cur.fetchone()
    return int(row[0] if row else 0)


def fix_document_metadata_unique(conn) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            if not _table_exists(cur, "tbl_document_metadata"):
                print("tbl_document_metadata not found; skipping uniqueness repair.")
                return

            dup_groups = _duplicates_count(cur)
            print(f"duplicate document_id groups before cleanup: {dup_groups}")

            if dup_groups > 0:
                cur.execute(
                    """
                    DELETE FROM tbl_document_metadata t
                    USING tbl_document_metadata d
                    WHERE t.document_id = d.document_id
                      AND t.document_metadata_id < d.document_metadata_id
                    """
                )
                print(f"deleted duplicate rows: {cur.rowcount}")

            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tbl_document_metadata_document_id
                ON tbl_document_metadata(document_id)
                """
            )
            print("ensured unique index: uq_tbl_document_metadata_document_id")

            dup_groups_after = _duplicates_count(cur)
            print(f"duplicate document_id groups after cleanup: {dup_groups_after}")

            if dup_groups_after != 0:
                raise RuntimeError(
                    "Could not enforce unique document_id in tbl_document_metadata"
                )


def main() -> None:
    with connect() as conn:
        fix_document_metadata_unique(conn)
    print("done.")


if __name__ == "__main__":
    main()
