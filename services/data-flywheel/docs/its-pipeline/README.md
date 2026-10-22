# ITS Pipeline — Architecture

**ITS** = Intelligent Transportation Systems (the Smart City flywheel). It is the second
DEFT-backed flywheel in this repo, alongside AOI.

| | |
|---|---|
| Ships as | `job_type: "its_job"` + `use_case: "its"` — two independent keys |
| Pipeline | `ITSPipeline`, a `job_type`-only subclass of the shared `DeftPipeline`. The DAG is AOI's, unchanged |
| Training | the same DEFT service, one new profile: `smart-city-object-detection` (owned by the DEFT team) |
| Model | `GDINO`, published as ONNX |
| Offline gate | `DeftITSEvaluator.kpi_eval` — its own constraint check over DEFT's metrics |
| Online gate | none. `DeftITSEvaluator.evaluate()` always returns `hold` with empty metrics — a verdict, never a promotion |

For running the loop end to end on a host — credentials, images, object-store layout, data
preparation and the promotion ladder — see `ITS_E2E_RUNBOOK.md`, kept with the deployment
repo rather than here because it is per-host. This document covers the architecture; the
runbook covers operating it.

---

## What ITS adds

ITS reuses AOI's machinery almost entirely. The pieces that are genuinely ITS's own:

1. **Pipeline registration** — `ITSPipeline`, a `job_type`-only subclass; the DAG is unchanged.
2. **Offline evaluator** — `DeftITSEvaluator.kpi_eval`, its own constraint check so one use
   case's edit cannot move the other's promotion decision.
3. **Trigger wiring** for `its_job`.
4. **Use-case-resolved storage** in the promotion controller, rather than storage assumed to be
   AOI's.

Everything else — the eight stages, the handlers, the Celery tasks, the Storage Backend client,
the Model Manager client — is shared code that both job types run.

### Not yet ITS's own

- **Online evaluation.** `DeftITSEvaluator.evaluate()` always returns `hold` with empty metrics.
  There is no agreed ITS online metric, and the shipped config sets `eval_harness.enabled: false`.
- **Per-use-case Model Manager routing.** Every promotion goes to the one
  `model_manager.base_url`; there is no ITS-dedicated control plane.
- **ITS-shaped KPI minting.** `POST /v1/kpi` sends a hardcoded `{'tag': 'kpi'}` selection, which
  is why `kpi.pull_from_storage` ships off (see the config table below).

---

## The two axes

`job_type` and `use_case` are independent. Neither is derived from the other, and no reader of
one is keyed on the other.

### `job_type: "its_job"` — the submission edge

Answers the questions asked before stage 1 exists: which pipeline the dispatcher builds, which
trigger branch runs, whether the validator demands `workload_id`, whether the backend accepts the
job, and which YAML block is the deployment default.

| Gate | File | What it does |
|---|---|---|
| The enum | `src/job_types.py` | `JobType.ITS = "its_job"` |
| The family | `src/job_types.py` | `DEFT_JOB_TYPES` + `is_deft_job_type()` |
| `workload_id` / `client_id` exemption | `src/api/schemas.py` | a family test, not `!= AOI` |
| Backend capability | `src/backends/deft/provider.py` | `DeftBackendProvider.supported_job_types = DEFT_JOB_TYPES` |
| Dispatcher + pipeline registry | `src/tasks/tasks.py` | `is_deft_job_type()` → `_DEFT_PIPELINES[job_type]` |
| Runtime-override guard | `src/tasks/tasks.py` | skips the NMP-shaped override path for the family |
| Pipeline declaration | `src/tasks/pipelines/its_pipeline.py` | `ITSPipeline.job_type = JobType.ITS` — the whole class |
| Trigger validators | `src/api/triggers/schemas.py` · `models.py` | both `Literal`s widened; `config_name` stays optional via the `oneOf` |
| Trigger executor | `src/tasks/triggers/executor.py` | `_is_deft` picks the DEFT branch |
| Config registry | `src/config/deft.py` | `DEFT_JOB_CONFIG_TYPES` — both keys, one class |
| YAML loader | `src/config/deft.py` | `load_deft_settings` loops the registry for `<job_type>_config` |
| Public schema | `openapi.json` | regenerated; `its_job` appears |

