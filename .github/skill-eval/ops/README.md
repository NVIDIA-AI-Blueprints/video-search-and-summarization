# Distributed skill-eval coordinators

This directory stages eight CPU coordinators with four GitHub runners each.
Staging is intentionally inert:

- every runner service is disabled and stopped;
- runners receive `vss-skill-eval-standby`, not
  `vss-skill-eval-postgres`;
- runner environments select PostgreSQL mode, but no database secret is
  copied by these scripts.

Thus a service-start mistake still cannot match the current workflows.

## PostgreSQL prerequisite

Use a PostgreSQL deployment with synchronous durability and **RPO 0 for
acknowledged commits**. Apply the schema as its owner:

```bash
psql "$GPU_LEASE_ADMIN_DATABASE_URL" \
  -v ON_ERROR_STOP=1 \
  -f .github/skill-eval/postgres-gpu-leases.sql
```

Create two non-owner login roles before applying the schema: one coordinator
role and one validation-only GPU role. Use independently managed passwords.
After applying the schema, grant only:

```sql
GRANT CONNECT ON DATABASE eval TO skill_eval_lease;
REVOKE ALL ON public.gpu_workers, public.gpu_leases, public.gpu_lease_status
FROM skill_eval_lease;
REVOKE CREATE ON SCHEMA public FROM skill_eval_lease;
GRANT USAGE ON SCHEMA public TO skill_eval_lease;
GRANT SELECT ON public.gpu_workers, public.gpu_lease_status TO skill_eval_lease;
GRANT EXECUTE
ON FUNCTION public.acquire_gpu_lease(text[], text, uuid, integer),
            public.renew_gpu_lease(text, text, uuid, bigint, integer),
            public.release_gpu_lease(text, text, uuid, bigint)
TO skill_eval_lease;

GRANT CONNECT ON DATABASE eval TO skill_eval_fence;
REVOKE ALL ON public.gpu_workers, public.gpu_leases, public.gpu_lease_status
FROM skill_eval_fence;
REVOKE CREATE ON SCHEMA public FROM skill_eval_fence;
GRANT USAGE ON SCHEMA public TO skill_eval_fence;
GRANT EXECUTE
ON FUNCTION public.validate_gpu_lease(text, uuid, bigint)
TO skill_eval_fence;
```

The `SECURITY DEFINER` functions have a fixed `pg_catalog` search path. The
coordinator role must not receive direct lease-table DML. The GPU role must
not receive any table/view read or lease acquire/renew/release capability.

Add the operator-managed GPU inventory explicitly. New workers default to
disabled:

```sql
INSERT INTO gpu_workers (gpu_id, enabled)
VALUES
  ('vss-eval-l40s', false),
  ('vss-eval-rtx-1g-2', false)
ON CONFLICT (gpu_id) DO NOTHING;
```

Enable only workers whose Brev identity and hardware were verified. The
schema trigger creates the corresponding lease row.

## Stage 32 runners without accepting jobs

Prerequisites on the operator workstation:

- authenticated `brev` access to all eight named machines;
- `gh` permission to register repository runners;
- a mode-0600 coordinator environment file containing the shared Harbor,
  model, NGC, and Brev pool settings (but no PostgreSQL DSN).

Run a read-only connectivity preflight, then explicitly stage:

```bash
.github/skill-eval/ops/stage-distributed-runners.sh
COORDINATOR_ENV_FILE=/secure/path/coordinator.env \
  .github/skill-eval/ops/stage-distributed-runners.sh --apply
```

The apply command fetches a fresh short-lived GitHub registration token for
each host and sends it through a mode-0600 temporary file. It also installs
the protected coordinator environment as
`/home/ubuntu/eval-coordinator/.env`, plus the authenticated local Brev client
and `${BREV_CONFIG_DIR:-$HOME/.brev}` runtime used to discover and connect to
GPU workers. Temporary credential payloads are mode 0600 and removed after
installation. It registers
`vss-skill-validator-distributed-{1..8}-runner-{1..4}`, then verifies through
GitHub that all 32 are offline, standby-labeled, and lack the production label.

