# Distributed skill-eval coordinators

This directory stages eight CPU coordinators with four GitHub runners each.
Staging is intentionally inert:

- every runner service is disabled and stopped;
- runners receive `vss-skill-eval-standby`, not
  `vss-skill-eval-runner`;
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

Create a non-owner runtime role and grant only:

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
```

The `SECURITY DEFINER` functions have a fixed `pg_catalog` search path and
enforce owner, token, generation, expiry, and inventory checks. The runtime
role must not receive direct `INSERT`, `UPDATE`, or `DELETE` privileges on
lease state.

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
- `gh` permission to register repository runners.

Run a read-only connectivity preflight, then explicitly stage:

```bash
.github/skill-eval/ops/stage-distributed-runners.sh
.github/skill-eval/ops/stage-distributed-runners.sh --apply
```

The apply command fetches a fresh short-lived GitHub registration token for
each host and sends it through a mode-0600 temporary file. It registers
`vss-skill-validator-distributed-{1..8}-runner-{1..4}`, then verifies through
GitHub that all 32 are offline, standby-labeled, and lack the production
label.

Place the TLS-enforced runtime DSN in
`/home/ubuntu/eval-coordinator/.env` on each coordinator:

```bash
GPU_LEASE_DATABASE_URL='postgresql://skill_eval_lease:...@db.example/eval?sslmode=verify-full'
GPU_LEASE_MODE=postgres
```

Do not put the DSN in a runner label, command line, repository secret file,
or the staging script.

## Cutover (separate, explicit operation)

Never mix active flock-only coordinators with active PostgreSQL coordinators.

1. Verify all configured GPU workers and an empty/expired `gpu_leases` view.
2. Drain every runner using local-only `flock` and remove its
   `vss-skill-eval-runner` label before adding any distributed production
   label.
3. Start one staged runner service while it still has only the standby label;
   run a local lease smoke test.
4. Add `vss-skill-eval-runner` to that runner and observe one real leg,
   including several renewals and an owner-checked release.
5. Activate additional services gradually.

Activation is deliberately not automated here. It changes scheduling state
and must be a separately reviewed operator action.

### Required worker-fencing decision

The PostgreSQL generation fences lease database operations; it is not yet
enforced by a service on the GPU worker. Version 1 therefore assumes trusted
CPU coordinators and passive GPU workers. An abrupt coordinator death could
leave stale remote work after its row lease expires.

Production activation is blocked until the team either:

1. deploys GPU-side session fencing that accepts `(gpu_id, token, generation)`
   and rejects/terminates stale generations; or
2. formally accepts the trusted-controller risk and documents the stale-work
   detection and cleanup procedure.

The successful normal-path canary does not validate this failure mode.

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

