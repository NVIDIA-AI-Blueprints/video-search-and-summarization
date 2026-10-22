# AOI Model Manager (`aoi-model-manager`)

Standalone service implementing the **DFW Model Promotion API**
(`aoi-api/model_promotion/openapi.json`). It is the single component that
touches the AOI serving stack: it stages model versions onto the shared model
repository and loads them, activates them via the routing API
(`shadow`/`canary`/`full_switch`/`unload`), reports serving state, and reclaims
retired versions.

It also owns the model repository's **lifecycle on disk** — it is the only
component that writes version directories there, and therefore the only one that
deletes them. The serving stack owns *serving state* and is the authority on
whether a version is safe to remove.

## Why it is a separate project

The Model Manager has its own `pyproject.toml`, `uv.lock`, and `Dockerfile` and
**does not import from DFW's `src/`**. The only coupling to DFW is the HTTP
Model Promotion contract, so the folder can later be lifted into the AOI Client
Space with zero DFW code changes (just repoint `model_manager.base_url`).

## Architecture: the `ServingAdapter` seam

`ModelManager` owns the **invariant** promotion orchestration and knows nothing
about any specific serving stack:

- the stateless convergence read,
- the bounded, advisory inline wait after an apply,
- error-envelope mapping, and
- a *generic* convergence check over a normalized `ServingState`.

Every **client-specific** action lives behind the `ServingAdapter` interface
(`src/adapters/base.py`): staging/load, translating the Model Promotion control
verbs (`shadow`/`canary`/`full_switch`/`unload`) into backend calls, and reading
back the backend's serving/routing state. Adapters return a normalized
`ServingState`, so `ModelManager` never sees a client-shaped payload.

```text
src/adapters/
  base.py        # ServingAdapter ABC + normalized ServingState
  registry.py    # get_adapter(name) -> ServingAdapter (selected by config)
  activation.py  # shared staging: download/verify/publish/delete, backend-agnostic
  aoi/           # the legacy AOI adapter (/v1/models/*, /v1/model-routing)
    adapter.py   #   AOIServingAdapter: verb->AOI mapping + state composition
    inference.py #   AOI Inference HTTP client (load/unload/list/ready)
    client.py    #   AOI Client HTTP client (GET/PUT model-routing)
    activation.py#   ExplicitActivation/PollActivation: the AOI load protocol
  unified/       # the Unified Model Control API adapter (one, for any backend)
    client.py    #   UnifiedModelClient: load/unload/routing/status
    state.py     #   pure: model-status payload -> ServingState
    inventory.py #   pure: status + repo listing -> VersionInventory
    errors.py    #   unified error envelope -> ModelManagerError
    activation.py#   UnifiedActivation: the unified load/unload
    adapter.py   #   UnifiedServingAdapter: the six seam methods
src/clients/http.py  # shared transport (bounded retries) used by all clients
```

The adapter is selected at startup from `MODEL_MANAGER_CLIENT` (default `aoi`):

| value | backend |
|---|---|
| `aoi` | legacy AOI control plane |
| `unified` | any backend speaking the Unified Model Control API |

There is **one** unified client, not one per use case. It sends only the
contract's portable core — the fields every implementation must accept — and
takes load completion from the response status (`200` completed, any other 2xx
accepted-and-warming), which is what the contract instructs clients to do. So
nothing has to declare which implementation is on the other end: `unified_url`
says where it is, and the promotion policy on the DFW side says which rollout
stages to ask for.

### Unified client caveats

* `MODEL_MANAGER_UNIFIED_URL` replaces the `MODEL_MANAGER_AOI_*` addresses: the
  unified contract serves load, unload, routing and status from one base URL.
* **Unload is allowed by default**, because both backends this adapter ships
  against evict per version — verified at the wire. `deft-aoi-inference` was
  fixed by `0fce90f3` (2026-08-05), which pins Triton's `version_policy` to
  `specific:[primary, candidate]`; RTVI-CV refuses to evict a routed version at
  all (`409 MODEL_IN_USE`). An earlier deft build did unload the whole model,
  taking live traffic with it, which is why `MODEL_MANAGER_UNIFIED_ALLOW_UNLOAD`
  survives as a switch: granularity is a property of the *build*, not of the
  contract, so set it false against an older image. `false` disables the
  eviction and, with it, the reclaim that is downstream of it.
