# DEFT service — production readiness (v0.2)

v0.2 replaces the pre-0.2 in-process simulator with a real execution engine
and closes the P0/P1 gaps flagged in `DEFT_SERVICE_TEST_REPORT.md`. This doc
is the honest scorecard of what is now production-grade, what is deliberately
scoped, and what remains.

## What's implemented (and how)

| Area | v0.1 (reference) | v0.2 |
|---|---|---|
| Execution | `asyncio.sleep` + synthetic FAR formula | **Real DEFT AOI pipeline** (`DEFT_EXECUTION_MODE=real`): TAO VisualChangeNet train + inference containers, KPI threshold sweep, RCA, routing, SigLIP k-NN mining, monotonic CSV assembly + provenance, leakage gates, KPI-gated best-checkpoint selection, early stop on `pipeline.kpi_target` |
| Results | hardcoded `threshold=0.237481`, fabricated FAR curve | **Achieved** FAR@recall=100% / threshold / AUC from the real inference CSV; `best_checkpoint` is a real file on disk |
| Persistence | in-memory dicts (restart = total loss) | **SQLite (WAL)** write-through on every stage transition; restart marks mid-flight runs failed (resumable); idempotency keys persist |
| Auth | trusts bearer token content | `DEFT_AUTH_MODE=static` + `DEFT_API_KEYS` (token→subject, admin scope); `trusted` remains for local demos with a startup warning |
| Cancellation | flag checked between sleeps | flag between stages **and `docker kill`** of the in-flight container |
| Artifacts | URIs to nonexistent paths | real files under `<workspace>/results/service_run_<id>/`: `deft_state.json`, `loop_log.jsonl`, `DEFT_Loop_Report.html`, `best_model.json`, `best_model_inference_spec.yaml` |
| GPU safety | n/a | GPU-slot semaphore (`DEFT_GPU_SLOTS`) serializes concurrent runs on single-GPU hosts; per-stage wall-clock timeout (`DEFT_STAGE_TIMEOUT_S`) |
| Secrets | n/a | `HF_TOKEN` forwarded to stage containers, redacted from stage logs |

The API surface is **unchanged** — `openapi.json` remains the contract and the
served spec is still byte-identical to the repo file. All 14 original contract
tests pass unmodified (simulate mode is the default), plus 9 new unit tests
(KPI math, CSV/leakage rules, store persistence across restart).

## Workspace contract (real mode)

Real mode operates on a DEFT AOI workspace laid out per the skill bank's
`tao-run-deft-aoi` Data Contract. Config mapping:

| ExperimentConfig field | Meaning | Default |
|---|---|---|
| `data_pool.path` | workspace root (local path / `file://`) | required |
| `model.checkpoint` | base ChangeNet checkpoint | required |
| `training.initial_spec_path` | TAO train spec | `specs/baseline_spec.yaml` |
| `kpi.inference_spec_path` | TAO inference spec | `specs/baseline_inference_spec.yaml` |
| `kpi.path` | KPI csv | `kpi/testing_set.csv` |
| `pipeline.iterations` / `kpi_target` | loop budget / early-stop (e.g. `FAR<0.5%@recall=100%`) | 5 / none |
| `pipeline.num_sdg` | unset ⇒ **mining-only** (AnomalyGen skipped) | unset |
| `training.epochs` / `batch_size` | per-iteration fine-tune | 5 / 8 |
| `mining.min_similarity` / `topn` | k-NN cosine gate / per-target keep | 0.9 / 5 |

Hard-won container rules encoded in the engine (each one cost a failed run
during by-hand validation): stage `results_dir` and every path handed to TAO
are **container** paths under the workspace mount; `train.checkpoint_interval=1`;
predict reads `dataset.classify.infer_dataset.csv_path`; base CSV rows get a
`kpi/images/` prefix when specs are re-rooted to the workspace; mined pool rows
use `input_path=augmentation/mining_pool/images` + `golden_path=kpi/images/<pool
golden>`; `PASS` stays uppercase, all other labels lowercase.

## Deliberate scope (documented, not hidden)

1. **Backends** — real execution is implemented for the **docker** backend.
   `kfp` / `slurm` / `brev` submissions in real mode are rejected at
   `POST /v1/fine-tune` (422) rather than failing mid-run. The
   `docker_backend.py` seam is the template for adding them.
2. **Workflows** — `aoi_active_learning` runs for real; `tmm_only`
   (GroundingDINO) real execution is rejected at submission (422) and remains
   simulate-only.
3. **AnomalyGen** — the stage exists and is cleanly skipped when
   `pipeline.num_sdg` is unset (mining-only, the recommended default). When
   requested, it fails fast with an actionable message: synthetic generation
   needs the `paidf-anomalygen` container (~22 GB assets, ≥48 GB GPU), which
   the single-GPU blueprint host cannot run. Wire it behind the same
   `ContainerSpec` seam when a capable host exists.
4. **Resume** — a restarted service marks mid-flight runs failed;
   `POST /resume` restarts the pipeline from the beginning into the same run
   directory (completed-stage artifacts are preserved on disk). Incremental
   stage-level resume (the skill's `deft_state.json` semantics) is future work.
5. **SQLite** — right-sized for the single-node blueprint. For multi-replica
   k8s, swap `store.py` for Postgres/Redis; the interface is unchanged.
6. **Scores** — `far_pct` in results/metrics is the **achieved** FAR at
   recall=100% on the KPI set (the honest number; small defect counts make it
   volatile — the metrics dict also carries AUC and FAR@95%).

## Runbook

```bash
# simulate (default; contract demos, CI)
uvicorn app.main:app --port 8000

# real execution on the docker backend
export DEFT_EXECUTION_MODE=real
export DEFT_AUTH_MODE=static DEFT_API_KEYS="s3cr3t:tejas,r00t:admin:root"
export DEFT_DB_PATH=/var/lib/deft/deft_service.db
export HF_TOKEN=...            # SigLIP pulls inside stage containers
uvicorn app.main:app --port 8000
```

Submit (mining-only, 3 iterations, stop early at FAR<1%):

```bash
curl -X POST :8000/v1/fine-tune -H "Authorization: Bearer s3cr3t" -d '{
  "workflow": "aoi_active_learning",
  "config": {
    "model": {"name": "visual_changenet_classify",
               "checkpoint": "results/<baseline_run>/model_epoch_019.pth"},
    "data_pool": {"path": "/workspaces/aoi_ws"},
    "pipeline": {"iterations": 3, "kpi_target": "FAR<1%@recall=100%"},
    "training": {"epochs": 10, "batch_size": 8, "num_gpus": 1}
  }}'
```

## E2E validation

See the postscript in `DEFT_SERVICE_TEST_REPORT.md`: a real
`aoi_active_learning` run was driven end-to-end through this API against the
`deft_workspace_0520` AOI workspace (21,148 KPI pairs / 38 defects) on a
TITAN RTX, producing a real trained checkpoint and achieved KPI numbers that
match the same loop executed by hand.
