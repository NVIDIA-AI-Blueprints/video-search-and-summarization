<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Migrating a skill-eval from Brev to OpenShell

The first OpenShell path did **not** land as a new daily/overlay workflow.
It was added to the existing PR dispatcher,
[`.github/workflows/skills-eval.yml`](../../workflows/skills-eval.yml), so
`vss-deploy-test-openshell` can run Harbor **on the GPU guest** instead of
hopping through the Brev coordinator and the `vss-eval-*` pool.

`.github/workflows/skills-eval-daily.yml` is **not** part of this branch’s
diff vs `origin/develop`. Daily OpenShell overlays live on other branches;
do not copy them here as the migration pattern.

Two ways to put another skill on OpenShell:

1. **Add it to skills-eval** — same workflow, planner allowlist, in-guest
   Harbor (same shape as `vss-deploy-test-openshell`).
2. **Give it a separate workflow** — own YAML, still reuse `plan_matrix.py`
   / `run_leg.py` / guest pin. Use this when you do not want the skill on
   every `pull-request/<N>` skills-eval run.

Read this file, then
[README.md](../README.md) (spec schema) and
[AGENTS.md](../AGENTS.md) (in-guest Harbor rules).

---

## What already changed (for `vss-deploy-test-openshell`)

Placement: GitHub labels + GPU **count**. Sizing: live `nvidia-smi` on the
guest. Brev pool selection is skipped when
`SKILL_EVAL_LOCAL_GPU_INSTANCE` is set.

### Workflow and planner (placement)

| File | What changed |
|---|---|
| `.github/workflows/skills-eval.yml` | `OPENSHELL_GPU_FLEET: "1"`. Matrix legs carry `local_gpu`. OpenShell jobs set `SKILL_EVAL_LOCAL_GPU_INSTANCE` from `$RUNNER_NAME` (do not key on name prefixes; H200 looks like `h200-2-g10-…`). Guests take Anthropic/NGC/HF from **repository secrets**, not `/home/ubuntu/eval-coordinator/.env`. Strip corp `LLM_REMOTE_*` / `VLM_REMOTE_*` and inherited `BREV_INSTANCE`. |
| `.github/skill-eval/plan_matrix.py` | `OPENSHELL_SKILLS` (today only `vss-deploy-test-openshell`). `openshell_job_labels()` emits `vss-skill-eval-gpu` + `openshell-runner` + `openshell` + `gpus-N` — no SKU. Jobs still match `openshell-runner`; the eval step rejects a guest that also carries `l40s`. OpenShell eval legs use an **empty** `platform` / `hardware_profile` and `cohort: openshell`. Skills **not** in the set stay on Brev (`self-hosted`, `vss-eval`, `local_gpu: false`). |
| `.github/workflows/ci.yml` | Two-line touch from the same rebase. Not the eval dispatcher. |

`skills-eval-daily.yml` is unchanged on this branch vs `origin/develop`.

### In-guest Harbor (no Brev hop)

| File | What changed |
|---|---|
| `.github/skill-eval/envs/brev_env.py` | Local GPU pin. Count-only gate for the OpenShell test skill (live `gpu_count`, no SKU/VRAM/`gpu_type`). Strip remote LLM/VLM URLs so guests deploy local NIMs. |
| `.github/skill-eval/run_leg.py` | When the pin is set, skip Brev pool/`flock` selection; Harbor uses this VM’s Docker. |
| `.github/skill-eval/skills_eval_agent.py` | Prompt: do not call `brev`, do not SSH to a coordinator, do not wait on `vss-eval-*`. |
| `.github/skill-eval/direct_agent_progress.py` | In-guest watchdog (idle timeout; cold NIM pulls). |
| `.github/skill-eval/leg_report.py` | Result comment path still owned here for OpenShell legs. |
| `.github/skill-eval/adapters/vss-deploy-test-openshell/generate.py` (+ `__init__.py`) | Live `nvidia-smi` sizing. No SKU in `task.toml` from the planner. Unrecognised GPU → block with the detected name. |

### Docs and tests for that routing

- `.github/skill-eval/AGENTS.md`
- `.github/skill-eval/README.md`
- `.github/skill-eval/tests/test_plan_matrix.py`
- `.github/skill-eval/tests/test_vss_deploy_test_openshell_adapter.py`
- `.github/skill-eval/tests/test_run_leg.py`
- `.github/skill-eval/tests/test_direct_agent_progress.py`
- `.github/skill-eval/tests/test_eval_env_forwarding.py`
- `.github/skill-eval/tests/test_python_runtime_contract.py`
- `.github/skill-eval/tests/test_skills_eval_agent_protocol.py`
- `.github/skill-eval/envs/tests/test_registered_node.py`

### Not runner code, shipped in the same update