* `VersionInventory.deletable` has two sources. Where the backend publishes a
  top-level `deletable` list it is authoritative and used verbatim. Where it
  publishes none, at most **one** version is inferred, and only the one whose
  eviction the backend attested to in the same call — a 2xx unload, which it
  refuses with `409 MODEL_IN_USE` for a routing target or a stream-attached
  instance. Everything downstream of that attestation can only withhold. A
  version that was never loaded has no attestation and is never reclaimed.
* **RTVI-CV needs the streams named.** It merges the default routing policy but
  does not re-bind a stream that is already attached, so promoting a live shadow
  with a default-only write makes it build a fresh inference bin for the
  candidate against the *outgoing* version's Triton ensemble — which is being
  evicted at that moment — and the process exits. Set
  `MODEL_MANAGER_UNIFIED_STREAM_ROUTES=true` there and every routing write also
  carries `routes: [{"streams": "all", ...}]`, in the same request. Leave it
  false for AOI Inference, which *rejects* the field
  (`supports-explicit-stream-routes: false`); there is no capability endpoint to
  discover this from, so it is a per-deployment setting.
* A backend without canary routing must be paired with
  `promotion_policy.deployment_stages.canary=false` on the DFW side. The Model
  Manager does not carry a table of which backend supports what — that would
  duplicate the policy, and duplicate it wrongly, since a serving stack can also
  disable a routing mode per deployment. A backend that will not accept the stage
  says so (`400 FIELD_UNSUPPORTED` / `409 MODE_DISABLED`), and the adapter turns
  that into an error naming the policy key to change.

### Adding a new client

1. Create `src/adapters/<client>/` and implement `ServingAdapter` (`stage`,
   `apply_control`, `serving_state`, `unload`, `inventory`, `delete_version`),
   mapping your backend's routing/registry payloads onto the normalized
   `ServingState` and `VersionInventory`.
2. Register its factory in `src/adapters/registry.py` under a new key.
3. Add `<client>` to the `client` literal in `src/config.py` and select it via
   `MODEL_MANAGER_CLIENT=<client>`.

No changes to `ModelManager`, the API layer, or the Model Promotion contract are
required — the orchestration is already client-agnostic.

## AOI control-plane contract