### `use_case: "its"` — the runtime

Answers the questions asked inside the stages.

| Reader | File | What it selects |
|---|---|---|
| Evaluator registry | `src/backends/deft/eval.py` | `_EVALUATORS["its"] = DeftITSEvaluator` |
| Storage client | `src/backends/deft/provider.py` | `StorageBackendClient(self._config.for_use_case(use_case))` at both `evaluator()` and `datastore()` |
| Endpoint map | `src/config/deft.py` | `use_case_endpoints` / `endpoints_for` / `for_use_case` |
| Fail-fast at init | `src/tasks/handlers/deft/handlers.py` | resolves the evaluator at `initialize`, not at the eval gate |
| Fail-closed snapshot | `src/tasks/handlers/deft/handlers.py` | `SelectAndSnapshotHandler._resolve_store` |
| Presign scope | `src/tasks/promotion/controller.py` | `_presign_for_pull` signs against `deft_config.for_use_case(record.use_case)` |
| Record identity | `src/api/promotion/models.py` | `PromotionRecord.use_case` is required, with no read-side shim — a document without one fails validation in `from_mongo` |

### The one place the axes touch

`PromotionController._load_policy` resolves the *deployment-default* policy by reading
`settings.job_configs["aoi_job"]` literally — a job-type-keyed read on an otherwise
use-case-keyed path. It is **pre-existing on `main`, not introduced here**, and it is only
reachable for a promotion record that carries no policy of its own: `StartPromotionHandler`
stamps the job config's `promotion_policy` onto the record, and that wins.

Should a record ever fall through to it on an ITS-only deployment (no `aoi_job_config:` block),
the substituted `PromotionPolicyConfig()` is **not** a loss of governance — measured, its defaults
are `hitl.canary: true` / `hitl.full: true`, so the approval gates are on. The mismatch is the
other way: it also defaults `deployment_stages.canary: true` with `canary_steps: [5, 25, 50]`, a
partial-traffic stage the shipped ITS config deliberately disables. The failure mode is an
**unwanted extra stage**, not a missing gate. Widening this to sweep the job-type family would
change AOI's resolution path, so it is deliberately left alone.

---

## The DAG

`ITSPipeline` inherits `DeftPipeline.build_workflow` verbatim
(`src/tasks/pipelines/deft_pipeline.py`) and adds only `job_type`. The eight stages:

1. `initialize_aoi_workflow` — mints `use_case` into the chain context, resolves the evaluator
2. `select_and_snapshot` — resolves this use case's store, requests an immutable snapshot
3. `run_deft_training` — profile-mode fine-tune. One DFW stage, but DEFT runs its own
   sequence inside it: `pseudo_label` (Co-DETR) → `pool_embeddings` → `inference_kpi` →
   `gap_analysis` → `embeddings` (SigLIP) → `mining` → `training_evaluation` →
   `publish_results`. DFW sees one stage and polls it; the profile owns the rest
4. `run_offline_eval_gate` — `kpi_eval` against `eval.kpi_target`
5. `register_candidate` — mint the serving version, record it in MLflow
6. `start_promotion` — hand to the Promotion Controller
7. `wait_for_promotion` — blocks the chain until the promotion terminates
8. `finalize_aoi_run`

Stage logic lives in `src/tasks/handlers/deft/handlers.py`, and the task definitions in
`src/tasks/deft_tasks.py`. The `AOI`-prefixed task and handler names are historical, not a
restriction — both job types run them. Renaming the Celery task wire names is deliberately **not**
done here: a task name is a wire identifier, so renaming one strands any message already queued
under the old name.

### Change map, stage by stage

