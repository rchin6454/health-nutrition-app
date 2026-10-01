-- 003_safety: food-safety rules and guidance passages (architecture §10.2, implementation plan
-- Phase 4). Filled by scripts/ingest/load_safety_rules.py and scripts/ingest/chunk_and_embed.py.
-- Additions to §10.2: `food_label` and `hot_max_duration_hours` on safety_rules (the stricter
-- limit above ~32 °C, so code can compare a stated storage time with it), the
-- `safety_food_aliases` table (food names → food_group), and `doc`/`dataset_version` on
-- doc_chunks for traceability.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS safety_rules (
    id                     TEXT PRIMARY KEY,
    food_group             TEXT NOT NULL,       -- cooked_rice | chicken | milk | power_cut ...
    food_label             TEXT NOT NULL,       -- "Cooked rice"
    state                  TEXT CHECK (state IN ('raw', 'cooked', 'thawed')),  -- NULL = any
    location               TEXT CHECK (location IN ('room_temp', 'fridge', 'freezer')),
    max_duration_hours     REAL CHECK (max_duration_hours > 0),
    hot_max_duration_hours REAL CHECK (hot_max_duration_hours > 0),
    safe_internal_temp_c   REAL,
    guidance               TEXT NOT NULL,
    region_note            TEXT,                -- Indian conditions: heat, power cuts
    origin                 TEXT NOT NULL,       -- authority; audit only, never in the prompt
    dataset_version        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS safety_rules_group_idx ON safety_rules (food_group);

CREATE TABLE IF NOT EXISTS safety_food_aliases (
    alias           TEXT PRIMARY KEY,           -- normalized: "chawal", "chicken curry"
    food_group      TEXT NOT NULL,
    implies_cooked  BOOLEAN NOT NULL,           -- "biryani", "chicken curry"
    is_generic      BOOLEAN NOT NULL,           -- only used when no specific group matches
    dataset_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id              BIGSERIAL PRIMARY KEY,
    category        TEXT NOT NULL CHECK (category IN ('nutrition', 'food_safety')),
    doc             TEXT NOT NULL,              -- source file under data/guidance/
    text            TEXT NOT NULL,
    origin          TEXT NOT NULL,              -- audit only, never in the prompt
    embedding       vector(384) NOT NULL,       -- BAAI/bge-small-en-v1.5
    dataset_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_idx
    ON doc_chunks USING hnsw (embedding vector_cosine_ops);
