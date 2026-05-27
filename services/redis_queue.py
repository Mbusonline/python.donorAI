from typing import List, Optional

import redis

import settings


QUEUE_KEY = "document_metadata_queue"
LOCK_PREFIX = "document_metadata_lock:"
# Short-lived dedupe only; must NOT share LOCK_PREFIX or the worker cannot acquire the processing lock.
ENQUEUE_GUARD_PREFIX = "document_metadata_enqueue_guard:"
DONE_PREFIX = "document_metadata_done:"
ENQUEUE_GUARD_TTL_SECONDS = 10


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _doc_key(document_id: str) -> str:
    return str(document_id).strip()


def enqueue_document(document_id: str) -> dict:
    """
    Enqueue a document for metadata processing.

    Uses:
      - DONE key to avoid re-enqueue after success
      - Short ENQUEUE_GUARD key to dedupe rapid duplicate enqueues
    """
    try:
        r = _client()
    except Exception as e:
        print(f"[redis_queue] Failed to connect (check REDIS_URL / is Redis running?): {e}")
        raise
    document_id = _doc_key(document_id)
    done_key = f"{DONE_PREFIX}{document_id}"
    guard_key = f"{ENQUEUE_GUARD_PREFIX}{document_id}"

    if r.exists(done_key):
        return {"enqueued": False, "reason": "already_done"}

    # Brief guard to dedupe rapid double-enqueue (worker uses LOCK_PREFIX separately)
    if not r.set(guard_key, "1", nx=True, ex=ENQUEUE_GUARD_TTL_SECONDS):
        return {"enqueued": False, "reason": "already_queued_or_processing"}

    r.lpush(QUEUE_KEY, document_id)
    return {"enqueued": True}


def enqueue_documents_bulk(document_ids: List[str]) -> List[dict]:
    """
    Fast path: one Redis connection, one LPUSH for all accepted ids.
    Same semantics as enqueue_document per id (done key + short enqueue guard).
    Preserves FIFO for BRPOP: first id in the list is processed first.
    """
    if not document_ids:
        return []
    try:
        r = _client()
    except Exception as e:
        print(f"[redis_queue] Failed to connect (check REDIS_URL / is Redis running?): {e}")
        raise

    results: List[dict] = []
    to_push: List[str] = []

    for raw in document_ids:
        document_id = _doc_key(raw)
        done_key = f"{DONE_PREFIX}{document_id}"
        guard_key = f"{ENQUEUE_GUARD_PREFIX}{document_id}"

        if r.exists(done_key):
            results.append(
                {"document_id": document_id, "enqueued": False, "reason": "already_done"}
            )
            continue
        if not r.set(guard_key, "1", nx=True, ex=ENQUEUE_GUARD_TTL_SECONDS):
            results.append(
                {
                    "document_id": document_id,
                    "enqueued": False,
                    "reason": "already_queued_or_processing",
                }
            )
            continue
        to_push.append(document_id)
        results.append({"document_id": document_id, "enqueued": True})

    if to_push:
        # LPUSH key id1 id2 ... => list head ... id2 id1; BRPOP consumes id1 first (FIFO for batch order).
        r.lpush(QUEUE_KEY, *to_push)

    return results


def mark_done(document_id: str, ttl_seconds: int = 7 * 24 * 3600) -> None:
    document_id = _doc_key(document_id)
    r = _client()
    r.set(f"{DONE_PREFIX}{document_id}", "1", ex=ttl_seconds)
    r.delete(f"{LOCK_PREFIX}{document_id}")


def release_lock(document_id: str) -> None:
    document_id = _doc_key(document_id)
    _client().delete(f"{LOCK_PREFIX}{document_id}")


def acquire_processing_lock(document_id: str, ttl_seconds: int = 30 * 60) -> bool:
    """
    Acquire a longer lock while processing to prevent parallel workers
    from processing same document_id.
    """
    document_id = _doc_key(document_id)
    r = _client()
    return bool(r.set(f"{LOCK_PREFIX}{document_id}", "1", nx=True, ex=ttl_seconds))


def pop_document(block_seconds: int = 15) -> Optional[str]:
    """
    Blocking pop from the queue. Returns document_id (string / UUID) or None on timeout.
    """
    r = _client()
    item = r.brpop(QUEUE_KEY, timeout=block_seconds)
    if not item:
        return None
    _, value = item
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def queue_length() -> int:
    return int(_client().llen(QUEUE_KEY))