| # | Stage | What ITS changes |
|:--:|---|---|
| 1 | `initialize` | Nothing. Fails fast on an unknown `use_case` |
| 2 | `select_and_snapshot` | Resolves the store by use case; fails closed on an undeclared one, because `use_case_endpoints` ships non-empty (see below). Selection facets ride flat, no client code |
| 3 | `run_deft_training` | Nothing. Profile mode already carries per-run refs on `overrides` |
| 4 | `offline_eval_gate` | `DeftITSEvaluator` + one line in `_EVALUATORS` |
| 5 | `register_candidate` | Nothing |
| 6 | `start_promotion` | Presigns against this use case's store rather than the top-level one |
| 7 | `wait_for_promotion` | Nothing — but see the capacity note below |
| 8 | `finalize` | Nothing |

### Capacity consequence

`its_job_config` ships `hitl.full: true`, and `wait_for_promotion` is unbounded. Every ITS run
therefore blocks in stage 7 until a human approves, holding the single concurrency-1
`parent_queue` slot — so no flywheel run of any job type starts while an ITS candidate is
waiting. This was already true of any AOI config with a HITL gate; what is new is that it is the
default for a whole job type. See
[docs/04-limitations-best-practices.md](../04-limitations-best-practices.md).

---

## Promotion

Stages 6–7 hand the candidate to the Promotion Controller, which drives it through the gates
`deployment_stages` leaves enabled:

```text
pull → deploy (shadow) → canary → full
```

