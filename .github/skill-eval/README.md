# VSS Skills Eval

Evaluate VSS skills (vss-deploy-profile, vss-deploy-dense-captioning, vss-manage-alerts, vss-manage-video-io-storage, vss-query-analytics, vss-search-archive, vss-summarize-video, vss-ask-video, vss-generate-video-report) against a live GPU deployment using [Harbor](https://github.com/laude-institute/harbor).

Evaluation is **fully CI-driven**. [`.github/workflows/skills-eval.yml`](../workflows/skills-eval.yml) fires on every push to a `pull-request/<N>` mirror branch whose diff touches `skills/` or `.github/skill-eval/`, and runs a single claude-agent-sdk session ([`skills_eval_agent.py`](skills_eval_agent.py)) that:

1. Diffs the PR against its base branch and picks out changed skills with an eval spec at `skills/<skill>/evals/<name>.json` (legacy `skills/<skill>/eval/<name>.json` still accepted).
2. Generates Harbor datasets per `(skill, profile, platform, mode)` via the adapter at [`adapters/<skill>/generate.py`](adapters/).
3. Selects an operator-managed `vss-eval-*` pool member matching the target platform, per the fleet-selection algorithm in [`AGENTS.md`](AGENTS.md) § 5a. The harness does **not** auto-provision — if no pool member matches, the run blocks until one appears (or times out).
4. Calls [`run_leg.py`](run_leg.py), which acquires the per-instance `flock`, holds it while every Harbor subprocess for this `(spec, platform)` runs, and invokes `uvx harbor run` with the canonical flags from [`AGENTS.md § Harbor invocation`](AGENTS.md).
5. Verifies each trial (containers running, endpoints healthy, trajectory / response / rubric checks — see `verifiers/generic_judge.py`) and scores 0.0–1.0.
6. Posts one Markdown results summary per `(PR, eval-spec)` batch as a PR comment, with trace URLs served by `harbor view`.

The whole thing runs inside the 8-hour GitHub Actions job timeout. The `.github/skill-eval/AGENTS.md` file **is** the agent's system prompt — keep it readable.

## Prerequisites

The workflow runs on a self-hosted GitHub Actions runner installed on `vss-skill-validator` (a long-running Brev CPU instance in the NVIDIA org). That host needs:

- **[uv](https://github.com/astral-sh/uv)** — harbor is invoked as `uvx harbor`.
- **[Brev CLI](https://docs.brev.nvidia.com/)** — authenticated via `brev login --auth nvidia` (refresh token lasts ~30 days; a user-level `brev-keepalive.timer` keeps the access token warm).
- **`git`**, **`gh` (GitHub CLI)** — authenticated against the VSS repo.
- **Python 3** — for the adapters.
- **A `.env` at `/home/ubuntu/eval-coordinator/.env`** with the keys below — the workflow step `Load coordinator env` sources this file.

### GPU targets (operator-managed `vss-eval-*` pool)

The runner has no GPU. Eval trials run on a long-lived pool of `vss-eval-*` Brev instances that the **operator** provisions ahead of time with `brev create`; the skill-eval agent only locks, drives, and resets them — never creates, stops, or deletes pool members. Default pool today:

| Platform | Pool member(s) | Instance type |
|---|---|---|
| `l40s` | `vss-eval-l40s`, `vss-eval-l40s-1g`, `vss-eval-l40s-2` | `massedcompute_L40S` / `massedcompute_L40Sx2` |
| `h100` | `vss-eval-h100` (when needed) | launchpad `dmz.h100x2.pcie` preferred |
| `rtx` | `vss-eval-rtx-1g`, `vss-eval-rtx-1g-2`, `vss-eval-rtx-2g` | AWS `g7e.4xlarge` / `g7e.12xlarge` (RTX PRO Server 6000) |
| `spark` | BYOH DGX Spark node registered via `brev register` | n/a |

Per-CI-run hygiene is the trial's own responsibility: each spec's first agent turn invokes `/vss-deploy-profile` (or a standalone deploy runbook) to bring up whatever it needs, including `docker compose down` of any prior leftover containers on the box. The harness no longer pre-deploys profiles or maintains an `active-deploy.txt` marker — that machinery was removed in favour of putting deploy steps inside the trial trajectory where they're visible in the reward, judge, and `claude-code.txt`. Fleet-selection scoring + the wait-for-pool path on exhaustion live in [`AGENTS.md § Platform topology`](AGENTS.md).

### API keys (`/home/ubuntu/eval-coordinator/.env` on the runner)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude Code authentication (NVIDIA inference API key works) |
| `ANTHROPIC_BASE_URL` | Custom API base (e.g. `https://inference-api.nvidia.com`) |
| `ANTHROPIC_MODEL` | Model ID (e.g. `aws/anthropic/bedrock-claude-sonnet-4-6`) |
| `NGC_CLI_API_KEY` | Pull VSS NIM containers from `nvcr.io` |
| `LLM_REMOTE_URL` / `LLM_REMOTE_MODEL` | Remote-LLM endpoint used by `remote-*` deploy modes |
| `VLM_REMOTE_URL` / `VLM_REMOTE_MODEL` | Remote-VLM endpoint used by `remote-*` deploy modes |
| `HF_TOKEN` | Required by the Edge 4B vLLM on SPARK / Thor `shared` mode |
| `GITHUB_TOKEN` | Issued to `gh pr comment` when the agent posts results |

## Layout

```
.github/skill-eval/
├── README.md              ← you are here
├── AGENTS.md              ← skills-eval agent's system prompt
├── skills_eval_agent.py   ← the CI entrypoint (spawns the agent)
├── run_leg.py             ← structural per-box lock + Harbor launcher
├── adapters/              ← per-skill dataset generators
│   ├── vss-deploy-profile/            ← profile × platform × mode matrix
│   │   └── generate.py
│   ├── vss-deploy-dense-captioning/   ← RT-VLM standalone/profile API checks
│   │   └── generate.py
│   ├── vss-manage-video-io-storage/   ← single-platform, step-chained
│   │   └── generate.py
│   └── <skill>/           ← the agent creates one if missing
│       └── generate.py
├── envs/
│   └── brev_env.py        ← Harbor environment for pre-existing Brev instances
└── verifiers/
    └── generic_judge.py   ← routes checks to shell / trajectory /
                             response / rubric evaluators
```

Runtime state (not checked in):

```
/tmp/skill-eval/
├── datasets/<leg-slug>/<run_id>/…        (this leg's dataset; slug = <skill>__<spec_stem>__<platform>)
│   ├── environment/Dockerfile            (placeholder; Brev env pre-exists)
│   ├── skills/<skill>/                   (copy of the skill the trial uses)
│   ├── solution/solve.sh                 (gold solution, for oracle agent)
│   └── tests/{instruction.md, task.toml, test.sh, <spec>.json}
└── results/
    ├── <leg-slug>/<run_id>/<date>/<trial>/…          (raw harbor output; collector tars this)
    └── _viewer/<leg-slug>__<run_id>__<date>/<trial>/ (cp -a copy, flattened for `harbor view`)
```

Each generated task contains:

- `instruction.md` — goal + context + success criteria (the agent figures out the how)
- `task.toml` — metadata, environment config, `skills_dir = "/skills"`
- `tests/test.sh` — verifier, writes reward to `/logs/verifier/reward.txt`
- `solution/solve.sh` — gold solution (for oracle agent)
- `skills/<skill>/` — copy of the skill harbor registers with Claude Code
- `environment/Dockerfile` — placeholder (not used — Brev env is pre-existing)

## Eval spec format

Each evaluable skill ships a spec at `skills/<skill>/evals/<name>.json`; legacy `skills/<skill>/eval/<name>.json` (singular) specs remain supported for unmigrated skills. This is the **only file a skill author writes** — the skills-eval agent derives the Harbor adapter, dataset, and dispatch matrix from it.

The **spec is the source of truth** for dispatch. Adapters iterate exactly what `resources.platforms` lists; they never invent platforms or modes a spec did not declare. This keeps PR authors in control of which `(platform, mode)` combos actually run.

Schema:

| Key | Type | Description |
|---|---|---|
| `skills` | `string[]` | Skill names this spec exercises (usually just one). |
| `resources.platforms` | `object` | `{<platform>: {"modes": [...]}}` — the Cartesian matrix the adapter fans out. E.g. `{"L40S": {"modes": ["remote-all"]}}` produces exactly one dataset. Platforms: `H100`, `L40S`, `RTXPRO6000BW`, `DGX-SPARK`. **Required** — the agent files a `missing_platforms_declaration` blocker comment and skips any spec without it. |
| `expects` | `array` | Ordered list — **each entry becomes one Harbor task**, chained to the previous via `requires_previous_passed`. There is no separate `env` field: every prerequisite (deployed profile, required env vars, ports, sample-data ingest, platform notes) goes **inside the relevant `expects[].query`** — usually the first/setup query, often a `/vss-deploy-profile …` deploy step. |
| `expects[].query` | `string` | What the agent is asked to do at this step, in plain English — including any prerequisites/environment the step needs. Can embed `{{platform}}`, `{{mode}}`, `{{llm_mode}}`, `{{vlm_mode}}`, `{{repo_root}}` — the adapter substitutes these per-dataset. |
| `expects[].checks` | `string[]` | Assertions the verifier runs after the agent acts. Backtick-wrapped `curl` / `docker` / `grep` commands are extracted and run as shell subprocesses (pass if exit 0). Everything else is handed to a `claude-agent-sdk` judge agent with `Bash` + `Read` + `Grep` tools — so trajectory-style checks ("agent called X exactly once", "response renders a 'Verification Step' section") are first-class; no per-skill probe scripts required. |

### Eval-profile vs deploy-profile (vss-deploy-profile adapter only)

The `vss-deploy-profile` adapter exposes a small `PROFILES` dict that maps **eval-profile names** to the underlying `/vss-deploy-profile` invocation:

```python
PROFILES = {
  "base":       {"description": "..."},                  # key == deploy profile
  "alerts_cv":  {"profile": "alerts", "deploy_mode": "verification"},
  "alerts_vlm": {"profile": "alerts", "deploy_mode": "real-time"},
  "lvs":        {"description": "..."},
  "search":     {"description": "..."},
}
```

An empty or absent `profile` means the dict key *is* the deploy profile (the `base` case). When `profile` is set, the agent is told to invoke `/vss-deploy-profile -p <profile>`; the optional `deploy_mode` becomes `-m <mode>`. This is how one skill profile (`alerts`) produces multiple eval variants (`alerts_cv`, `alerts_vlm`) with distinct spec files and distinct container-check sets while still deploying a shared compose stack.

### Worked example — `skills/vss-manage-video-io-storage/evals/vios_ops.json`

13-query thread against VIOS / VST: upload, snapshot, clip, sensor info, recorder status, timelines, etc. There is no `/vss-deploy-profile` prerequisite — the **first query** tells the agent to stand VIOS up standalone via the skill's bundled `references/deploy-vios-service.md` runbook, and folds the environment prerequisites (required env vars, ports) into that same query. Produces 13 chained tasks on the targeted platform.

```json
{
  "skills": ["vss-manage-video-io-storage"],
  "resources": {"platforms": {"L40S": {"gpu_count": 1}}},
  "expects": [
    {
      "query": "Upload the sample warehouse video to VIOS with timestamp 2025-01-01T00:00:00.000Z.\n\n**Environment & prerequisites:** No VSS profile is pre-deployed. Probe http://localhost:30888/vst/api/v1/sensor/version first; if it fails, stand VIOS up standalone via this skill's bundled references/deploy-vios-service.md runbook (pre-authorized via SKILL.md § Pre-authorized autonomous mode). Required env vars: NGC_CLI_API_KEY, HOST_IP, VSS_DATA_DIR, VSS_APPS_DIR, plus the Brev secure-link env vars.",
      "checks": [
        "The upload PUT to /vst/api/v1/storage/file/<filename>?timestamp=... either returns HTTP 2xx OR returns the VST sensor-cap error",
        "curl -sf http://localhost:30888/vst/api/v1/sensor/list returns a JSON array containing a sensor whose name matches the uploaded video's filename stem"
      ]
    },
    // ... 12 more entries ...
  ]
}
```

Source: [`skills/vss-manage-video-io-storage/evals/vios_ops.json`](../../skills/vss-manage-video-io-storage/evals/vios_ops.json)

What the agent derives from this spec:
- `profile` is absent → **no `/vss-deploy-profile` prerequisite is injected.** The trial runs on a bare Brev instance and the agent uses the skill's bundled deploy contract (documents direct-routing and SDRC-routed modes — either acceptable) when it finds VIOS missing.
- `resources.platforms` is `{L40S: {gpu_count: 1}}` → one dataset, one platform. No fan-out.
- `expects[]` has 13 entries → 13 chained `vss-manage-video-io-storage` tasks, each gated on `requires_previous_passed`.
- `checks` use a mix of curl probes and trajectory-style assertions — the generic judge routes each to the right evaluator.

## Running a trial by hand

For debugging an adapter or verifier locally, outside CI:

```bash
set -a && source /home/ubuntu/eval-coordinator/.env && set +a

# 1. Generate the dataset for one spec.
python3 .github/skill-eval/adapters/vss-manage-video-io-storage/generate.py \
  --output-dir /tmp/skill-eval/datasets/vss-manage-video-io-storage \
  --skill-dir skills/vss-manage-video-io-storage \
  --platform L40S

# 2. Make sure you have a Brev instance for the target platform
#    (or let the skills-eval agent select one).
#
# ⚠️ On a spec's first trial the env provider WIPES the box's docker runtime
#    (all containers, user-defined networks, and volumes; images are kept).
#    NEVER point a manual run at a box a CI run currently holds — it will
#    `docker rm -f` that run's deployment mid-trial. Use run_leg.py so the
#    same per-box lock contract applies to manual runs.
INSTANCE_NAME=vss-eval-l40s

# 3. Run one trial. run_leg.py discovers single-step vs multi-step task
#    layouts, holds /tmp/brev/$INSTANCE_NAME.lock, and invokes Harbor.
export PYTHONPATH="$(pwd)/.github/skill-eval:${PYTHONPATH:-}"

python3 .github/skill-eval/run_leg.py \
  --instance "$INSTANCE_NAME" \
  --dataset-root /tmp/skill-eval/datasets/vss-manage-video-io-storage \
  --results-root /tmp/skill-eval/results/manual-$(date +%Y%m%d-%H%M%S) \
  --scratch /tmp/skill-eval/manual \
  --spec-stem vios_ops \
  --platform L40S
```

`CLAUDE_CODE_DISABLE_THINKING=1` is required when routing through the NVIDIA Anthropic proxy — claude-code ≥ 2.1.x otherwise emits a `context_management` field the proxy rejects with HTTP 400.

### Inspect a result

```
/tmp/skill-eval/results/<leg-slug>/<run_id>/<date>/<trial>/
├── config.json
├── trial.log
├── verifier/
│   ├── reward.txt        ← 0.0–1.0
│   └── test-stdout.txt   ← verifier output
└── agent/
    └── claude-code.txt   ← agent trace
```

To view in the browser, **copy** (not move — the workflow's collector
still tars the leg's results root after the agent) into the viewer dir,
flattened with the leg slug:

```bash
VIEWER_JOB="/tmp/skill-eval/results/_viewer/<leg-slug>__<run_id>__<date>"
mkdir -p "$VIEWER_JOB"
cp -a "<leg-slug>/<run_id>/<date>/." "$VIEWER_JOB/"   # contents into a pre-made dir — idempotent
```

Then open `https://harbor-<BREV_ENV_ID>.brevlab.com/jobs/<leg-slug>__<run_id>__<date>`.

`harbor view` runs persistently on the CI runner host. If it's down:

```bash
nohup uvx harbor view /tmp/skill-eval/results/_viewer --jobs \
  --host 0.0.0.0 --port 8080 > /tmp/harbor-view.log 2>&1 &
disown
```

## Troubleshooting

**CI didn't fire after a push.** The workflow only triggers on pushes to `pull-request/<N>` mirror branches, created by copy-pr-bot after a maintainer comments `/ok to test <sha>` on the source PR. Check that the comment was posted on the correct head SHA.

**"missing_platforms_declaration" blocker on a spec.** The spec has no `resources.platforms`. Add one — see the worked example above.

**Agent returns "Not logged in."** `ANTHROPIC_API_KEY` is not set in `/home/ubuntu/eval-coordinator/.env` or is invalid. If using a proxy, also confirm `ANTHROPIC_BASE_URL` and `ANTHROPIC_MODEL`.

**`AddTestsDirError` / `DownloadVerifierDirError`.** File upload/download to the Brev instance failed. Check `brev exec <instance> "echo ok"` works manually. Clear `/tests /logs /skills` on the instance and retry.

**Pool exhausted for `<platform>`.** No `vss-eval-*` pool member matches the trial's `gpu_type` after the 21000s wait window (`brev ls` polled every 5 min). The agent emits `BLOCKED: pool exhausted for <platform>` and exits. Provisioning new pool members is the operator's job — `brev create vss-eval-<name>` with the matching instance type, then bring it online; the next CI run picks it up automatically via the `^vss-eval-*` fleet scan.

**Brev auth expired mid-run.** The CI run emits `BLOCKED: brev auth expired`. The `brev-keepalive.timer` systemd user unit keeps the access token warm, but only an interactive `brev login --auth nvidia` can refresh a fully-expired refresh token.

**Agent deployment fails with "pull access denied".** `NGC_CLI_API_KEY` missing or invalid — the agent needs it to pull VSS NIM containers from `nvcr.io`.

**Orphan `harbor-*` Brev instances.** The harness no longer auto-provisions — every trial must use a `vss-eval-*` pool member. If you see `harbor-*` instances in `brev ls`, they're stragglers from before this change (or from someone running `uvx harbor` manually without `BREV_INSTANCE` set). Clean them up with `brev delete <name>`.
