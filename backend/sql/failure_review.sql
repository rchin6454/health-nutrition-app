-- Saved queries for the weekly failure review (architecture §8, implementation plan §5.2).
-- Paste each one into the Supabase SQL editor and save it under the name in its header.
-- Scope blocks (severity = 'scope_block') are the gates working as designed, not failures, so the
-- queries below keep them separate.


-- 1. Failures per day by type (last 30 days) ---------------------------------------------------
SELECT
    date_trunc('day', created_at AT TIME ZONE 'Asia/Kolkata')::date AS day_ist,
    severity,
    failure_type,
    count(*) AS failures
FROM failures
WHERE created_at > now() - interval '30 days'
GROUP BY 1, 2, 3
ORDER BY day_ist DESC, failures DESC;


-- 2. Top recurring failure types (last 7 days) --------------------------------------------------
-- Each row with failures >= 2 needs an issue, a root-cause fix and a new eval case.
SELECT
    failure_type,
    stage,
    severity,
    count(*) AS failures,
    count(DISTINCT conversation_id) AS conversations,
    min(created_at) AS first_seen,
    max(created_at) AS last_seen,
    array_agg(DISTINCT prompt_version) FILTER (WHERE prompt_version IS NOT NULL) AS prompt_versions,
    (array_agg(request_id ORDER BY created_at DESC))[1:3] AS latest_request_ids
FROM failures
WHERE created_at > now() - interval '7 days'
  AND severity <> 'scope_block'
GROUP BY failure_type, stage, severity
ORDER BY failures DESC;


-- 3. Warning and error rate per prompt version ---------------------------------------------------
-- Rates are per assistant message (one per chat turn) answered with that prompt version.
WITH turns AS (
    SELECT prompt_version, count(*) AS turns
    FROM messages
    WHERE role = 'assistant' AND prompt_version IS NOT NULL
    GROUP BY prompt_version
),
issues AS (
    SELECT
        prompt_version,
        count(*) FILTER (WHERE severity = 'warning') AS warnings,
        count(*) FILTER (WHERE severity = 'error') AS errors,
        count(*) FILTER (WHERE failure_type = 'unverified_number') AS unverified_number,
        count(*) FILTER (WHERE failure_type = 'unsupported_without_context')
            AS unsupported_without_context
    FROM failures
    WHERE prompt_version IS NOT NULL
    GROUP BY prompt_version
)
SELECT
    t.prompt_version,
    t.turns,
    coalesce(i.warnings, 0) AS warnings,
    round(100.0 * coalesce(i.warnings, 0) / t.turns, 1) AS warning_rate_pct,
    coalesce(i.errors, 0) AS errors,
    round(100.0 * coalesce(i.errors, 0) / t.turns, 1) AS error_rate_pct,
    coalesce(i.unverified_number, 0) AS unverified_number,
    coalesce(i.unsupported_without_context, 0) AS unsupported_without_context
FROM turns t
LEFT JOIN issues i USING (prompt_version)
ORDER BY t.prompt_version DESC;


-- 4. R4 check: claims with a non-null source (must always return 0) ----------------------------
SELECT count(*) AS claims_with_source
FROM messages, jsonb_array_elements(content -> 'claims') AS c
WHERE role = 'assistant' AND c -> 'source' <> 'null'::jsonb;
