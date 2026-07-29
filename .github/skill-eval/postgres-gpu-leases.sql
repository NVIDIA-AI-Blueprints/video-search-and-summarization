-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
-- SPDX-License-Identifier: Apache-2.0
--
-- Apply as the database owner. Runtime coordinators receive read-only worker
-- inventory/status plus EXECUTE on fenced functions, never direct lease-table
-- DML or access to another owner's capability token.

BEGIN;

CREATE TABLE IF NOT EXISTS public.gpu_workers (
    gpu_id text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    fence_ready boolean NOT NULL DEFAULT false,
    fence_version text,
    fence_attested_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

ALTER TABLE public.gpu_workers
    ADD COLUMN IF NOT EXISTS fence_ready boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS fence_version text,
    ADD COLUMN IF NOT EXISTS fence_attested_at timestamptz;

DO $worker_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint
        WHERE conname = 'gpu_workers_fence_attestation'
          AND conrelid = 'public.gpu_workers'::regclass
    ) THEN
        ALTER TABLE public.gpu_workers
            ADD CONSTRAINT gpu_workers_fence_attestation CHECK (
                NOT fence_ready
                OR (
                    fence_version IS NOT NULL
                    AND fence_version <> ''
                    AND fence_attested_at IS NOT NULL
                )
            );
    END IF;
END
$worker_constraints$;

CREATE TABLE IF NOT EXISTS public.gpu_leases (
    gpu_id text PRIMARY KEY
        REFERENCES public.gpu_workers (gpu_id) ON DELETE RESTRICT,
    owner_id text,
    lease_token uuid,
    generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    acquired_at timestamptz,
    renewed_at timestamptz,
    lease_expires_at timestamptz,
    CHECK (
        (owner_id IS NULL AND lease_token IS NULL)
        OR (
            owner_id IS NOT NULL
            AND lease_token IS NOT NULL
            AND acquired_at IS NOT NULL
            AND renewed_at IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND acquired_at <= renewed_at
            AND renewed_at <= lease_expires_at
        )
    )
);

