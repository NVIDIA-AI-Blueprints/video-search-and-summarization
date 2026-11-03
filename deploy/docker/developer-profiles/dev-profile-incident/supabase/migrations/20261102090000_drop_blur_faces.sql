-- SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
-- SPDX-License-Identifier: Apache-2.0

-- Remove the privacy blur (20261101090000_video_blur_faces.sql): it added processing time without enough benefit.
--
-- Drops videos.blur_faces, analysis_jobs.blur_faces and analysis_jobs.reanalyse_video_id (with its foreign key), and
-- claim_queued_analysis_job(p_stale_seconds, p_uploads_only), which only existed to keep re-analysis jobs away from
-- consoles that could not run them. claim_analysis_job(p_stale_seconds) goes back to the claim of
-- 20261031120000_analysis_jobs.sql: stale recovery first, then the oldest queued job (FOR UPDATE SKIP LOCKED).
--
-- A queued or running re-analysis job would become an upload job of an existing video once its column is gone (a
-- second video row on the same R2 object), so those are cancelled first. Finished re-analysis jobs stay as history.
-- Nothing in R2 is touched, and revisions keep the evidence images they already have.
--
-- Destructive for the three columns only. Safe to run again (IF EXISTS; the cancel runs only while the column exists).

BEGIN;

-- Before the columns go: a re-analysis job that has not finished must not turn into an upload job.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'analysis_jobs' AND column_name = 'reanalyse_video_id') THEN
        EXECUTE $q$
            UPDATE public.analysis_jobs
            SET status = 'cancelled', finished_at = pg_catalog.now() AT TIME ZONE 'utc', heartbeat_at = NULL
            WHERE reanalyse_video_id IS NOT NULL AND status IN ('queued', 'running')
        $q$;
    END IF;
END;
$$;

-- claim_analysis_job stops calling claim_queued_analysis_job before that function is dropped.
CREATE OR REPLACE FUNCTION public.claim_analysis_job(p_stale_seconds INTEGER DEFAULT 180)
RETURNS SETOF public.analysis_jobs
LANGUAGE plpgsql SECURITY INVOKER SET search_path = ''
AS $$
DECLARE
    v_now TIMESTAMP WITHOUT TIME ZONE := pg_catalog.now() AT TIME ZONE 'utc';
BEGIN
    IF p_stale_seconds IS NULL OR p_stale_seconds < 1 THEN
        RAISE EXCEPTION 'p_stale_seconds must be at least 1' USING ERRCODE = '22023';
    END IF;

    -- A running job nobody has heartbeated for p_stale_seconds lost its runner (the console stopped).
    -- Its first stale run sends it back to the queue; a second fails it, so nothing hangs forever.
    UPDATE public.analysis_jobs AS j
    SET status = CASE WHEN j.attempts >= 2 THEN 'failed' ELSE 'queued' END,
        error = CASE WHEN j.attempts >= 2
                     THEN 'The console stopped twice while analysing this video'
                     ELSE NULL END,
        finished_at = CASE WHEN j.attempts >= 2 THEN v_now ELSE NULL END,
        heartbeat_at = NULL
    WHERE j.status = 'running'
      AND COALESCE(j.heartbeat_at, j.started_at, j.created_at) < v_now - pg_catalog.make_interval(secs => p_stale_seconds);

    RETURN QUERY
    UPDATE public.analysis_jobs AS j
    SET status = 'running', attempts = j.attempts + 1, started_at = v_now, heartbeat_at = v_now,
        finished_at = NULL, error = NULL
    WHERE j.id = (
        SELECT q.id FROM public.analysis_jobs AS q
        WHERE q.status = 'queued'
        ORDER BY q.created_at, q.id
        LIMIT 1
        FOR UPDATE SKIP LOCKED)
    RETURNING j.*;
END;
$$;

DROP FUNCTION IF EXISTS public.claim_queued_analysis_job(INTEGER, BOOLEAN);

ALTER TABLE public.analysis_jobs
    DROP COLUMN IF EXISTS reanalyse_video_id,
    DROP COLUMN IF EXISTS blur_faces;

ALTER TABLE public.videos
    DROP COLUMN IF EXISTS blur_faces;

-- Security: CREATE OR REPLACE keeps the grants of claim_analysis_job; stated again so this file stands alone.
REVOKE ALL ON FUNCTION public.claim_analysis_job(INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_analysis_job(INTEGER) TO service_role;

-- PostgREST caches the schema; reload so the dropped columns and function are gone at once.
NOTIFY pgrst, 'reload schema';

COMMIT;