ITS ships with `shadow` and `canary` both off, so the shipped ladder is `pull → full` with a
human in front of the switch. `canary` is off permanently (no partial-traffic routing exists);
`shadow` is off only until something is serving — see [Bootstrapping the first
promotion](#bootstrapping-the-first-promotion).

**`pull` is the expensive one.** The Model Manager stages the checkpoint into the model
repository, the serving stack exports ONNX and compiles a TensorRT engine, and only then is the
version loaded. That is minutes, not seconds — hence `model_manager.pull_timeout` (900s) rather
than the swap-settle budget, which is a different clock. `pull` changes no routing: it makes a
version *loadable*, nothing more.

Two things decide whether `pull` can succeed at all, and both fail late — after a full training
run has already cleared the offline gate:

- `model_name` must be a key the serving side's model catalog defines. An unknown name is
  refused before staging or export.
- The build is keyed on `{name, version}` with no fingerprint check, so a version number that
  already carries a completed build short-circuits and returns the earlier engine.

**The remaining gates change serving roles, not artifacts.** `hitl` decides which of them pause
for a human; `deployment_stages` decides which run at all.

### Bootstrapping the first promotion

A shadow deploy installs the candidate *alongside* the version currently serving. On a stack
where nothing has been promoted yet there is no such version, and the deployment backend refuses
rather than silently installing an unvetted candidate as the live primary. So the first
promotion on a fresh stack cannot be a shadow: it has to be a full switch, after which every
later candidate can shadow normally. That is why `deployment_stages.shadow` ships `false` —
leaving it on would fail the `deploy` gate with `registry_unreachable` on every attempt, after a
full training run had already cleared the offline gate. Turn it on once a version is promoted.

The policy is captured onto the promotion record when the promotion is created, so this is a
decision made *before* submitting, not one that can be changed while a run is in flight.

---

## The evaluator

`src/backends/deft/eval.py`:

- `DeftITSEvaluator` subclasses the neutral `DeftEvaluator`, **not** `DeftAOIEvaluator`. That is
  load-bearing: `DeftBackendProvider._is_aoi_family` tests
  `issubclass(evaluator_cls, DeftAOIEvaluator)` to decide whether the legacy `aoi_results_url`
  override applies, and ITS must answer no.
- `kpi_eval` (offline) is ITS's own constraint check, deliberately not shared with AOI's, so one
  use case's edit cannot move the other's promotion decision. It is fail-closed: a metric the
  payload never reported counts as unmet, and renders
  `macro_ap >= 0.55 (actual=None, reason=metric-not-reported)` so a wrong config key does not read
  like a bad model. An ordinary loss renders `(actual=0.41)` with no `reason=`.
- Metric lookup is case-insensitive, a cheap guard against a spelling drift in the emitted key.
- `evaluate` (online) returns `{"metrics": {}, "verdict": "hold"}` unconditionally, and logs a
  `WARNING` saying why. There is no agreed ITS online metric contract, and the shipped config sets
  `eval_harness.enabled: false`, so no ITS promotion should reach it — but if one does, `hold` is
  the fail-closed answer, the same one `DeftAOIEvaluator` gives when no online criteria are
  configured. `hold` never promotes (the beat only enqueues on `advance`/`rollback`), yet it *is*
  a verdict, so the observation window stays reachable and an `on_timeout: escalate` policy still
  fires. Raising instead — which is what this did — aborted the poll before the verdict write, and
  the beat's blanket `except Exception: continue` left the promotion in `promoting` forever,
  holding the concurrency-1 parent-queue slot.

The `unmet` detail reaches an `INFO` line on the `data_flywheel.backends.deft.eval` logger only.
`OfflineEval.run` repacks the verdict as `{metrics, passed}` and drops `unmet`, so
`FlywheelRun.error` — the field `GET /jobs/{id}` returns — does not name the constraint or the
reason. Propagating it would change AOI's operator-visible error text, so it is deliberately
not done.

`DeftITSEvaluator.collect` returns eval-set records unflattened. There is no per-record
normalisation because the ITS record shape is not agreed yet, and a wrong field-name guess is
silent: an unrecognised key yields a zero count that reads downstream as a real measurement of
zero. Nothing consumes these today — ITS has no online gate.

---

## Storage endpoints

`deft_config.use_case_endpoints` (`src/config/deft.py`, `UseCaseEndpoints`) maps a use case to
its own `storage_backend_url`, `object_store_uri`, `storage_api_key_env` and `presign_expires_s`.
`UseCaseEndpoints` is `extra="forbid"`, so a typo fails at startup rather than silently leaving
that use case on the top-level endpoints.

- `DEFTConfig.for_use_case(uc)` returns a copy with the overrides applied, or `self` — the
  identical object, `is` not `==` — when the use case declares an entry that overrides nothing.
  `for_use_case('aoi')` is field-for-field equal to the top-level config — pinned by
  `tests/unit/config/test_use_case_endpoints.py`.
- **`for_use_case` fails closed**, and that is where the rule lives: once the map is non-empty, a
  use case absent from it *raises* rather than silently inheriting the top-level endpoints. The
  guard sits on the single call every endpoint-resolving path already goes through — snapshot,
  presign, the promotion controller — instead of being repeated at each. The predicate is the
  *declaration*, never the name: `use_case != "aoi"` would be wrong, because an AOI-family use
  case registered against `DeftAOIEvaluator` legitimately shares the top-level endpoints and its
  name says nothing about that. An empty entry (`aoi: {}`) is
  exactly the statement that it inherits, which is why the check is membership rather than
  truthiness. A falsy `use_case` (`None`/`""`) is a legacy caller that never routed by one, so it
  inherits rather than being refused.
- `SelectAndSnapshotHandler._resolve_store` repeats the same check one stage earlier so the
  refusal names the snapshot, and logs the *resolved* endpoints — the record of what a run
  actually read. It is needed despite YAML startup validation because named job configs come from
  Mongo and never pass through it, and that is how an ITS run is launched.
- The **promotion pull gate is deliberately asymmetric**: `PromotionController._presign_for_pull`
  resolves the use case *inside* its presign `try`, so an undeclared use case logs a warning
  naming `use_case_endpoints` and falls back to the stored pull URL instead of raising. Snapshot
  must refuse (it decides what a run trains on); by the pull gate the artifact already exists, so
  failing the pull would only strand a promotion.

**The shipped map is populated** (`use_case_endpoints:` in `config/config.deft.yaml`):

- `aoi: {}` — stated, not omitted. With a non-empty map an omitted `aoi` would now refuse every
  AOI run.
- `its: {}` — same reason again, and the one worth spelling out because it used to be different.
  ITS shipped its own `storage_backend_url` / `object_store_uri` / `storage_api_key_env` triple
  pointing at `sc-backend`. It no longer does: **DFW is deployed per use case**, so a deployment
  fronts exactly one Storage Backend at deploy time regardless, and the Storage Backend is
  growing multi-use-case support of its own — so a second host was never what separated ITS data.
  Separation stays where it belongs, in distinct **prefixes** inside the shared bucket, which
  need no endpoint override at all. `DFW_ITS_STORAGE_BACKEND_URL`, `DFW_ITS_OBJECT_STORE_URI`
  and `ITS_STORAGE_API_KEY` are gone from Compose and from the deployment blueprint with it.
  The entry stays *empty rather than deleted*: the map is exhaustive, so deleting `its` would not
  restore inheritance, it would refuse every ITS run.
- A deployment that genuinely does front ITS with its own store still can — but it overrides the
  whole triple **together**, `storage_api_key_env` included. Unset fields inherit, so repointing
  only the URL would send this deployment's Storage Backend bearer to another host: a credential
  leak across a trust boundary, and a 401 if that store is stricter. Note that the two URLs are
  env-overridable like every other endpoint in the file while `storage_api_key_env` deliberately
  is not — it carries the NAME of the env var holding the bearer rather than the bearer itself,
  and rendering it would substitute the secret into the config.

Two consequences worth stating:

- The map is **exhaustive**, not additive. Any further live use case must be added at the same
  time, or its runs hard-fail at `for_use_case` — by design, since the alternative is reading and
  writing another use case's store with nothing reporting it.
- A named config POSTed to `/api/configs` cannot supply endpoints; they are deployment config and
  can only come from YAML.

---

## The shipped ITS config

`config/config.deft.yaml` → the `its_job_config:` block. The equivalent `POST /api/configs` body
is `config/its_config_example.json`. Pinned field-for-field by
`tests/unit/config/test_shipped_its_config.py`.

| Field | Value | Note |
|---|---|---|
| `use_case` | `its` | routes evaluator, store and policy |
| `model_name` | `GDINO` | MLflow registry key **and** the serving model-catalog key; the builder rejects an unknown name at the `pull` gate |
| `model_format` | `pth` | rides as `PullRequest.format` and names the artifact DEFT **hands over**, not what the serving stack ends up running. DEFT publishes `gdino_model_latest.pth`; the Model Manager stages it as `model.pth` and the builder picks the `gdino_pth` handler off that extension, which exports ONNX and compiles the engine itself |
| `deft.profile` | `smart-city-object-detection` | profile mode; `deft.overrides` is the sanctioned way to bend it |
| `deft.overrides` | `classes` · `mining` · `model.checkpoint` · `training` | the set driven end to end on a live stack. `model.checkpoint` must sit in the bucket named by `DFW_OBJECT_STORE_URI`/`AWS_BUCKET` — the backend strips only its own bucket from a URI, so another folds into the key and fails the prefix allowlist |
| `kpi.pull_from_storage` | `false` | the mint is AOI-shaped — a hardcoded `{'tag': 'kpi'}` selection `create_kpi_set` refuses to vary — so for ITS it yields an EMPTY set, not an error. Off by default; DEFT carves the split instead |
| `kpi.uri` | `null` | set `DFW_ITS_KPI_SET_URI` to judge against a FROZEN set; must name ONE object (`.tar.gz` of `images/<site>/` + `labels/` + `mapping.yaml`), not a prefix |
| `eval.kpi_target` | one constraint, `macro_ap >= 0.55` | key confirmed against deft-service MR !15 (draft) — see below |
| `selection.filters` | `created_after` only | flat facets, and only names the index template actually defines — an unknown name is accepted and matches nothing. `use_case: its` already routes to the ITS catalogue, so no `jobType` term is needed to separate rows; `tag` ships commented as the narrowing knob |
| `promotion_policy.deployment_stages` | `shadow: false`, `canary: false` | no partial-traffic routing exists, and a shadow needs something already serving |
| `promotion_policy.eval_harness.enabled` | `false` | no online evidence source exists |
| `promotion_policy.auto_rollback_on_regression` | `false` | it only acts on an online verdict |
| `promotion_policy.hitl.full` | `true` | the rollout pauses for a human |
| `promotion_policy.poll_max_attempts` | `90` | the swap-settle budget |
| `snapshot_poll_max_attempts` | `120` | ×5s = 600s; the store must decode video before it reports ready |

Data-plane names carry the `sc-` prefix; the control-plane key stays `its`. That split is the
design's.

### The offline gate metric key, and one value still unconfirmed

**The offline gate metric key is `macro_ap`.** Read off `_collect_kpi_metrics` in deft-service
`app/execution/its_adapter.py`: it is the mean of the per-class `AP` column, where `AP` is at
IoU 0.50 — the KPI CSV is fed by `all_ap50_results.parquet` and the classes carry AP50
thresholds. So it is the pooled scalar the earlier `mAP50` placeholder was reaching for. The full
set that call emits is `macro_ap`, `global_precision`, `global_recall`, `far_pct`, and per-class
`<class>.ap` / `.precision` / `.recall` / `.accuracy`.

Why this mattered: the constraint engine looks metrics up by name and treats a missing one as
unmet — correctly fail-closed. So a key that does not match DEFT's `ResultsResponse.metrics`
verbatim does not error, does not warn at load, and does not fail loudly: it rejects every
candidate forever while every stage of every run reports success. `mAP50` was such a key.

*Caveat: confirmed against deft-service MR !15, which is still a **draft**. The contract can move
before it merges — re-check the key at merge.*

Note also that `far_pct` is in that set. ITS has no online gate today partly for want of an
agreed online metric, and FAR is what AOI's online gate uses — worth revisiting.

**The media-retention selection facet — documented, deliberately not shipped.** The ITS catalogue
outlives the media it indexes, so a time-window selection mixes rows that still have a frame with
rows that only remember having had one. A selection facet is the only place DFW can catch that,
because it moves data by reference and never opens an object. But the facet name is a guess, and
the store's `SnapshotSelection` accepts unknown keys — so a guessed name is accepted and matches
nothing: an empty training pool, reported as success. That is worse than the hazard it was meant
to fix, and it would ride in the deployment default. The line ships commented out with its
rationale intact. *Owner: ITS store team.*

---

## External dependencies

| Service | Status | DFW client |
|---|---|---|
| DEFT fine-tune (`/v1/fine-tune`) | reuse, no change | `src/backends/deft/deft_client.py` |
| Storage Backend (`storage-backend:8080`, shared with AOI) | must serve the ITS use case | `src/backends/deft/storage_backend_client.py`, verbatim, no subclass |
| Model Promotion API (Model Manager) | reuse, no contract change | `src/lib/promotion/deployment_backend/model_manager_client.py` |

`smart-city-object-detection` is delivered in `deft-service/app/data/profiles.py`, on the
`its_active_learning` workflow — Grounding DINO with Co-DETR pseudo-labelling and SigLIP mining.
The `mining.topn` collision it used to hit is resolved: `MiningConfig` now treats `topn` as set
only when a caller supplies it explicitly, so a profile using `desired_unique_count` no longer
trips the mutual-exclusion validator.

The store team owes an implementation of the AOI Storage Backend's snapshot / results / eval /
presign surface, plus one ITS-only obligation: **decode video into sampled frames at snapshot
time**. DEFT's `ExperimentConfig` has no video-native fields, so a prefix containing video trains
on nothing.

---

## Reference

| What | Where |
|---|---|
| Running the loop end to end | `ITS_E2E_RUNBOOK.md` (deployment repo) |
| DEFT service (profiles) | `../deft-service/app/data/profiles.py` |
| External OpenAPI contracts | `../aoi-api/{storage-backend,deft_api,model_promotion,dfw_user_api}/openapi.json` |
| The DAG both job types run | `src/tasks/pipelines/deft_pipeline.py` → `src/tasks/deft_tasks.py` |
| The stage logic behind it | `src/tasks/handlers/deft/handlers.py` |
| The job-type family | `src/job_types.py` |
| The evaluator seam | `src/backends/deft/eval.py` |
| The promotion state machine | `src/tasks/promotion/controller.py` |