-- The digest is not an encryption secret; it is an authoritative, atomic
-- guard against two operators installing different backup passphrases.
CREATE TABLE IF NOT EXISTS public.backup_key_registry (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    registered_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE UNIQUE INDEX IF NOT EXISTS gpu_leases_token_unique
    ON public.gpu_leases (lease_token)
    WHERE lease_token IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS gpu_leases_owner_unique
    ON public.gpu_leases (owner_id)
    WHERE owner_id IS NOT NULL;

DROP VIEW IF EXISTS public.gpu_lease_status;
CREATE VIEW public.gpu_lease_status
WITH (security_barrier = true)
AS
SELECT
    l.gpu_id,
    w.enabled,
    w.fence_ready,
    w.fence_version,
    w.fence_attested_at,
    l.owner_id,
    l.generation,
    l.acquired_at,
    l.renewed_at,
    l.lease_expires_at
FROM public.gpu_leases AS l
JOIN public.gpu_workers AS w USING (gpu_id);

REVOKE ALL ON public.gpu_lease_status FROM PUBLIC;

INSERT INTO public.gpu_leases (gpu_id)
SELECT gpu_id
FROM public.gpu_workers
ON CONFLICT (gpu_id) DO NOTHING;

CREATE OR REPLACE FUNCTION public.create_gpu_lease_row()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    INSERT INTO public.gpu_leases (gpu_id)
    VALUES (NEW.gpu_id)
    ON CONFLICT (gpu_id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS gpu_workers_create_lease ON public.gpu_workers;
CREATE TRIGGER gpu_workers_create_lease
AFTER INSERT ON public.gpu_workers
FOR EACH ROW
EXECUTE FUNCTION public.create_gpu_lease_row();

CREATE OR REPLACE FUNCTION public.acquire_gpu_lease(
    eligible_gpu_ids text[],
    requested_owner_id text,
    requested_token uuid,
    ttl_seconds integer
)
RETURNS TABLE (
    gpu_id text,
    lease_token uuid,
    generation bigint,
    lease_expires_at timestamptz
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    WITH candidate AS (
        SELECT l.gpu_id
        FROM public.gpu_leases AS l
        JOIN public.gpu_workers AS w USING (gpu_id)
        WHERE l.gpu_id = ANY(eligible_gpu_ids)
          AND w.enabled
          AND w.fence_ready
          AND requested_owner_id <> ''
          AND requested_token IS NOT NULL
          AND ttl_seconds BETWEEN 60 AND 300
          AND (
              l.owner_id IS NULL
              OR l.lease_expires_at <= statement_timestamp()
          )
        ORDER BY array_position(eligible_gpu_ids, l.gpu_id)
        FOR UPDATE OF l SKIP LOCKED
        LIMIT 1
    )
    UPDATE public.gpu_leases AS l
    SET owner_id = requested_owner_id,
        lease_token = requested_token,
        generation = l.generation + 1,
        acquired_at = statement_timestamp(),
        renewed_at = statement_timestamp(),
        lease_expires_at = statement_timestamp() + make_interval(secs => ttl_seconds)
    FROM candidate
    WHERE l.gpu_id = candidate.gpu_id
    RETURNING l.gpu_id, l.lease_token, l.generation, l.lease_expires_at
$$;

CREATE OR REPLACE FUNCTION public.renew_gpu_lease(
    requested_gpu_id text,
    requested_owner_id text,
    requested_token uuid,
    requested_generation bigint,
    ttl_seconds integer
)
RETURNS TABLE (lease_expires_at timestamptz)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    UPDATE public.gpu_leases AS l
    SET renewed_at = statement_timestamp(),
        lease_expires_at = statement_timestamp() + make_interval(secs => ttl_seconds)
    WHERE l.gpu_id = requested_gpu_id
      AND l.owner_id = requested_owner_id
      AND l.lease_token = requested_token
      AND l.generation = requested_generation
      AND l.lease_expires_at > statement_timestamp()
      AND ttl_seconds BETWEEN 60 AND 300
      AND EXISTS (
          SELECT 1
          FROM public.gpu_workers AS w
          WHERE w.gpu_id = l.gpu_id
            AND w.enabled
            AND w.fence_ready
      )
    RETURNING l.lease_expires_at
$$;

CREATE OR REPLACE FUNCTION public.release_gpu_lease(
    requested_gpu_id text,
    requested_owner_id text,
    requested_token uuid,
    requested_generation bigint
)
RETURNS TABLE (gpu_id text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    UPDATE public.gpu_leases AS l
    SET owner_id = NULL,
        lease_token = NULL,
        renewed_at = statement_timestamp(),
        lease_expires_at = statement_timestamp()
    WHERE l.gpu_id = requested_gpu_id
      AND l.owner_id = requested_owner_id
      AND l.lease_token = requested_token
      AND l.generation = requested_generation
    RETURNING l.gpu_id
$$;

CREATE OR REPLACE FUNCTION public.validate_gpu_lease(
    requested_gpu_id text,
    requested_token uuid,
    requested_generation bigint
)
RETURNS TABLE (
    valid boolean,
    remaining_seconds double precision
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT
        COALESCE(
            bool_or(
                w.enabled
                AND w.fence_ready
                AND l.lease_token = requested_token
                AND l.generation = requested_generation
                AND l.lease_expires_at > statement_timestamp()
            ),
            false
        ) AS valid,
        COALESCE(
            max(
                CASE
                    WHEN w.enabled
                     AND w.fence_ready
                     AND l.lease_token = requested_token
                     AND l.generation = requested_generation
                     AND l.lease_expires_at > statement_timestamp()
                    THEN extract(
                        epoch FROM l.lease_expires_at - statement_timestamp()
                    )
                    ELSE 0
                END
            ),
            0
        )::double precision AS remaining_seconds
    FROM public.gpu_workers AS w
    JOIN public.gpu_leases AS l USING (gpu_id)
    WHERE w.gpu_id = requested_gpu_id
$$;

REVOKE ALL ON FUNCTION
    public.acquire_gpu_lease(text[], text, uuid, integer)
FROM PUBLIC;
REVOKE ALL ON FUNCTION
    public.renew_gpu_lease(text, text, uuid, bigint, integer)
FROM PUBLIC;
REVOKE ALL ON FUNCTION
    public.release_gpu_lease(text, text, uuid, bigint)
FROM PUBLIC;
REVOKE ALL ON FUNCTION
    public.validate_gpu_lease(text, uuid, bigint)
FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

-- Upgrade the documented runtime role from the earlier direct-DML grants when
-- it already exists. Custom role names must receive the equivalent grants
-- from the runbook after this owner transaction commits.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'skill_eval_lease'
    ) THEN
        EXECUTE
            'REVOKE ALL ON TABLE public.gpu_workers, public.gpu_leases, '
            'public.gpu_lease_status FROM skill_eval_lease';
        EXECUTE 'REVOKE CREATE ON SCHEMA public FROM skill_eval_lease';
        EXECUTE 'GRANT USAGE ON SCHEMA public TO skill_eval_lease';
        EXECUTE
            'GRANT SELECT ON TABLE public.gpu_workers, '
            'public.gpu_lease_status TO skill_eval_lease';
        EXECUTE
            'GRANT EXECUTE ON FUNCTION '
            'public.acquire_gpu_lease(text[], text, uuid, integer), '
            'public.renew_gpu_lease(text, text, uuid, bigint, integer), '
            'public.release_gpu_lease(text, text, uuid, bigint) '
            'TO skill_eval_lease';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'skill_eval_fence'
    ) THEN
        EXECUTE
            'REVOKE ALL ON TABLE public.gpu_workers, public.gpu_leases, '
            'public.gpu_lease_status FROM skill_eval_fence';
        EXECUTE 'REVOKE CREATE ON SCHEMA public FROM skill_eval_fence';
        EXECUTE 'GRANT USAGE ON SCHEMA public TO skill_eval_fence';
        EXECUTE
            'GRANT EXECUTE ON FUNCTION '
            'public.validate_gpu_lease(text, uuid, bigint) '
            'TO skill_eval_fence';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'skill_eval_backup'
    ) THEN
        EXECUTE
            'REVOKE ALL ON ALL TABLES IN SCHEMA public '
            'FROM skill_eval_backup';
        EXECUTE
            'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public '
            'FROM skill_eval_backup';
        EXECUTE
            'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public '
            'FROM skill_eval_backup';
        EXECUTE 'REVOKE CREATE ON SCHEMA public FROM skill_eval_backup';
        EXECUTE 'GRANT USAGE ON SCHEMA public TO skill_eval_backup';
        EXECUTE
            'GRANT SELECT ON ALL TABLES IN SCHEMA public '
            'TO skill_eval_backup';
        EXECUTE
            'GRANT SELECT ON ALL SEQUENCES IN SCHEMA public '
            'TO skill_eval_backup';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES '
            'REVOKE ALL ON TABLES FROM skill_eval_backup';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES '
            'REVOKE ALL ON SEQUENCES FROM skill_eval_backup';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES '
            'REVOKE ALL ON FUNCTIONS FROM skill_eval_backup';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES '
            'REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'REVOKE ALL ON TABLES FROM skill_eval_backup';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'REVOKE ALL ON SEQUENCES FROM skill_eval_backup';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'REVOKE ALL ON FUNCTIONS FROM skill_eval_backup';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'GRANT SELECT ON TABLES TO skill_eval_backup';
        EXECUTE
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            'GRANT SELECT ON SEQUENCES TO skill_eval_backup';
    END IF;
END;
$$;

-- Inventory defaults disabled and unfenced. Only the reviewed activation
-- helper should set fence_ready after validating the worker daemon.
-- INSERT INTO gpu_workers (
--     gpu_id, enabled, fence_ready, fence_version, fence_attested_at
-- ) VALUES ('vss-eval-l40s', true, true, '1', statement_timestamp())
-- ON CONFLICT (gpu_id) DO UPDATE
-- SET enabled = EXCLUDED.enabled,
--     fence_ready = EXCLUDED.fence_ready,
--     fence_version = EXCLUDED.fence_version,
--     fence_attested_at = EXCLUDED.fence_attested_at,
--     updated_at = statement_timestamp();

COMMIT;
