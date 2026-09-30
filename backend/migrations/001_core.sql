-- 001_core: conversation storage and the failures log (architecture §8.2, §8.3).

CREATE TABLE IF NOT EXISTS conversations (
    id               UUID PRIMARY KEY,               -- conversation_id sent by the frontend
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    title            TEXT                            -- first user message, truncated
);

CREATE TABLE IF NOT EXISTS messages (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    request_id       UUID NOT NULL,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content          JSONB NOT NULL,      -- {"text": ...} for user; the full ChatResponse for assistant
    analysis         JSONB,               -- QuestionAnalysis (Phase 2)
    context_snapshot JSONB,               -- ContextBundle used (Phase 3+)
    model            TEXT,
    prompt_version   TEXT,
    latency_ms       INTEGER,
    usage            JSONB                -- token counts from Groq
);
CREATE INDEX IF NOT EXISTS messages_conversation_created_idx
    ON messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS failures (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      UUID NOT NULL,
    conversation_id UUID,
    stage           TEXT NOT NULL,   -- input_gate | understanding | retrieval | generation | validation | output_gate | upstream_api | storage
    failure_type    TEXT NOT NULL,   -- schema_validation_failed | source_not_null | empty_answer | empty_claims |
                                     -- length_exceeded | rate_limited | api_error | timeout | ...
    severity        TEXT NOT NULL,   -- error | warning | scope_block
    model           TEXT,
    prompt_version  TEXT,
    user_message    TEXT,
    raw_output      TEXT,            -- exact model output, unmodified
    error_detail    TEXT,
    latency_ms      INTEGER
);
CREATE INDEX IF NOT EXISTS failures_created_at_idx ON failures (created_at DESC);
CREATE INDEX IF NOT EXISTS failures_failure_type_idx ON failures (failure_type);