- New skill tree: `skills/vss-deploy-test-openshell/**` (`SKILL.md`, `references/`, `scripts/`, `evals/`)
- `skills/README.md`
- `deploy/docker/services/nim/*/hw-A16.env`, `hw-A40.env`, `hw-H200.env` (and some `hw-H200-shared.env`) so guests can size NIMs for those cards

---

## Add another skill to skills-eval

Planner discovery is automatic for **Brev**. OpenShell is an **allowlist**.
A new eval spec without `OPENSHELL_SKILLS` still runs on the coordinator
even when `OPENSHELL_GPU_FLEET=1`.

### 1. Skill + Harbor spec

Under `skills/<category>/<leaf>/` (or flat `skills/<leaf>/`):

- `SKILL.md` — leaf name is unique across `skills/`.
- `evals/<stem>.json` — required keys: `skills`, `resources.platforms`,
  `expects`. Prerequisites live in `expects[].query`, usually the first
  step. Schema: [README.md § Eval spec format](../README.md).
- Optional `evals.json` for static routing questions (not Harbor dispatch).

Brev placement still comes from `resources.platforms` (`A16`, `A40`,
`H200`, `RTXPRO6000BW`, …). For OpenShell **placement**, add:

```json
"openshell": {
  "gpu_count": 1,
  "min_vram_gb_per_gpu": 96,
  "requires_video_codec": false,
  "multi_gpu_capable": false,
  "requires_blackwell": false,
  "supported_hardware_profiles": ["H200", "RTXPRO6000BW"]
}
```

The planner only **consumes** `openshell.gpu_count` (`1` or `2`). The
other keys are type-checked documentation of what you measured against.
Missing or invalid `gpu_count` → `BLOCKED_NO_COMPATIBLE_COHORT` (ubuntu
skip runner), not a silent Brev fallback.

Warehouse / search-class work that needs two cards: `"gpu_count": 2`.

### 2. Adapter

`.github/skill-eval/adapters/<leaf>/generate.py` (flat tree, keyed by
leaf name). Pattern-match
`adapters/vss-manage-video-io-storage/generate.py` (step chain) or
`adapters/vss-build-vision-ai/generate.py` (matrix).

Every `instruction.md` must start with the existing `PREAMBLE` (autonomous
deploy in CI).

If the adapter is missing, skills-eval emits one `missing_adapter` leg
that commits `generate.py` to the contributor branch. Do not rely on
that for OpenShell: the committed adapter must already know empty
`EVAL_PLATFORM` and in-guest sizing.

For an OpenShell-routed skill, follow
`adapters/vss-deploy-test-openshell/generate.py`:

- Do not require every `resources.platforms` key in the dataset. The
  guest has one card; one platform directory is correct, not stale
  (AGENTS.md adapter exception).
- Resolve SKU from live `nvidia-smi` (or `--platform` only for local
  runs). CI must pass `--platform "$EVAL_PLATFORM"` **verbatim** (empty
  means the guest decides).
- Put measured NIM sizing in `hw-<profile>.env`, not in the job labels.
- Exit non-zero naming an unknown GPU; do not guess a nearby SKU.

### 3. Route it onto OpenShell (this is the actual migrate step)

In `plan_matrix.py`:

```python
OPENSHELL_SKILLS = frozenset({
    "vss-deploy-test-openshell",
    "<your-skill-leaf>",
})
```

`_route_skill_on_openshell(skill)` is
`OPENSHELL_GPU_FLEET` **and** membership in that set.

Then:

- Extend `test_plan_matrix.py` so the new skill gets empty `platform`,
  `local_gpu: true`, and `openshell_job_labels(gpu_count)`.
- If `brev_env` still special-cases `EVAL_SKILL ==
  "vss-deploy-test-openshell"` for the count-only gate, either generalize
  that to “any OpenShell-routed skill” or add the new leaf the same way.
  Otherwise the guest still SKU-gates and rejects a valid A16/A40/H200
  mix.
- Adapter unit tests (copy
  `test_vss_deploy_test_openshell_adapter.py`).
- Register new tests in `.github/workflows/ci.yml` if they are not
  already globbed.

`skills-eval.yml` does not need a new job for each skill. `plan` already
fans `(skill, spec)` and `eval` already branches on `matrix.local_gpu`.

### 4. Guest runtime (usually already true)

Confirm, do not reimplement:

- `Assert OpenShell GPU runtime` when `matrix.local_gpu`.
- Pin `SKILL_EVAL_LOCAL_GPU_INSTANCE=$RUNNER_NAME`.
- Secrets on the guest; unset remote LLM/VLM URLs and `BREV_INSTANCE`.
- NIM `hw-A16` / `hw-A40` / `hw-H200` files if the skill deploys those
  cards.

