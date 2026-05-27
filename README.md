# Donor report generator (API)

FastAPI service for **Postgres-driven donor reports**, **document metadata** (captions, embeddings), and **template metadata**. Optional **Redis** backs a **separate document-metadata queue** processed by a small worker process.

---

## Contents

1. [What runs where](#what-runs-where)
2. [Prerequisites](#prerequisites)
3. [Environment variables](#environment-variables)
4. [Install and run (local)](#install-and-run-local)
5. [Deploy the API (production)](#deploy-the-api-production)
6. [Document metadata: three ways to run it](#document-metadata-three-ways-to-run-it)
7. [Deploy the document queue worker separately](#deploy-the-document-queue-worker-separately)
8. [How the Redis queue works](#how-the-redis-queue-works)
9. [Troubleshooting](#troubleshooting)

---

## What runs where

| Process | Command | Purpose |
|--------|---------|---------|
| **API** | `python main.py` or `uvicorn main:app --host 0.0.0.0 --port 8002` | HTTP: reports, sync metadata, enqueue to Redis, template metadata |
| **Worker** (optional) | `python workers/document_metadata_worker.py` | Pulls `document_id`s from Redis; runs `process_document_metadata` **one at a time** |
| **Redis** (optional) | e.g. `redis-server` or managed Redis | List queue + locks + “done” dedupe keys |

Report generation (`POST /api/reports/generate-from-db`) runs **inside the API** only. The worker is **only** for queued document metadata.

---

## Prerequisites

- **Python** 3.10+ (3.12 is common)
- **PostgreSQL** with **pgvector** (`CREATE EXTENSION vector;` — see `database/sql/001_enable_pgvector.sql`)
- **AWS S3** (typical): bucket + IAM for paths in `tbl_document` / pipelines
- **OpenAI / Gemini** keys (see [Environment variables](#environment-variables))
- **Redis** — only if you use `POST /api/documents/enqueue-metadata` with `inline_process: false` **and** the separate worker

---

## Environment variables

Create a **`.env`** in the project root (same directory as `main.py`). On your server this is typically **`/var/www/donor_report/python/.env`**. Both the API and the worker load it via `python-dotenv` (`settings.py`, `database/connection.py`).

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | `postgresql://user:pass@host:5432/dbname` |
| `OPENAI_API_KEY` | For OpenAI paths | Used when DB has no `tbl_model.private_key` for OpenAI, and for some processors |
| `GEMINI_API_KEY` | For Gemini paths | Used when DB has no key for Google/Gemini; **required** for image section mapping and several metadata flows |
| `AWS_BUCKET` or `S3_BUCKET` | Typical | Default bucket for non-`s3://` paths |
| `AWS_REGION` / `AWS_DEFAULT_REGION` | Typical | S3 region |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | If not using IAM role | S3 credentials |
| `REDIS_URL` | Queue mode | Default `redis://localhost:6379/0` — **API and worker must use the same URL** |
| `S3_REPORTS_PREFIX` | No | Prefix for generated PDF keys (default `generated-reports/`) |
| `SKIP_STARTUP_DB_CHECK` | No | `1` / `true` / `yes` skips Postgres ping at startup (debug only) |

**Assembly API keys (report generation):** `tbl_model.private_key` is used **first** when non-empty; if empty, the code falls back to `OPENAI_API_KEY` or `GEMINI_API_KEY` depending on `tbl_model.provider` (`services/report_pipeline_service.py`).

---

## Install and run (local)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Apply DB schema (new environments):

```powershell
python -m database.setup
```

Run API:

```powershell
python main.py
```

- Docs: `http://localhost:8002/docs`
- Health: `GET /api/health`

---

## Deploy the API (production)

### 1. Layout

- Install app under **`/var/www/donor_report/python`** (project root: `main.py`, `.venv`, `.env` live here)
- Create venv, `pip install -r requirements.txt`
- Place **`.env`** with production secrets (file mode `600`, not committed to git)
- Bind Uvicorn to **localhost** and put **Nginx** (or another reverse proxy) in front for TLS

### 2. Example systemd unit — API

`/etc/systemd/system/donor-report-api.service`:

```ini
[Unit]
Description=Donor Report API
After=network.target

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/var/www/donor_report/python
EnvironmentFile=/var/www/donor_report/python/.env
ExecStart=/var/www/donor_report/python/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8002 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now donor-report-api
```

Use **long proxy timeouts** for `POST /api/reports/generate-from-db` (often several minutes). Example Nginx:

```nginx
proxy_read_timeout 600s;
proxy_send_timeout 600s;
```

### 3. Firewall

- Expose **443** (HTTPS) to the world
- **Do not** expose Postgres or Redis to the public internet; Redis should be reachable only from API + worker hosts

### 4. Kaleido / Chrome (required for CSV charts in reports)

Report generation uses **Plotly + Kaleido** to export CSV charts as PNG. On Linux servers you must install **Chrome for Testing** (not snap Chromium):

The `deploy` user often has `HOME=/var/www/donor_report`, which is **not writable**, so a plain `plotly_get_chrome` fails with `Permission denied: .../.local`. Install into the project `tmp/` folder instead:

```bash
sudo mkdir -p /var/www/donor_report/python/tmp/chrome-browser
sudo chown -R deploy:deploy /var/www/donor_report/python/tmp

sudo -u deploy bash -c 'cd /var/www/donor_report/python && source .venv/bin/activate && plotly_get_chrome -y --path /var/www/donor_report/python/tmp/chrome-browser'
```

Confirm the binary exists:

```bash
ls /var/www/donor_report/python/tmp/chrome-browser/chrome-linux64/chrome
```

Add to `.env` (required — avoids snap `/snap/bin/chromium`):

```env
BROWSER_PATH=/var/www/donor_report/python/tmp/chrome-browser/chrome-linux64/chrome
```

**Verify Chrome runs as `deploy`** (must exit 0; if this fails, charts will fail too):

```bash
sudo -u deploy env \
  LD_LIBRARY_PATH=/var/www/donor_report/python/tmp/chrome-browser/chrome-linux64 \
  /var/www/donor_report/python/tmp/chrome-browser/chrome-linux64/chrome \
  --headless --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --dump-dom about:blank 2>&1 | head -5
```

If you see `error while loading shared libraries` or `not found` from `ldd`:

```bash
ldd /var/www/donor_report/python/tmp/chrome-browser/chrome-linux64/chrome | grep "not found"
```

**Ubuntu 24.04 (Noble)** — use `t64` package names:

```bash
sudo apt-get install -y \
  libatk1.0-0t64 libatk-bridge2.0-0t64 libnss3 libnspr4 libcups2t64 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libasound2t64 libpango-1.0-0 libcairo2 libx11-6 libxcb1 libxext6
```

Older Ubuntu (22.04): replace `libcups2t64` → `libcups2`, `libasound2t64` → `libasound2`, `libatk1.0-0t64` → `libatk1.0-0`.

Deploy the latest `csv_processor.py` (sets `LD_LIBRARY_PATH` to the `chrome-linux64` folder during export).

**Do not rely on** Ubuntu snap Chromium (`/snap/bin/chromium`) for the API.

Then restart the API: `sudo systemctl restart donor-report-api`.

Ensure the **API service user** can write Kaleido temp files (charts use `tmp/kaleido` under the project, or `KALEIDO_TMP_DIR`):

```bash
sudo mkdir -p /var/www/donor_report/python/tmp/kaleido
sudo chown -R deploy:deploy /var/www/donor_report/python/tmp
# use www-data instead of deploy if that is your systemd User=
```

If logs show `Permission denied: '/var/www/donor_report/.choreographer-...'`, Kaleido is using **snap Chromium**, which writes temp files under **`HOME`** (often the parent deploy folder), not `TMPDIR`. After deploying the latest `csv_processor.py`, the app sets `HOME` to `tmp/kaleido/home` during chart export. You still need `tmp/` writable by the service user; you do **not** need to chmod the parent `/var/www/donor_report` unless you run an older build.

Optional in `.env`:

```env
KALEIDO_TMP_DIR=/var/www/donor_report/python/tmp/kaleido
```

Confirm systemd has `WorkingDirectory=/var/www/donor_report/python` (not the parent folder).

Without Chrome, `POST /api/reports/generate-from-db` fails when the pipeline includes CSV/Excel documents (HTTP **503** with `chart_dependency_missing`).

---

## Document metadata: three ways to run it

All paths expect `tbl_document.document_id` (UUID) and valid `file_path` / S3 data for the worker or processors.

| Mode | Endpoint / flag | Redis | Worker | When to use |
|------|-------------------|-------|--------|-------------|
| **A. Synchronous** | `POST /api/documents/process-metadata` | No | No | One-off; caller waits for completion |
| **B. Queued (recommended for bulk)** | `POST /api/documents/enqueue-metadata` with `inline_process: false` (default) | **Yes** | **Yes — separate process** | Many documents; offload CPU/API from the HTTP server |
| **C. Inline in API** | `POST /api/documents/enqueue-metadata` with `inline_process: true` | No* | No | No Redis infra; work runs in FastAPI **BackgroundTasks** after the response |

\*Inline mode still calls `mark_done` on success, which touches Redis if configured; for pure no-Redis operation, use **A** or ensure Redis is optional for your deployment path.

**Bulk enqueue example (queue mode):**

```bash
curl -sS -X POST "https://your-host/api/documents/enqueue-metadata" \
  -H "Content-Type: application/json" \
  -d '{"document_ids":["uuid-1","uuid-2"],"inline_process":false}'
```

Response includes `queue_length` and per-id `enqueued` / `reason`. After that, **run the worker** (same machine or another host with same `REDIS_URL` and app code + `.env`).

---

## Deploy the document queue worker separately

The worker is a **standalone long-running process**. It does **not** need to listen on HTTP; it only needs Redis, Postgres, S3, LLM keys, and the same Python project on disk.

### 1. Same machine as the API

1. Ensure **Redis** is running (`REDIS_URL` in `.env` matches).
2. From **`/var/www/donor_report/python`**, with the **same** `.venv` and **same** `.env`:

   **Linux:**

   ```bash
   cd /var/www/donor_report/python
   source .venv/bin/activate
   python workers/document_metadata_worker.py
   ```

   **Windows (local dev):**

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python workers\document_metadata_worker.py
   ```

3. Keep it running under **systemd** (or Supervisor, PM2, etc.).

### 2. Example systemd unit — worker only

`/etc/systemd/system/donor-report-metadata-worker.service`:

```ini
[Unit]
Description=Donor Report Document Metadata Worker (Redis queue)
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/var/www/donor_report/python
EnvironmentFile=/var/www/donor_report/python/.env
ExecStart=/var/www/donor_report/python/.venv/bin/python workers/document_metadata_worker.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Adjust `After=` / `Wants=` if Redis is remote (e.g. ElastiCache — drop `redis.service`).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now donor-report-metadata-worker
```

### 3. Separate machine (scale-out)

- Install the **same repo**, venv, and **`.env`** (at minimum: `DATABASE_URL`, `REDIS_URL`, `GEMINI_API_KEY` / `OPENAI_API_KEY`, AWS vars — whatever `process_document_metadata` needs).
- Point `REDIS_URL` at the **same** Redis instance the API uses.
- Run **one or more** worker processes:
  - Each worker loops with `BRPOP` + a **processing lock** per `document_id` (`services/redis_queue.py`: `acquire_processing_lock`) so two workers do not process the same id in parallel.
  - Throughput = more workers × rate limits of Gemini/OpenAI; still **one metadata job per worker iteration**.

### 4. Logs

Worker writes under `logs/document_<document_id>/` relative to `WorkingDirectory`. Ensure the service user can write there and rotate logs if the host fills up.

---

## How the Redis queue works

Implemented in `services/redis_queue.py`:

| Redis key | Role |
|-----------|------|
| `document_metadata_queue` | List: API **LPUSH**es `document_id`; worker **BRPOP**s (FIFO for bulk order) |
| `document_metadata_lock:<id>` | Prevents duplicate processing across workers (NX + TTL) |
| `document_metadata_enqueue_guard:<id>` | Short TTL dedupe on rapid double-enqueue |
| `document_metadata_done:<id>` | After success: set with TTL (~7 days); **blocks re-enqueue** as `already_done` |

Worker flow (`workers/document_metadata_worker.py`):

1. `BRPOP` queue (15s timeout; reconnects on Redis errors).
2. `acquire_processing_lock(document_id)` — skip if another worker holds it.
3. `process_document_metadata(...)` → DB + S3 + LLM.
4. On success: `mark_done(document_id)` (sets done key, clears lock).
5. On failure: `release_lock(document_id)` so the document **can be re-enqueued** manually.

---

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| Enqueue returns 500 / connection error | Redis up? `REDIS_URL` correct from API host? |
| Queue length grows, nothing processes | Worker service running? Same `REDIS_URL`? Logs under `logs/` |
| `already_done` immediately | `document_metadata_done:<id>` still present; wait for TTL or flush key in dev only |
| Worker exits on Redis blip | Worker is built to sleep and retry; check systemd `Restart=` |
| Metadata needs Gemini | `GEMINI_API_KEY` in `.env` on **worker** host |
| Kaleido / Chrome error on report | Run `plotly_get_chrome` in venv (see [§4 Kaleido / Chrome](#4-kaleido--chrome-required-for-csv-charts-in-reports)) |
| `[report_pipeline] skip image` for every image | Metadata not ready: worker must fill **caption** and **description** in `tbl_document_metadata` |

---

## API quick reference

| Method | Path |
|--------|------|
| `GET` | `/`, `/docs`, `/api/health` |
| `POST` | `/api/reports/generate-from-db` |
| `POST` | `/api/documents/process-metadata` |
| `POST` | `/api/documents/enqueue-metadata` |
| `POST` | `/api/templates/process-metadata` |

---

## Security

- Do not commit `.env`; rotate leaked keys.
- Restrict Redis to private networks; use TLS for managed Redis when available.
- Terminate HTTPS at the proxy; tighten CORS in `main.py` for production if needed.
