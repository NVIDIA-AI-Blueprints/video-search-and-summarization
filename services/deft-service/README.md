# DEFT service (reference implementation)

## Overview

DEFT (**D**ata-driven **E**xperiment **F**ine-**T**uning) is a microservice
that launches and tracks iterative fine-tuning pipelines for NVIDIA TAO
models — object detection (Grounding DINO, via the `tmm_only` workflow) and
AOI visual inspection (VisualChangeNet, via the `aoi_active_learning`
workflow). A client submits a fine-tune run (either a named **profile**
with optional overrides, or an inline **workflow** + **config**); DEFT walks
that workflow's pipeline — mine, train, evaluate, and, for AOI, RCA →
routing → synthetic-defect generation → mining → train → evaluate — across
one or more iterations until a KPI target or iteration budget is met, and
exposes progress, structured stage events, logs, and the resulting
checkpoint (with its decision threshold) through the API.

This repository implements that API contract **and** (since v0.2) the real
execution engine behind it. Two modes, selected by `DEFT_EXECUTION_MODE`:

* **simulate** (default) — runs are walked in-process with short sleeps and
  synthetic metrics. Exercises the full API contract anywhere (CI, laptops,
  no GPU); this is the original reference behavior.
* **real** — `aoi_active_learning` runs execute the actual DEFT loop on the
  local **docker** backend: TAO VisualChangeNet training + inference
  containers, KPI threshold sweep (achieved FAR@recall / threshold / AUC),
  RCA → routing → SigLIP k-NN mining → leakage-gated CSV assembly →
  fine-tune → evaluate, with KPI-gated best-checkpoint selection and real
  on-disk artifacts. See `PRODUCTION_READINESS.md` for the workspace
  contract, runbook, and the deliberate scope lines (backends, tmm_only,
  AnomalyGen).

Run state persists in SQLite (`DEFT_DB_PATH`) so restarts don't lose runs;
`app/store.py` remains the seam to swap for Postgres/Redis in multi-replica
deployments.

## The API contract

[`openapi.json`](./openapi.json) is the source of truth — the spec produced
and reviewed before this implementation started (auth, ownership,
idempotency, mining-config validation, cursor-paginated stage events, SSE
streaming, and artifact pointers were all agreed there first). `app/main.py`
serves that exact file at `/openapi.json` (and therefore `/docs`), so the
running service and the reviewed spec can never drift apart — if you change
behavior, change `openapi.json` first.

## Compute backend

`app/config.py` reads the service's backend selection from the environment:

| Env var | Default | Notes |
| --- | --- | --- |
| `DEFT_BACKEND_TYPE` | `docker` | One of `docker`, `kfp`, `slurm`, `brev` — must match `BackendInfo.type` in `openapi.json`. Invalid values fail fast at startup. |
| `DEFT_STORAGE_ROOT` | `file:///data/deft` | Returned as `InfoResponse.storage_root`; also the prefix used for the simulator's synthetic artifact URIs. |
| `DEFT_MAX_NUM_GPUS` | `64` | Ceiling enforced on `training.num_gpus` in `app/resolve.py`. |