### 5. What not to do

- Do not construct Brev endpoints or `brev exec` from the agent on a
  pinned guest (`brev` is not on guest PATH).
- Do not AND SKU labels (`gpu-h200`, `openshell-h200-active`) onto
  `runs-on` for this routing model. Count + fleet tags only.
- Do not treat `skills-eval-daily.yml` on this branch as the OpenShell
  switch.

---

## Separate workflow that runs on OpenShell

Use a new workflow when the skill should **not** share the PR
`skills-eval.yml` matrix (different cadence, secrets, concurrency, or a
fleet you do not want on every mirror push).

Keep using the same Python: `plan_matrix.py`, `run_leg.py`,
`skills_eval_agent.py`, `envs/brev_env.py`. A second YAML that shells
out to `harbor` + `brev` will reintroduce the hop this work removed.

### Skeleton

```yaml
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

name: Skills Eval OpenShell <skill>

on:
  workflow_dispatch:
    inputs:
      skills:
        description: "Skill leaf name (not *) until the allowlist is proven."
        required: true
        type: string
  # Optional: schedule. Do not add pull-request/<N> until you want PR noise.

permissions:
  contents: write
  pull-requests: write

concurrency:
  group: skills-eval-openshell-<skill>-${{ github.ref }}
  cancel-in-progress: true

env:
  SKILL_EVAL_PYTHON_VERSION: "3.12"
  OPENSHELL_GPU_FLEET: "1"

jobs:
  plan:
    runs-on: ubuntu-24.04
    outputs:
      matrix: ${{ steps.plan.outputs.matrix }}
      has_targets: ${{ steps.plan.outputs.has_targets }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Plan
        env:
          MANUAL_SKILLS_FILTER: ${{ inputs.skills }}
          OPENSHELL_GPU_FLEET: "1"
        run: python3 .github/skill-eval/plan_matrix.py

  eval:
    needs: plan
    if: needs.plan.outputs.has_targets == 'true'
    name: ${{ matrix.name }}
    runs-on: ${{ matrix.runs_on }}
    strategy:
      fail-fast: false
      max-parallel: 30
      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}
    timeout-minutes: 840
    steps:
      # Copy the OpenShell-relevant steps from skills-eval.yml:
      # checkout, Python 3.12 venv + claude-agent-sdk, Assert OpenShell GPU
      # runtime (if: matrix.local_gpu), Load eval env + pin
      # SKILL_EVAL_LOCAL_GPU_INSTANCE, Run skills_eval_agent.py, collect
      # results. Do not source the Brev coordinator .env.
```

### Required wiring

| Piece | Why |
|---|---|
| `OPENSHELL_GPU_FLEET: "1"` on **plan** | Without it, `plan_matrix.py` emits Brev `runs_on` even if eval later sits on an OpenShell VM. |
| Skill in `OPENSHELL_SKILLS` | Fleet flag alone does not route a Brev skill. |
| `runs-on: ${{ matrix.runs_on }}` | Planner already emits fleet + `gpus-N`. Do not hardcode `vss-skill-eval-runner` (coordinator). |
| `matrix.local_gpu` → pin + no Brev | Same guest path as skills-eval. |
| Repository secrets | Guests have no coordinator `.env`. |
| Own `concurrency.group` | Avoid cancelling PR skills-eval legs. |
| Manual summary | No PR on `workflow_dispatch`; agent writes `$GITHUB_STEP_SUMMARY` (AGENTS.md manual sweep). |

### Do not

- Merge or duplicate the OpenShell **daily** overlay from other branches
  onto `skills-eval-daily.yml` on `develop` unless that is an explicit
  follow-up. That file is the Brev nightly full sweep here.
- Set `EVAL_FLEET=openshell` / `OPENSHELL_COUNT_ONLY` unless those env
  names exist in **this** tree’s `plan_matrix.py`. Current routing is
  `OPENSHELL_GPU_FLEET` + `OPENSHELL_SKILLS` + `openshell.gpu_count`.
- Point Harbor at `BREV_INSTANCE` from `~/.eval_env` on the guest.

### Checklist

1. Skill evals + `openshell.gpu_count` + adapter that sizes from the
   guest.
2. Add the leaf to `OPENSHELL_SKILLS` (or the new workflow will plan
   Brev rows that never match OpenShell labels).
3. New YAML: plan on `ubuntu-24.04`, eval on `matrix.runs_on`, copy
   guest secret/pin steps from `skills-eval.yml`.
4. Tests: planner rows, adapter `nvidia-smi` path, `run_leg` pin skips
   pool, agent “do not call brev” prompt.
5. Prove with `workflow_dispatch` on one spec (`base` / one GPU) before
   attaching `pull-request/` or a cron.
