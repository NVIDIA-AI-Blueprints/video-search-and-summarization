-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
-- SPDX-License-Identifier: Apache-2.0
--
-- Apply as the database owner. Runtime coordinators need SELECT on both
-- tables and UPDATE on gpu_leases, never schema-owner privileges.

CREATE TABLE IF NOT EXISTS gpu_workers (
    gpu_id text PRIMARY KEY,
    enabled boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE IF NOT EXISTS gpu_leases (
    gpu_id text PRIMARY KEY REFERENCES gpu_workers (gpu_id) ON DELETE RESTRICT,
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

CREATE UNIQUE INDEX IF NOT EXISTS gpu_leases_token_unique
    ON gpu_leases (lease_token)
    WHERE lease_token IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS gpu_leases_owner_unique
    ON gpu_leases (owner_id)
    WHERE owner_id IS NOT NULL;

INSERT INTO gpu_leases (gpu_id)
SELECT gpu_id
FROM gpu_workers
ON CONFLICT (gpu_id) DO NOTHING;

CREATE OR REPLACE FUNCTION create_gpu_lease_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO gpu_leases (gpu_id)
    VALUES (NEW.gpu_id)
    ON CONFLICT (gpu_id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS gpu_workers_create_lease ON gpu_workers;
CREATE TRIGGER gpu_workers_create_lease
AFTER INSERT ON gpu_workers
FOR EACH ROW
EXECUTE FUNCTION create_gpu_lease_row();

-- Inventory is explicit and defaults disabled. Example activation:
-- INSERT INTO gpu_workers (gpu_id, enabled) VALUES ('vss-eval-l40s', true)
-- ON CONFLICT (gpu_id) DO UPDATE
-- SET enabled = EXCLUDED.enabled, updated_at = statement_timestamp();

