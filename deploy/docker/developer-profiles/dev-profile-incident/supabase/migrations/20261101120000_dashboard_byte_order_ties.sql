-- SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
-- SPDX-License-Identifier: Apache-2.0

-- The dashboard breaks ties between equal counts in byte order, whatever the database's collation.
--
-- get_incident_dashboard ranks types, labels and models by count and breaks ties alphabetically. That order
-- followed the database's default collation, so the same rows could rank ties differently on two databases:
-- en_US ignores spaces, hyphens and case at the first level, C does not ("car-door" and "carbon", "Unknown
-- model" and "nvidia/..."). It also decided which tied labels made a top-3 or top-8 cut. Every text
-- tie-break now says COLLATE "C" (byte order), the order the SQL harness, the evaluation recount
-- (eval/evalv2/references/dashboard_figures.py) and every test database use. Nothing else changes: the
-- same function body as 20261031090000, the same signature, grants and results apart from tie order.
-- Safe to run twice.

BEGIN;

CREATE OR REPLACE FUNCTION public.get_incident_dashboard(
    p_period_start TIMESTAMP WITHOUT TIME ZONE DEFAULT NULL::TIMESTAMP WITHOUT TIME ZONE,
    p_period_end TIMESTAMP WITHOUT TIME ZONE DEFAULT NULL::TIMESTAMP WITHOUT TIME ZONE,
    p_type TEXT DEFAULT NULL::TEXT,
    p_severity TEXT DEFAULT NULL::TEXT,
    p_day_of_week INTEGER DEFAULT NULL::INTEGER,
    p_hour INTEGER DEFAULT NULL::INTEGER,
    p_timezone TEXT DEFAULT 'UTC'::TEXT,
    p_scope TEXT DEFAULT 'official'::TEXT
) RETURNS JSONB
LANGUAGE sql STABLE SET search_path = ''
AS $function$
WITH target_tz AS (
    SELECT COALESCE(NULLIF(p_timezone, ''), 'UTC') AS tz
),
counted_all AS (
    -- One incident per video: its official revision, else its newest (the Library's rule, through
    -- searchable_revision_id). Its time is when that revision was made.
    SELECT
        r.video_id AS incident_id, r.id AS revision_id, r.kind, r.created_at AS generated_datetime,
        v.official_revision_id IS NOT NULL AS is_official,
        r.incident_type, r.severity_level AS severity,
        CASE WHEN jsonb_typeof(r.payload #> '{incident,confidence_score}') = 'number'
            THEN (r.payload #>> '{incident,confidence_score}')::DOUBLE PRECISION END AS confidence,
        r.payload,
        EXTRACT(DOW FROM (r.created_at AT TIME ZONE 'utc' AT TIME ZONE target_tz.tz))::INTEGER AS day_of_week,
        EXTRACT(HOUR FROM (r.created_at AT TIME ZONE 'utc' AT TIME ZONE target_tz.tz))::INTEGER AS hour_of_day,
        (r.created_at AT TIME ZONE 'utc' AT TIME ZONE target_tz.tz)::DATE AS local_day
    FROM public.videos AS v
    JOIN public.report_revisions AS r ON r.video_id = v.id AND r.id = public.searchable_revision_id(v.id)
    CROSS JOIN target_tz
),
dimension_filtered AS (
    SELECT * FROM counted_all
    WHERE (p_type IS NULL OR incident_type = public.canonical_incident_type(p_type)
          OR (public.canonical_incident_type(p_type) = 'unclassified' AND incident_type IS NULL))
      AND (p_severity IS NULL OR CASE p_severity
          WHEN 'low' THEN severity <= 2 WHEN 'medium' THEN severity = 3 WHEN 'high' THEN severity >= 4 ELSE FALSE END)
      AND (p_day_of_week IS NULL OR day_of_week = p_day_of_week)
      AND (p_hour IS NULL OR hour_of_day = p_hour)
      AND (COALESCE(p_scope, 'official') <> 'official' OR is_official)
      AND (COALESCE(p_scope, 'official') <> 'not_official' OR NOT is_official)
),
filtered AS (
    SELECT * FROM dimension_filtered
    WHERE (p_period_start IS NULL OR generated_datetime >= p_period_start)
      AND (p_period_end IS NULL OR generated_datetime < p_period_end)
),
video_counts AS (
    SELECT
        COUNT(*)::INTEGER AS uploaded,
        COUNT(*) FILTER (WHERE v.official_revision_id IS NOT NULL)::INTEGER AS with_official,
        COUNT(*) FILTER (WHERE v.official_revision_id IS NULL AND EXISTS (SELECT 1 FROM public.report_revisions r WHERE r.video_id = v.id))::INTEGER AS awaiting_selection,
        COUNT(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM public.report_revisions r WHERE r.video_id = v.id))::INTEGER AS without_report
    -- All time, independent of the period and the filters.
    FROM public.videos AS v
),
date_bounds AS (
    SELECT
        COALESCE((p_period_start AT TIME ZONE 'utc' AT TIME ZONE (SELECT tz FROM target_tz))::DATE, MIN(local_day), CURRENT_DATE) AS first_day,
        COALESCE(((p_period_end - INTERVAL '1 microsecond') AT TIME ZONE 'utc' AT TIME ZONE (SELECT tz FROM target_tz))::DATE, MAX(local_day), CURRENT_DATE) AS last_day
    FROM filtered
),
daily AS (
    SELECT day::DATE AS day, COUNT(f.incident_id)::INTEGER AS count,
        COUNT(f.incident_id) FILTER (WHERE f.is_official)::INTEGER AS official
    FROM date_bounds
    CROSS JOIN LATERAL generate_series(first_day, last_day, INTERVAL '1 day') AS day
    LEFT JOIN filtered AS f ON f.local_day = day::DATE
    GROUP BY day ORDER BY day
),
entity_rows AS (
    SELECT DISTINCT f.incident_id, f.incident_type, public.canonical_entity_type(e ->> 'type') AS label
    FROM filtered f CROSS JOIN LATERAL jsonb_array_elements(COALESCE(f.payload -> 'entities', '[]'::JSONB)) AS e
),
instrument_rows AS (
    SELECT DISTINCT f.incident_id, f.incident_type, public.normalized_name(ins ->> 'name') AS label,
        CASE WHEN jsonb_typeof(ins -> 'threat_level') = 'number'
            THEN (ins ->> 'threat_level')::NUMERIC::INTEGER END AS threat_level
    FROM filtered f CROSS JOIN LATERAL jsonb_array_elements(COALESCE(f.payload -> 'instruments', '[]'::JSONB)) AS ins
    WHERE public.normalized_name(ins ->> 'name') IS NOT NULL
),
asset_rows AS (
    SELECT DISTINCT f.incident_id, f.incident_type, f.is_official, public.normalized_name(a ->> 'name') AS label
    FROM filtered f CROSS JOIN LATERAL jsonb_array_elements(COALESCE(f.payload -> 'assets', '[]'::JSONB)) AS a
    WHERE public.normalized_name(a ->> 'name') IS NOT NULL
),
type_counts AS (
    SELECT incident_type, COUNT(*)::INTEGER AS count, COUNT(*) FILTER (WHERE is_official)::INTEGER AS official
    FROM filtered GROUP BY incident_type
),
type_details AS (
    SELECT jsonb_agg(jsonb_build_object(
        'type', COALESCE(tc.incident_type, 'Unclassified'),
        'count', tc.count,
        'official', tc.official,
        'notOfficial', tc.count - tc.official,
        'severity', jsonb_build_object(
            'low', (SELECT COUNT(*) FROM filtered f WHERE f.incident_type IS NOT DISTINCT FROM tc.incident_type AND f.severity <= 2),
            'medium', (SELECT COUNT(*) FROM filtered f WHERE f.incident_type IS NOT DISTINCT FROM tc.incident_type AND f.severity = 3),
            'high', (SELECT COUNT(*) FROM filtered f WHERE f.incident_type IS NOT DISTINCT FROM tc.incident_type AND f.severity >= 4)),
        'entities', COALESCE((SELECT jsonb_agg(jsonb_build_object('label', label, 'count', count) ORDER BY count DESC, label COLLATE "C") FROM (
            SELECT label, COUNT(DISTINCT incident_id)::INTEGER AS count FROM entity_rows WHERE incident_type IS NOT DISTINCT FROM tc.incident_type GROUP BY label ORDER BY count DESC, label COLLATE "C" LIMIT 3) ranked), '[]'::JSONB),
        'instruments', COALESCE((SELECT jsonb_agg(jsonb_build_object('label', label, 'count', count) ORDER BY count DESC, label COLLATE "C") FROM (
            SELECT label, COUNT(DISTINCT incident_id)::INTEGER AS count FROM instrument_rows WHERE incident_type IS NOT DISTINCT FROM tc.incident_type GROUP BY label ORDER BY count DESC, label COLLATE "C" LIMIT 3) ranked), '[]'::JSONB),
        'assets', COALESCE((SELECT jsonb_agg(jsonb_build_object('label', label, 'count', count) ORDER BY count DESC, label COLLATE "C") FROM (
            SELECT label, COUNT(DISTINCT incident_id)::INTEGER AS count FROM asset_rows WHERE incident_type IS NOT DISTINCT FROM tc.incident_type GROUP BY label ORDER BY count DESC, label COLLATE "C" LIMIT 3) ranked), '[]'::JSONB)
    ) ORDER BY tc.count DESC, tc.incident_type COLLATE "C") AS values
    FROM type_counts tc
),
ranked AS (
    SELECT 'entity' AS kind, label, COUNT(DISTINCT incident_id)::INTEGER AS count, NULL::INTEGER AS official FROM entity_rows GROUP BY label
    UNION ALL SELECT 'instrument', label, COUNT(DISTINCT incident_id)::INTEGER, NULL FROM instrument_rows GROUP BY label
    UNION ALL SELECT 'asset', label, COUNT(DISTINCT incident_id)::INTEGER,
        COUNT(DISTINCT incident_id) FILTER (WHERE is_official)::INTEGER FROM asset_rows GROUP BY label
),
threat_levels AS (
    SELECT threat_level AS level, COUNT(*)::INTEGER AS count FROM instrument_rows WHERE threat_level IS NOT NULL GROUP BY threat_level
),
attempts AS (
    -- Every recorded analysis attempt in the period, by outcome: failed attempts from model_runs,
    -- successful ones by their AI revision.
    SELECT mr.model_name, public.run_outcome(mr.notes) AS outcome
    FROM public.model_runs mr
    WHERE public.run_outcome(mr.notes) IN ('contract_failed', 'request_failed')
      AND (p_period_start IS NULL OR mr.run_datetime >= p_period_start)
      AND (p_period_end IS NULL OR mr.run_datetime < p_period_end)
    UNION ALL
    SELECT mr.model_name, public.run_outcome(mr.notes)
    FROM public.report_revisions r JOIN public.model_runs mr ON mr.id = r.model_run_id
    WHERE r.kind = 'ai'
      AND (p_period_start IS NULL OR r.created_at >= p_period_start)
      AND (p_period_end IS NULL OR r.created_at < p_period_end)
),
model_coverage AS (
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'model', model_name,
        'withReport', with_report, 'validFirstPass', valid_first_pass, 'repaired', repaired,
        'legacy', legacy, 'contractFailed', contract_failed, 'requestFailed', request_failed
    ) ORDER BY with_report DESC, model_name COLLATE "C"), '[]'::JSONB) AS values
    FROM (
        SELECT COALESCE(model_name, 'Unknown model') AS model_name,
            COUNT(*) FILTER (WHERE outcome NOT IN ('contract_failed', 'request_failed'))::INTEGER AS with_report,
            COUNT(*) FILTER (WHERE outcome = 'valid_first_pass')::INTEGER AS valid_first_pass,
            COUNT(*) FILTER (WHERE outcome = 'valid_after_structural_repair')::INTEGER AS repaired,
            COUNT(*) FILTER (WHERE outcome = 'legacy')::INTEGER AS legacy,
            COUNT(*) FILTER (WHERE outcome = 'contract_failed')::INTEGER AS contract_failed,
            COUNT(*) FILTER (WHERE outcome = 'request_failed')::INTEGER AS request_failed
        FROM attempts GROUP BY COALESCE(model_name, 'Unknown model')
    ) per_model
),
confidence_buckets AS (
    SELECT LEAST(9, GREATEST(0, FLOOR(confidence * 10)::INTEGER)) AS bucket, COUNT(*)::INTEGER AS count
    FROM filtered WHERE confidence IS NOT NULL GROUP BY 1
),
heatmap AS (
    SELECT day_of_week, hour_of_day, COUNT(*)::INTEGER AS count, COUNT(*) FILTER (WHERE is_official)::INTEGER AS official
    FROM filtered GROUP BY day_of_week, hour_of_day
),
previous AS (
    SELECT COUNT(*)::INTEGER AS total,
        COUNT(*) FILTER (WHERE kind = 'human')::INTEGER AS human,
        COUNT(*) FILTER (WHERE kind = 'ai')::INTEGER AS ai,
        COUNT(*) FILTER (WHERE severity >= 4)::INTEGER AS high
    FROM dimension_filtered
    WHERE p_period_start IS NOT NULL AND p_period_end IS NOT NULL
      AND generated_datetime >= p_period_start - (p_period_end - p_period_start)
      AND generated_datetime < p_period_start
)
SELECT jsonb_build_object(
    'total', (SELECT COUNT(*) FROM filtered),
    'official', (SELECT COUNT(*) FILTER (WHERE is_official) FROM filtered),
    'notOfficial', (SELECT COUNT(*) FILTER (WHERE NOT is_official) FROM filtered),
    'videos', (SELECT jsonb_build_object('uploaded', uploaded, 'withOfficial', with_official, 'awaitingSelection', awaiting_selection, 'withoutReport', without_report) FROM video_counts),
    'previous', CASE WHEN p_period_start IS NULL OR p_period_end IS NULL THEN NULL ELSE (SELECT to_jsonb(previous) FROM previous) END,
    'averageConfidence', (SELECT AVG(confidence) FROM filtered WHERE confidence IS NOT NULL),
    'confidenceNotProvided', (SELECT COUNT(*) FROM filtered WHERE confidence IS NULL),
    'severity', (SELECT jsonb_build_object('low', COUNT(*) FILTER (WHERE severity <= 2), 'medium', COUNT(*) FILTER (WHERE severity = 3), 'high', COUNT(*) FILTER (WHERE severity >= 4)) FROM filtered),
    'severityByScope', (SELECT jsonb_build_object(
        'official', jsonb_build_object('low', COUNT(*) FILTER (WHERE is_official AND severity <= 2), 'medium', COUNT(*) FILTER (WHERE is_official AND severity = 3), 'high', COUNT(*) FILTER (WHERE is_official AND severity >= 4)),
        'notOfficial', jsonb_build_object('low', COUNT(*) FILTER (WHERE NOT is_official AND severity <= 2), 'medium', COUNT(*) FILTER (WHERE NOT is_official AND severity = 3), 'high', COUNT(*) FILTER (WHERE NOT is_official AND severity >= 4))) FROM filtered),
    'revisions', (SELECT jsonb_build_object('ai', COUNT(*) FILTER (WHERE kind = 'ai'), 'human', COUNT(*) FILTER (WHERE kind = 'human')) FROM filtered),
    'types', COALESCE((SELECT values FROM type_details), '[]'::JSONB),
    'trend', COALESCE((SELECT jsonb_agg(jsonb_build_object('date', day, 'count', count, 'official', official, 'notOfficial', count - official) ORDER BY day) FROM daily), '[]'::JSONB),
    'heatmap', COALESCE((SELECT jsonb_agg(jsonb_build_object('day', day_of_week, 'hour', hour_of_day, 'count', count, 'official', official, 'notOfficial', count - official) ORDER BY day_of_week, hour_of_day) FROM heatmap), '[]'::JSONB),
    'confidence', COALESCE((SELECT jsonb_agg(jsonb_build_object('bucket', bucket, 'count', count) ORDER BY bucket) FROM confidence_buckets), '[]'::JSONB),
    'entityTypes', COALESCE((SELECT jsonb_agg(jsonb_build_object('label', label, 'count', count) ORDER BY count DESC, label COLLATE "C") FROM ranked WHERE kind = 'entity'), '[]'::JSONB),
    'topInstruments', COALESCE((SELECT jsonb_agg(jsonb_build_object('label', label, 'count', count) ORDER BY count DESC, label COLLATE "C") FROM (SELECT * FROM ranked WHERE kind = 'instrument' ORDER BY count DESC, label COLLATE "C" LIMIT 8) top), '[]'::JSONB),
    'topAssets', COALESCE((SELECT jsonb_agg(jsonb_build_object('label', label, 'count', count, 'official', official, 'notOfficial', count - official) ORDER BY count DESC, label COLLATE "C") FROM (SELECT * FROM ranked WHERE kind = 'asset' ORDER BY count DESC, label COLLATE "C" LIMIT 8) top), '[]'::JSONB),
    'threatLevels', COALESCE((SELECT jsonb_agg(jsonb_build_object('level', level, 'count', count) ORDER BY level) FROM threat_levels), '[]'::JSONB),
    'modelCoverage', (SELECT values FROM model_coverage)
);
$function$;

-- PostgREST caches function signatures; reload so the new definition is served at once.
NOTIFY pgrst, 'reload schema';

COMMIT;