The authoritative document is the
[Model Manager Integration Recipe](https://gitlab-master.nvidia.com/metromind/metropolis-aoi/deft-aoi-inference/-/blob/v0.0.0/docs/model-manager-recipe.md)
(deft-aoi-inference `v0.0.0`). The mechanics that matter here:

### Control-plane endpoints are a singleton

HAProxy pins every `/v1/models/*` **and** `/v1/model-routing` request to the
single GPU/control replica; only inference itself fans out. One address therefore
serves both APIs, which is why `aoi_control_url` is the primary setting and the
per-API URLs are overrides (see [Configuration](#configuration)).

### Staging

Weights are written to `{repo}/{model_name}/{version}/model.pth` (plus an
optional `spec.yaml` and a `metadata.json` sidecar). The serving stack resolves
the artifact by `{model_name, version}` and reads a canonical `model.<ext>`
(`.pth`/`.onnx`/`.plan`, defaulting to `.pth`), so staging writes that canonical
name rather than the source basename.

> **The version directory must be writable by the serving process (UID 1000).**
> It writes the exported `model.onnx`/`model.plan` back *into* the directory the
> Model Manager created. Set `securityContext.fsGroup: 1000` in Kubernetes, or a
> matching group on a bare-Docker bind mount. Otherwise the load fails with a
> `PermissionError`, which the Model Manager surfaces as a **non-retryable**
> `load_failed` naming this fix — retrying cannot repair a mount.

### Loading is asynchronous, and `pull` waits for it

A cold `.pth` load exports ONNX and builds a TensorRT engine, taking minutes —
longer than the 120s HAProxy timeout in front of the control plane. So `pull`:

1. `POST /v1/models/load?wait=0` → `202 {build_id, phase: "building"}`;
2. polls `GET /v1/models/build-status` until `succeeded` or `failed`.

The wait lives in `pull` (not `apply`) because **a routing PUT is rejected while
its target is not READY** — there is no "route it now and let it warm up" state.
DFW budgets `POST /v1/models/pull` 900s for exactly this. `MODEL_MANAGER_LOAD_ASYNC=false`
selects the synchronous fallback (`wait` omitted); it holds one connection open
for the whole build and only works where nothing in between times out first.

Recovery paths, none of which start a duplicate build:

| Situation | Recovery |
|---|---|
| Model Manager restarted, `build_id` lost | Poll `build-status?model_name=…&version=…` (in-memory registry; survives an MM restart, not a control-service restart) |
| `build_id` no longer resolves (404) | Fall back to the `{model_name, version}` lookup |
| `phase=idle` (HTTP **200**, not 404) | Nothing in flight — confirm the real outcome against `GET /v1/models` |
| Load POST timed out | Ambiguous, so it is **not** replayed: ask `build-status` what is running |
| `409 idempotency_in_progress` | Already re-attached to the running build; keep polling (**not** an error) |

Only after all of those come up empty is the load re-POSTed, once, with the same
key — which rebuilds solely if the previous attempt *failed* (failures are not
cached; the reservation is released deliberately).

**Idempotency keys.** The caller's `idempotency_key` is used verbatim when
supplied: DFW sends `{promotion_id}:pull`, which is stable across its own retries
and is therefore what re-attaches after a restart. When absent, a key is derived
from the weights digest — `sha256(model:version:weights-digest)`, which does
not cover `spec.yaml` or the threshold — preserving the
property that the same weights always reserve the same build.

### Two request gotchas for `load`/`unload`

Both endpoints read **typed body fields**, so both calls always send an explicit
`Content-Type: application/json` **and** an explicit `version`. With either
missing, `version` silently defaults to *every staged version*, and a call meant
for one version rebuilds or evicts the whole model. `PUT /v1/model-routing` reads
the raw body and is unaffected.

### Routing

`PUT /v1/model-routing` with `single` | `shadow` | `canary`. Refs are written
canonically as `{model_name, model_version}` and read tolerantly (legacy
`{name, version}` still accepted). `canary_percent` is a percentage in the
**exclusive** range `0 < p < 100`, so a `canary_weight` of 0 or 1 — or none at
all — is rejected up front with a message saying to use `shadow` or
`full_switch` instead, rather than emitting a PUT the backend will refuse.

### Cleanup is a collector: it reclaims disk and never evicts

`cleanup` calls `unload` nowhere. The eviction it used to perform had already
happened hours earlier — the promotion path unloads the outgoing baseline at the
full switch — so re-asking got `404 MODEL_NOT_LOADED` on backends that drop the
row and reclaimed nothing. Evicting is `POST /v1/models/unload`, on its own
clock, called by whoever retires the version.

That costs the guarantee that used to be structural: the guards and the `unload`
that minted the delete authority were one function, so no handler could reach a
delete without evicting in the same call. What replaces it is an explicit
conjunction, in which **every term can only withhold**:

1. **The caller nominates.** DFW's sweep only offers versions whose promotion is
   terminal, settled, and carries an eviction stamp. A nomination never
   authorises anything on its own.
2. **The backend affirms**, by one of three routes depending on what it
   publishes: its own top-level `deletable` array (used verbatim and never
   recomputed locally — it unions on-disk, loaded and routed state this service
   cannot observe from outside — minus only entries the same payload reports no
   row for, because every per-version guard is written "if a row says so" and an
   entry with no row clears all of them at once); a tombstone row reading
   `state: unloaded`; or,
   on a backend that *drops* the row when a version is unloaded, the caller's
   `evicted_by_caller` assertion. That last one is the single term that is not a
   read, and it satisfies the absent-row branch and nothing else.
3. **Model Manager's own roots refuse**, each on its own read: routing,
   residency, attached streams, another **live** version serving out of this
   directory, and a `.staging.lock` saying a pull is writing into it right now.

   An in-flight **build** is deliberately not on that list, although this
   paragraph used to claim it was. `VersionState.building` is filled from a
   backend row only by the AOI adapter, so on the unified adapter the clause is
   structurally dead, and the unified build check reaches a decision only through
   the `deletable` *inference* — which a backend that publishes its own list
   never runs. What covers the build window there is the staging marker, which a
   pull holds across the whole load, engine build included.

   Two of those refuse only while the condition holds, which is the point. A
   neighbour that once loaded out of this directory keeps publishing the paths it
   read, so the aliasing root turns on that neighbour still being resident rather
   than on the path matching. And a `.staging.lock` outlives the process that
   wrote it — a pull killed mid-stage never removes one — so past
   `MODEL_MANAGER_STAGING_LOCK_MAX_AGE_S` (24h) it reads as abandoned and is
   reported at `WARNING`, with the marker's own contents, on every sweep that
   sees it. Both refusals used to be permanent, which is a disk that fills and a
   log that says only "a pull is staging" about a pull that died weeks ago.

Those roots are scored against a **destructive** read of the serving state, not
the one a swap takes. Readable is not complete: a routing source that answers
`200` while stating no policy is "nothing is routed" for a swap and *unknown* for
a destructive verb, so on this path it makes `routing_available` false and every
routing guard refuses instead of passing vacuously over a live version.

`POST /v1/models/unload` is scored the same way, and shares the refusal stack —
including the stream refcount. The two things a vacuous routing guard must not
wave through are an `rmtree` and an eviction: taking a version out of GPU memory
while streams are attached to it, or while it is the live default, is not a
smaller failure than deleting its weights. The cost is stated rather than hidden:
an eviction can now be refused with a retryable `503` when the routing endpoint
answers without a default slot. That is the cheap side of the trade, because the
promotion path logs a failed baseline unload **without failing the promotion** —
so a refusal leaks a GPU slot until the next retire and never breaks a promotion.

There is no shared snapshot, and that is deliberate rather than an oversight:
one reclaim takes three status reads and two routing reads, ordered so the read
that **authorises** is the oldest and the reads that **refuse** are the newest,
the last taken immediately ahead of the `rmtree`. Every term can only withhold,
so two reads that disagree refuse. An unreadable inventory is therefore a
retryable `503` and not a `200` with `disk_reclaimed: false` — with nothing
stranded behind it, "I could not find out" is the honest answer, and a sweep
handed a silent `false` would record the version as processed and never come
back.

`CleanupResponse` answers twice over, because the two answers differ.  `status`
is the postcondition — `removed` when the version's directory is not on the
repository as the call returns, `kept` when it is still there — and
`disk_reclaimed` is whether the caller may stop offering the version. `false`
there means every guard ran and the answer was no, or a dry run was asked for; a
refusal raises.

A directory that is **already gone** is what separates the two from "did the
`rmtree` run": it answers `removed` with `disk_reclaimed: true` having deleted
nothing, because the goal state holds and reporting it as a refusal re-offered
that version to the sweep on every tick, forever — the permanent loop the
`DIRECTORY_ABSENT` age answer was meant to end, one HTTP status lower. It is
answered **below** the guards, so a version whose directory has vanished while
routing, residency or a neighbour's artifacts still name it is still a `409` and
not a tidy success, and absence is proven rather than assumed: an unreadable
directory, or a repository root that is not there at all, reads as unknown, so an
unmounted volume cannot report every version in the deployment as reclaimed. A
dry run over an absent directory reports `removed` with `disk_reclaimed: false`
— observing costs nothing, and a `true` is what makes the caller retire its
nomination.

`status` was a one-valued `Literal["removed"]` until this release, so `kept` is a
value no caller has seen before it. DFW's client passes the field through and
compares it to nothing; a consumer validating against the older one-value enum
would reject it, which is the deploy order this MR already requires — Model
Manager first.

## Dependencies / lockfile

This repo does **not** commit a hand-written `uv.lock`. Generate it before
building or running:

```bash
cd model-manager
uv lock          # generates uv.lock from pyproject.toml
uv sync          # installs into .venv
```

## Run locally

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8090
# Open http://localhost:8090/docs
```

## Endpoints (Model Promotion API)

| Method | Path | Response |
|---|---|---|
| GET | `/v1/health` | `HealthResponse` (`{"status":"ok"}`) |
| POST | `/v1/models/pull` | `PullResponse` (201) |
| POST | `/v1/models/apply` | `ApplyResponse` (202, carries `applied` — the poll token) |
| POST | `/v1/models/convergence` | `ConvergenceReport` (200) |
| GET | `/v1/models/status/{swap_id}` | **410 Gone** — poll `convergence` instead |
| POST | `/v1/models/unload` | `UnloadResponse` (200) — evicts only, keeps the directory |
| POST | `/v1/models/cleanup` | `CleanupResponse` (200) — reclaims the directory of a version the serving stack reports reclaimable (see the three routes above); never evicts (`status` is `removed`/`kept`, and `disk_reclaimed` can be `false`) |
| GET | `/v1/models/{model_name}/serving-state` | `ServingStateResponse` (200) |

`POST /v1/models/pull` is the long one: it downloads the weights **and** waits out
the engine build, so it is measured in minutes. `apply` is short by construction —
its target is already READY by the time it runs.

## Configuration

Environment variables, prefix `MODEL_MANAGER_`:

| Env var | Default | Purpose |
|---|---|---|
| `CLIENT` | `aoi` | Serving adapter selected from the registry. |
| `AOI_CONTROL_URL` | `http://localhost:5000` | Base URL of the AOI control plane, serving both `/v1/models/*` and `/v1/model-routing`. |
| `AOI_INFERENCE_URL` | _(unset)_ | Overrides the control URL for `/v1/models/*` only. |
| `AOI_CLIENT_URL` | _(unset)_ | Overrides the control URL for `/v1/model-routing` only. |
| `MODEL_REPO_PATH` | `/models` | Shared model repository root; must be writable by UID 1000. |
| `ACTIVATION_MODE` | `explicit` | `explicit` stages + loads; `poll` is a placeholder raising `NotImplementedError`. |
| `MODEL_NAME` | `aoi-secondary-inspection` | Default model name when a request omits one. |
| `REQUEST_TIMEOUT` | `30.0` | Per-request timeout for ordinary control-plane calls. |
| `REQUEST_MAX_RETRIES` | `2` | Bounded retries for transient downstream failures. |
| `REQUEST_BACKOFF_BASE` | `0.5` | Exponential backoff base, in seconds. |
| `LOAD_ASYNC` | `true` | Use `?wait=0` + build-status polling. `false` selects the synchronous fallback. |
| `BUILD_POLL_INTERVAL` | `5.0` | Seconds between build-status reads. |
| `BUILD_POLL_TIMEOUT` | `600.0` | Total budget for one build, comfortably clearing the ~5-6 min cold build while staying inside DFW's 900s `pull_timeout`. |
| `BUILD_STATUS_REQUEST_TIMEOUT` | `10.0` | Per-request timeout of a single build-status read. |
| `READINESS_MAX_CHECKS` | `15` | Inline post-`apply` readiness checks (fast path only). |
| `READINESS_POLL_INTERVAL` | `2.0` | Seconds between those checks — a 30s inline budget in total. |

The two per-API URL overrides exist because the APIs were once deployed as
separate services, and the deployment wiring still sets both env vars (today
pointing at the same address). Unset, they resolve to `AOI_CONTROL_URL`, which is
the single-control-replica topology the serving contract describes.

`BUILD_POLL_TIMEOUT` is deliberately not derived from `REQUEST_TIMEOUT`: that
bounds one cheap status read, not the length of a build.

### Artifact sources

`artifact_uri`/`spec_uri` are fetched over `file://` (or a bare path) and
`http(s)://`, the latter streamed to disk so multi-GB weights are never buffered
in memory. DFW resolves object-store locators to short-lived presigned HTTPS URLs
before the pull, so `s3://` arrives through the `http(s)` branch; a literal
`s3://` or `mlflow://` URI returns `501`.

## Optional pre-load engine build

The ONNX -> TensorRT build used to happen inside `POST /api/v1/model/load`. A
multi-minute GPU build on the serving process drops frames on live traffic, so it
can be split out into a separate Model Builder service. Not every deployment runs
one -- the AOI backend builds inside its own load -- so the step is optional and
`MODEL_MANAGER_BUILDER_URL` is the entire switch:

| `MODEL_MANAGER_BUILDER_URL` | Behaviour |
|---|---|
| unset or empty (default) | No build step. No HTTP request is made to any builder and the load body is byte-identical to a build-less deployment. |
| set | Between staging and load, `POST /api/v1/model/build` runs and is polled to `ready`; the `extra_load_params` it returns are relayed **verbatim** into the load. Any build failure fails the promotion. |

Build, load and warm-up share **one** deadline (`MODEL_MANAGER_UNIFIED_LOAD_TIMEOUT`); each stage
gets what is left of it rather than a fresh budget, so enabling the builder cannot push a pull past
what the caller is prepared to wait for.

Relaying is not profile-sniffing: the client never learns what those keys mean,
and the builder's presence -- not the backend's identity -- is what decides. The
builder is idempotent on `{name, version}` (a repeat answers *"already exists or
is in progress"*), so a re-driven pull re-attaches to the running build instead of
starting a second one.

## The service holds no promotion state

There is no swap store and no swap id to resolve. `apply` returns an `applied`
object — the expectation it just acted on — and the caller hands that straight
back to `POST /v1/models/convergence`, which answers from live serving state:

```text
apply  -> { "applied": { model_name, to_version, control, from_version,
                         canary_weight, key }, ... }
poll   -> POST /v1/models/convergence  { "expectation": <that same object> }
       -> { "converged": bool, "reason": { code, message, terminal, detail } }
```

The caller **echoes** the object; it never builds one. Both `Applied` and
`ConvergenceRequest` forbid extra fields, so a re-serialised mirror of the token
would start failing the moment either side gained a field — passing the object
through verbatim is what keeps the round trip identity-preserving.

Two properties follow, and they are why the store was removed:

- **A restart costs nothing.** Nothing is in flight to forget, so a Model
  Manager that dies between an apply and the caller's first poll answers that
  poll exactly as the old process would have.
- **Replicas need no coordination.** Any instance can answer for an apply
  performed by any other, because the answer is derived from the backend.

Previously `SwapStore` was a process-local dict, so a restart made
`GET /v1/models/status/{swap_id}` answer `404` for a swap that was in fact fine,
and the caller could not distinguish that from a swap id it had invented. That
failure was **faster and harder than it looked**: the 404 was enveloped
`retryable=false`, so DFW's `_is_transient_error` honoured it, `_retry` declined
to retry, and the poll loop had no handler for it — the promotion was marked
`failed` on the **first poll, at ~0s**, not after polling out its 900s budget.

That route is now a **410 tombstone**. 410 rather than 404 precisely because a
404 there was indistinguishable from the bug: the swap is not unknown, the
surface is retired.

### Who decides what

The Model Manager answers *"has it landed, and can waiting still help?"* —
terminality is knowledge about the backend. The caller answers *"how much longer
will I wait?"* — the budget is orchestration. So `ConvergenceReport` carries no
attempt counts and no deadlines, and the inline wait after an apply is
**advisory**: it exists only so an ordinary routing flip is already converged by
the time the first poll arrives. It never declares failure. A candidate still
coming up is left to the caller's budget.
