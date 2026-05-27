-- Enable pgvector extension (required for VECTOR type + similarity search)
CREATE EXTENSION IF NOT EXISTS vector;

-- UUID defaults use gen_random_uuid() — built in on PostgreSQL 13+.
-- On PG 12, run: CREATE EXTENSION IF NOT EXISTS pgcrypto;

