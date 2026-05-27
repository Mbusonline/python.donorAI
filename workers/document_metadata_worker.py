import os
import sys
import time

import redis

# Allow running as a script: python workers/document_metadata_worker.py
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from services.document_metadata_processor import process_document_metadata
from services.redis_queue import (
    acquire_processing_lock,
    mark_done,
    pop_document,
    queue_length,
    release_lock,
)


def main() -> None:
    print("Document metadata worker started.")
    print("Waiting for document_ids...")

    os.makedirs("logs", exist_ok=True)

    while True:
        try:
            document_id = pop_document(block_seconds=15)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            # Redis can reset idle/blocking sockets on Windows or service restarts.
            # Keep worker alive and retry instead of crashing.
            print(f"[worker] Redis connection dropped while waiting for queue: {e}")
            time.sleep(2.0)
            continue
        except Exception as e:
            print(f"[worker] Unexpected queue read error: {e}")
            time.sleep(2.0)
            continue
        if document_id is None:
            continue

        if not acquire_processing_lock(document_id):
            # Another worker is processing; skip.
            continue

        try:
            remaining = queue_length()
        except Exception:
            remaining = -1
        print(
            f"→ metadata (1 at a time) document_id={document_id} "
            f"approx_queue_remaining={remaining}"
        )

        log_dir = os.path.join("logs", f"document_{document_id}")
        os.makedirs(log_dir, exist_ok=True)

        try:
            process_document_metadata(document_id=document_id, log_dir=log_dir)
            mark_done(document_id)
            print(f"✓ Processed document_id={document_id}")
        except Exception as e:
            # Release lock so it can be retried by re-enqueue
            release_lock(document_id)
            print(f"✗ Failed document_id={document_id}: {e}")
            # small backoff to avoid hot-loop on repeated failing docs
            time.sleep(1.0)


if __name__ == "__main__":
    main()