Store the coordinator DSN in the repository Actions secret
`GPU_LEASE_DATABASE_URL`:

```bash
postgresql://skill_eval_lease:...@db.example/eval?sslmode=verify-full
```

The workflow restores that secret after loading each host `.env`; distributed
runners reject a box-local DSN and any mode other than PostgreSQL. Do not put
either DSN in a runner label, command line, source file, or runner `.env`.

## Deploy worker-side fencing

First drain legacy coordinators and confirm `gpu_lease_status` has no live
lease. Service startup deliberately removes all containers and stale trial
processes on each dedicated worker.

Write the validation-only DSN to a mode-0600 local file, review the exact
enabled-worker inventory, then preflight and deploy:

```bash
chmod 600 /secure/path/gpu-fence-dsn
export GPU_FENCE_DATABASE_URL_FILE=/secure/path/gpu-fence-dsn
export GPU_WORKERS='vss-eval-l40s vss-eval-rtx-1g-2'
.github/skill-eval/ops/deploy-gpu-fence-workers.sh
.github/skill-eval/ops/deploy-gpu-fence-workers.sh \
  --apply --confirm-drained
```

For registered external nodes, also set `REGISTERED_GPU_WORKERS` to the names
that use direct SSH aliases. The installer requires `sslmode=verify-full`,
stores the validation DSN root-only, probes the database function, restarts
the service, and verifies the expected worker ID and service version.

## Cutover (separate, explicit operation)

Never mix active flock-only coordinators with active PostgreSQL coordinators.

1. Verify all configured GPU workers and an empty/expired `gpu_leases` view.
2. Drain every runner using local-only `flock` and remove its
   `vss-skill-eval-runner` label.
3. Deploy and verify the GPU fence on every enabled worker.
4. Start one staged runner with only `vss-skill-eval-canary`; run one normal
   Harbor leg and observe renewals plus owner-checked release.
5. Force-kill a canary coordinator after it launches a long worker process.
   After lease expiry, require the next generation to kill the stale process
   and containers before its first command.
6. Remove the canary label, add `vss-skill-eval-postgres`, and activate
   additional runner services gradually. The production workflows never
   select the legacy label.

Activation is deliberately not automated here. It changes scheduling state
and must be a separately reviewed operator action.

### Worker-fencing boundary

`vss-gpu-fence` validates the exact `(gpu_id, token, generation)` through the
validation-only role. Every Harbor mutation and trial command uses its local
Unix-socket session. A newer generation, invalid lease, database outage past
the local safety deadline, daemon restart, or shutdown terminates registered
process groups and all dedicated-worker containers. Admission stays blocked
unless cleanup postconditions pass.

This is an operational safety boundary for trusted coordinators and dedicated
workers, not a hostile multi-tenant sandbox. Administrators with unrestricted
root/SSH access can bypass it and must not mutate an enabled worker during an
evaluation.

## Runtime behavior

`run_leg.py` uses a 90-second PostgreSQL row lease renewed every 20 seconds.
Acquisition is atomic (`FOR UPDATE ... SKIP LOCKED`), increments a database
fencing generation, and preserves candidate preference. The existing
host-local `flock` remains defense in depth.

If a renewal cannot be confirmed, the heartbeat marks the lease lost.
`run_leg.py` checks health at most every five seconds, terminates the Harbor
process group, and fails the leg closed. Release requires the exact worker,
owner, token, and generation; an unreachable database is left to TTL expiry.
GitHub cancellation (`SIGTERM`) is converted into normal exception unwinding,
so the same Harbor termination and owner-checked lease cleanup run.

The worker revalidates every five seconds and keeps a deadline 30 seconds
inside PostgreSQL expiry. A separate local watchdog enforces that deadline
even if a database read stalls. The persisted generation high-water mark
rejects older claims after service restart.