`DEFT_BACKEND_TYPE` only changes what this service *reports* — `app/runner.py`
still simulates every run in-process regardless of the value, so no backend
credentials are read or required by this repository today. A real
orchestration layer swapped into `app/runner.py` (see Overview above) would
need to authenticate against whichever backend `DEFT_BACKEND_TYPE` names,
following the corresponding platform skill in the
[TAO Skill Bank](https://github.com/NVIDIA-TAO/tao-skills-bank):

| Backend | Skill | Typical credentials |
| --- | --- | --- |
| `docker` | `tao-run-on-local-docker` | `NGC_KEY` (nvcr.io pulls), `HF_TOKEN` (gated HF weights) |
| `kfp` | `tao-run-on-kubernetes` | Authenticated kubeconfig / cluster context |
| `slurm` | `tao-run-on-slurm` | `SLURM_USER`, `SLURM_HOSTNAME`, SSH key auth to the login node |
| `brev` | `tao-run-on-brev` | `brev login` session (Brev CLI) |

## AOI real mode: specs & backbone (zero setup)

The AOI real-mode pool converter (`app/execution/build_pool.py`) needs two deployment
assets — the ChangeNet spec templates and the pretrained C-RADIOv2 backbone. These are
**not per-run data**; DEFT provides them itself so `profile: aoi_default` runs with no
hand-placed files:

- **Specs** ship in `app/data/specs/` and are staged into each run's `<workspace>/specs/`.
- **Backbone** is fetched once from public HF, cached, and staged as
  `<workspace>/augmentation/backbone/model.safetensors`.

Precedence (both): already-present/materialized → `DEFT_POOL_TEMPLATE_DIR` (dev override) →
bundled/HF default.

| Env var | Default | Notes |
| --- | --- | --- |
| `DEFT_POOL_TEMPLATE_DIR` | _(unset)_ | Dev override: a dir with `specs/*.yaml` + `backbone/*.safetensors`. Wins over the bundled/HF defaults. |
| `DEFT_BACKBONE_URL` | public HF `nvidia/C-RADIOv2-B` | Backbone source. **Empty string disables** the fetch (use cache/template only); point at an internal mirror for air-gapped/throttled hosts. |
| `DEFT_BACKBONE_CACHE` | `~/.cache/deft/c_radio_v2_b.safetensors` | Persistent cache path — the fetch is one-time per host. |
| `DEFT_BACKBONE_RETRIES` | `3` | Retries (with backoff) for transient fetch failures (HF rate-limits anonymous downloads per IP → `429`). |
| `HF_TOKEN` | _(unset)_ | Raises the HF anonymous rate limit. |

A failed fetch falls back gracefully (no crash) with an actionable message.

## Project layout

```
openapi.json              the agreed API contract (served verbatim at /openapi.json)
app/
  main.py                 FastAPI app, exception handlers, serves openapi.json
  auth.py                 bearer-token identity + deft:admin scope (placeholder — see Auth below)
  config.py               service constants (version, backend, storage root, GPU ceiling)
  errors.py               ErrorResponse-shaped HTTPException factories
  resolve.py              profile+overrides / workflow+config -> resolved ExperimentConfig
  store.py                SQLite-backed run store (write-through; restart-safe)
  runner.py               execute_run dispatcher + the contract simulator
  execution/              real DEFT engine: docker backend, AOI pipeline stages,
                          KPI sweep, workspace/CSV rules, real-mode runner
  schemas/                Pydantic models mirroring components.schemas in openapi.json
  data/                   static workflow + profile catalog (tmm_only, aoi_active_learning, ...)
  routers/                one module per tag: service, workflows, profiles, fine_tune
tests/test_smoke.py       end-to-end tests against the real request/response flow
```

## Install, start, and stop

```bash
cd deft-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start (foreground, auto-reload on code changes)
uvicorn app.main:app --reload --port 8000
```

Swagger UI / interactive docs: http://localhost:8000/docs

To stop: `Ctrl-C` in the foreground terminal.

If you started it in the background instead:

```bash
uvicorn app.main:app --port 8000 &
UVICORN_PID=$!
...
kill $UVICORN_PID       # stop
```

Run state persists to SQLite at `DEFT_DB_PATH` (default
`./deft_service.db`). A restart keeps every run; anything that was
mid-flight is marked `failed` with a "service restarted" error and can be
resumed. Delete the db file for a clean slate.

## Auth

Every endpoint except `/v1/health` requires `Authorization: Bearer <token>`.
Two modes via `DEFT_AUTH_MODE`:

* `trusted` (default, local demos only — a startup warning is logged): the
  token *is* the identity.

  ```
  Authorization: Bearer alice          -> identity "alice"
  Authorization: Bearer admin:alice    -> identity "alice", deft:admin scope
  ```

* `static`: the token must match a configured API key —
  `DEFT_API_KEYS="s3cr3t-a:alice,s3cr3t-r:admin:root"`. Baseline for an
  internal single-tenant deployment; swap `parse_principal` in
  `app/auth.py` for JWT/OIDC verification beyond that.

`RunStatus.created_by` is set from this identity at creation time. Viewing
or managing a run created by a different identity (`GET`/`DELETE`/`cancel`/
`resume`/etc. on `/v1/fine-tune/{id}*`, or listing another identity's runs
via `?owner=`) requires the `deft:admin` scope and otherwise returns `403`.

## Usage examples

```bash
BASE=http://localhost:8000
AUTH='Authorization: Bearer alice'

# List available workflows and profiles
curl -s $BASE/v1/workflows -H "$AUTH" | python3 -m json.tool
curl -s $BASE/v1/profiles -H "$AUTH" | python3 -m json.tool

# Kick off a run from a built-in profile
RUN_ID=$(curl -s -X POST $BASE/v1/fine-tune \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"profile": "aoi_default"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
echo "run: $RUN_ID"

# ...or an inline workflow + config, with an idempotency key so a retried
# request can't launch a duplicate run:
curl -s -X POST $BASE/v1/fine-tune \
  -H "$AUTH" -H "Content-Type: application/json" -H "Idempotency-Key: my-key-1" \
  -d '{
        "workflow": "tmm_only",
        "config": {
          "model": {"name": "grounding_dino", "checkpoint": "s3://deft-models/models/gdino_swin_tiny.pth"},
          "data_pool": {"path": "file:///data/deft/raw_frames/santomas/"},
          "training": {"num_gpus": 4}
        }
      }'

# Poll status / full details (details include the resolved config actually used)
curl -s $BASE/v1/fine-tune/$RUN_ID/status -H "$AUTH"
curl -s $BASE/v1/fine-tune/$RUN_ID -H "$AUTH"

# Tail stage events live via Server-Sent Events (Ctrl-C to stop)
curl -sN $BASE/v1/fine-tune/$RUN_ID/events/stream -H "$AUTH"

# ...or poll the same events with cursor-based pagination (no offset drift on a growing log)
curl -s "$BASE/v1/fine-tune/$RUN_ID/events?after_seq=0" -H "$AUTH"

# Raw container stdout/stderr
curl -s $BASE/v1/fine-tune/$RUN_ID/logs -H "$AUTH"

# Once state=="completed": results carry the KPI decision threshold —
# always use it, never a spec default (see ResultsResponse.threshold in openapi.json)
curl -s $BASE/v1/fine-tune/$RUN_ID/results -H "$AUTH"

# Storage pointers for this run's artifacts (state snapshot, event log, HTML
# report, and once completed, the best-checkpoint inference handoff)
curl -s $BASE/v1/fine-tune/$RUN_ID/artifacts -H "$AUTH"

# Cancel a running job, then resume it from its last completed stage
curl -s -X POST $BASE/v1/fine-tune/$RUN_ID/cancel -H "$AUTH"
curl -s -X POST $BASE/v1/fine-tune/$RUN_ID/resume -H "$AUTH"

# List your own runs (add ?owner=<identity> with the deft:admin scope to see others')
curl -s "$BASE/v1/fine-tune?state=completed&limit=20" -H "$AUTH"

# Bulk delete requires an explicit confirm=true — no filter means "everything you own"
curl -s -X DELETE "$BASE/v1/fine-tune?confirm=true&state=failed" -H "$AUTH"
```

## Tests

```bash
pytest
```

14 end-to-end tests exercise the real request/response flow (not just unit
functions): full run lifecycle from profile to completion, ownership 403s,
idempotency-key replay and conflict, mining-config and `num_gpus`
validation, the bulk-delete confirm guard, and cancel → resume →
completion. All pass, and the same behavior was additionally verified by
hand against a live `uvicorn` process (not just the test client) —
`/events/stream` was tailed over real HTTP end-to-end, `/results` was
confirmed to return the threshold/far_pct/backbone/images_dir contract
only once a run reaches `completed`, and `/openapi.json` was confirmed to
serve the exact reviewed spec (17 paths).
