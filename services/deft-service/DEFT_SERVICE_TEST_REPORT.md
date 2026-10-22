# DEFT Service — Test Report (pre-handoff to Ratnesh's team)

Tested by driving the FastAPI reference implementation both via its test suite and as a
live `uvicorn` service exercised over HTTP. Scope: verify the **API contract** works as
intended (the runs are simulated in-process by design — no real TAO training here).

## Verdict
**Works as intended.** The service faithfully implements the `openapi.json` contract; all
happy-path and contract-guarantee behaviors verified. **No functional bugs found.** The
remaining items are the (already-acknowledged) reference-impl → production seams that must
be filled before the August Helm/k8s blueprint ships. One thing I initially flagged
(events pagination) turned out to be a **false alarm** on my side — it works correctly.

## What was verified (evidence)
- **Test suite:** `pytest tests/` → **14/14 PASS** (health/auth, workflows+profiles seeded,
  profile-or-config required, unknown-profile 422, mining-config mutual-exclusion 422,
  num_gpus-ceiling 422, full run lifecycle, results-before-completion 409, ownership scoping,
  idempotency replay+conflict, bulk-delete confirm, cancel→resume, events+logs stream).
- **Live service (uvicorn):**
  - `/v1/health` public `{"status":"ok"}`; `/v1/info` → 401 without auth, `{version, backend, storage_root}` with auth.
  - `/v1/workflows` → `tmm_only` + `aoi_active_learning` (with pre_loop/loop/post_loop stages); `/v1/profiles` → `santomas_gdino`→tmm_only, `aoi_default`→aoi_active_learning.
  - **Full `aoi_active_learning` lifecycle** end-to-end: `validate_training_pool → baseline_eval → [rca → routing → anomalygen → data_mining → train → evaluate] ×3 → register_candidate_model → completed`. `progress` tracks `current_iteration/total_iterations/current_step/steps_completed`. **This matches the real DEFT AOI loop stage sequence** (good fidelity).
  - **Results** carry the DEFT inference handoff: `best_checkpoint, best_iteration, threshold, far_pct, backbone, images_dir, training_spec, metrics` — matches the real loop's `best_model.json`.
  - **AuthZ:** ownership enforced (bob→alice run = **403**; `admin:` scope = **200**). **Idempotency-Key:** same key → same run id. **Cursor pagination:** paged all 22 events across 5 pages via `after_seq`/`next_seq` (terminal end → `next_seq: null`). **SSE:** `/events/stream` streams `data: {...}` events. **Cancel→resume:** cancel→`cancelled` (200), resume (201).
  - **Validation:** strict (`extra_forbidden`) config schema with clear 422s; unknown run → 404; malformed UUID → 422.
  - **Served `/openapi.json` == repo `openapi.json`** (byte-identical) — no spec/impl drift.

## Gaps & recommendations (prioritized for the August blueprint)
1. **[P0 — production-critical] In-memory store → no persistence, no multi-replica.**
   `app/store.py` `RunStore` holds runs + idempotency keys in process dicts (`self._runs`, `self._idempotency`). A pod restart / rolling deploy **loses all run state**, and multiple replicas won't share it. For a Helm/k8s blueprint this must be a **DB-backed store** (Postgres/Redis + object storage for artifacts). The seam exists (swap `store.py`); it's acknowledged in the module docstring, but it's the #1 must-do.
2. **[P0 — production-critical] Single-process asyncio execution.** `app/runner.py` runs each pipeline as an asyncio task in one uvicorn process. Real orchestration must **offload stages to containers/KFP/SLURM** (the `runner.py` seam) and decouple run execution from the API process (job queue/worker) so the API can scale and survive restarts. Acknowledged.
3. **[P1 — security] Placeholder auth.** `app/auth.py` trusts the bearer token's *content* as the identity (no JWT/IdP verification). Fine for local demo; **must** be swapped for real token verification before any shared/remote deployment. Acknowledged in docstring.
4. **[P2 — usability] Inline `config` is strict and deeply nested** (`model`, `data_pool`, `kpi`, `pipeline`, `training`, `mining`; extras rejected). 422s are clear, but clients need the exact schema. Recommend: (a) ship a documented **example inline config** in the README (the `aoi_default` profile's config is a good template), and (b) add a **`POST /v1/fine-tune/validate` (dry-run)** endpoint so clients can check a config without launching a run.
5. **[P2 — real-swap correctness note] `results.far_pct` currently echoes the KPI target** (simulator). When `runner.py` is swapped for real, ensure `far_pct`/`metrics` report the **achieved** FAR at the recall operating point, and `threshold` is the real decision threshold from KPI analysis.
6. **[P2 — config coverage] Make the AnomalyGen branch explicitly configurable/skippable.** In the real loop, AnomalyGen (Cosmos SDG) is heavy/GPU-bound and is often skipped on smaller GPUs (mining-only DEFT). The `aoi_active_learning` workflow should let a client select mining-only vs mining+anomalygen via config, and the contract should surface that switch.

## Bottom line for handoff
The contract, validation, auth/ownership, idempotency, pagination, SSE, and results handoff
are all correct and test-covered — this is a clean, faithful API reference. The work
remaining before the blueprint ships is exactly the three seams the code already names:
**persistence (store.py), real orchestration (runner.py), and real auth (auth.py)** — plus
the two small usability/correctness items (dry-run validate + achieved-FAR in results).

---

# POSTSCRIPT (v0.2) — the gaps above are now implemented and E2E-validated

All P0/P1/P2 items from this report were subsequently implemented (see
`PRODUCTION_READINESS.md` for the full scorecard and deliberate scope):
real DEFT AOI execution on the docker backend (`DEFT_EXECUTION_MODE=real`),
SQLite persistence, static-API-key auth mode, real artifacts, achieved-FAR
results, mining-only AnomalyGen switch, docker-kill cancellation, GPU-slot
serialization, per-stage timeouts. The API surface is unchanged
(`openapi.json` still served byte-identical); all 14 contract tests pass
unmodified plus 9 new unit tests (23/23).

## E2E validation — a real DEFT run driven through this API

Workspace: `deft_workspace_0520` (VisualChangeNet, C-RADIOv2-B; 21,148 KPI
pairs / 38 defects; TITAN RTX; TAO 6.26.3-pyt). Submitted via
`POST /v1/fine-tune` (inline `aoi_active_learning` config: 1 iteration,
2 epochs, batch 8, mining-only). Run `d354a2b6`, stage events verbatim:

```
[baseline/validate_training_pool] ok   train=211 val=249 kpi=21148 (38 defects) pool=100; leakage=0
[baseline/baseline_eval]          ok   FAR@recall=100%=99.99% (AUC=0.514)   <- matches the same loop run by hand
[iter1/rca]                       ok   220 weak (200 PASS false-alarms, 20 near-miss defects)
[iter1/routing]                   ok   200 -> mining, 20 -> anomalygen
[iter1/anomalygen]                ok   skipped (num_sdg unset — mining-only)
[iter1/data_mining]               ok   mined 56/100 pool images (SigLIP k-NN, cosine>=0.9)
[iter1/train]                     ok   2 epochs, 267 rows (56 mined), real checkpoint saved (104s)
[iter1/evaluate]                  ok   FAR@recall=100%=99.67%, AUC 0.514 -> 0.778, threshold=0.1349
[post_loop/*]                     ok   best model registered + results published
```

`GET /results` returned the **real** trained checkpoint
(`.../iter1/train/train/changenet_model_classify_latest.pth`, on disk),
achieved FAR/threshold/AUC, and real artifacts (`deft_state.json`,
`loop_log.jsonl`, `DEFT_Loop_Report.html`, `best_model.json`,
`best_model_inference_spec.yaml`). The AUC lift 0.514→0.778 after a 2-epoch
smoke iteration is consistent with the by-hand trajectory (10 epochs reaches
~0.97); the run's purpose was validating the service, not the KPI.

The failure/recovery path was validated organically during this E2E: two
real TAO spec traps (`validation_interval > epochs`; backbone path outside
the mount) surfaced as structured `error` objects with the actual TAO
traceback via `/status`, the run **survived a service restart** (SQLite),
and each fix was applied by resuming the same run through `POST /resume`.
