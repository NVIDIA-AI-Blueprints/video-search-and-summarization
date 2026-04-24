# VSS Skills Eval

Evaluate VSS skills (deploy, alerts, vios, video-analytics, video-search, video-summarization, incident-report, report) against a live GPU deployment using [Harbor](https://github.com/laude-institute/harbor).

Evaluation is **fully CI-driven**. [`.github/workflows/skills-eval.yml`](../workflows/skills-eval.yml) fires on every push to a `pull-request/<N>` mirror branch whose diff touches `skills/` or `.github/skill-eval/`, and runs a single claude-agent-sdk session ([`skills_eval_agent.py`](skills_eval_agent.py)) that:

1. Diffs the PR against its base branch and picks out changed skills with an eval spec at `skills/<skill>/eval/<profile>.json`.
2. Generates Harbor datasets per `(skill, profile, platform, mode)` via the adapter at [`adapters/<skill>/generate.py`](adapters/).
3. Acquires a per-instance `flock` on a Brev GPU host, reusing one that matches the target platform or creating one via the fallback chain in [`AGENTS.md`](AGENTS.md).
4. Runs `uvx harbor run` against each dataset, one trial at a time, with the canonical invocation captured in [`AGENTS.md § Harbor invocation`](AGENTS.md).
5. Verifies each trial (containers running, endpoints healthy, trajectory / response / rubric checks — see `verifiers/generic_judge.py`) and scores 0.0–1.0.
6. Posts one Markdown results summary per `(PR, eval-spec)` batch as a PR comment, with trace URLs served by `harbor view`.
7. Leaves instance IDs in `/tmp/brev/started-by-<run_id>.txt`; the workflow wrapper deletes / stops them after a 5-min cooldown.

The whole thing runs inside the 3-hour GitHub Actions job timeout. The `.github/skill-eval/AGENTS.md` file **is** the agent's system prompt — keep it readable.

## Prerequisites

The workflow runs on a self-hosted GitHub Actions runner installed on `vss-skill-validator` (a long-running Brev CPU instance in the NVIDIA org). That host needs:

- **[uv](https://github.com/astral-sh/uv)** — harbor is invoked as `uvx harbor`.
- **[Brev CLI](https://docs.brev.nvidia.com/)** — authenticated via `brev login --auth nvidia` (refresh token lasts ~30 days; a user-level `brev-keepalive.timer` keeps the access token warm).
- **`git`**, **`gh` (GitHub CLI)** — authenticated against the VSS repo.
- **Python 3** — for the adapters.
- **A `.env` at `/home/ubuntu/eval-coordinator/.env`** with the keys below — the workflow step `Load coordinator env` sources this file.

### GPU targets (provisioned on demand, not on the runner)

The runner has no GPU. Eval trials run on per-platform Brev instances the agent provisions (and the workflow tears down):

| Platform | Instance type | Lifecycle |
|---|---|---|
| `l40s` | `massedcompute_L40Sx2` (2× L40S 48 GB) | `brev delete` after trials complete (MC is non-stoppable) |
| `h100` | `dmz.h100x2.pcie` (2× H100 80 GB) | `brev delete` after trials complete |
| `rtx` | `g7e.12xlarge` (RTX PRO 6000) | `brev stop` after trials complete |
| `spark` | BYOH DGX Spark node | no-op — stays online across runs |

Fallback chains and matrix constraints live in [`AGENTS.md § Platform topology`](AGENTS.md).

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
├── adapters/              ← per-skill dataset generators
│   ├── deploy/            ← profile × platform × mode matrix
│   │   └── generate.py
│   ├── vios/              ← single-platform, step-chained
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
├── datasets/<skill>/<profile>/<platform>-<mode>/
│   ├── environment/Dockerfile            (placeholder; Brev env pre-exists)
│   ├── skills/<skill>/                   (copy of the skill the trial uses)
│   ├── solution/solve.sh                 (gold solution, for oracle agent)
│   └── tests/{instruction.md, task.toml, test.sh, <spec>.json}
└── results/
    ├── <run_id>/<date>/<trial>/…         (raw harbor output)
    └── _viewer/<run_id>__<date>/<trial>/ (flattened for `harbor view`)
```

Each generated task contains:

- `instruction.md` — goal + context + success criteria (the agent figures out the how)
- `task.toml` — metadata, environment config, `skills_dir = "/skills"`
- `tests/test.sh` — verifier, writes reward to `/logs/verifier/reward.txt`
- `solution/solve.sh` — gold solution (for oracle agent)
- `skills/<skill>/` — copy of the skill harbor registers with Claude Code
- `environment/Dockerfile` — placeholder (not used — Brev env is pre-existing)

## Eval spec format

Each evaluable skill ships a spec at `skills/<skill>/eval/<profile>.json`. This is the **only file a skill author writes** — the skills-eval agent derives the Harbor adapter, dataset, and dispatch matrix from it.

The **spec is the source of truth** for dispatch. Adapters iterate exactly what `resources.platforms` lists; they never invent platforms or modes a spec did not declare. This keeps PR authors in control of which `(platform, mode)` combos actually run.

Schema:

| Key | Type | Description |
|---|---|---|
| `skills` | `string[]` | Skill names this spec exercises (usually just one). |
| `resources.platforms` | `object` | `{<platform>: {"modes": [...]}}` — the Cartesian matrix the adapter fans out. E.g. `{"L40S": {"modes": ["remote-all"]}}` produces exactly one dataset. Platforms: `H100`, `L40S`, `RTXPRO6000BW`, `DGX-SPARK`. **Required** — the agent files a `missing_platforms_declaration` blocker comment and skips any spec without it. |
| `env` | `string` | Prose describing prerequisites: target platform(s), deployed VSS profile (if any), required env vars, Brev secure-link assumptions, etc. |
| `expects` | `array` | Ordered list — **each entry becomes one Harbor task**, chained to the previous via `requires_previous_passed`. |
| `expects[].query` | `string` | What the agent is asked to do at this step, in plain English. Can embed `{{platform}}`, `{{mode}}`, `{{llm_mode}}`, `{{vlm_mode}}`, `{{repo_root}}` — the adapter substitutes these per-dataset. |
| `expects[].checks` | `string[]` | Assertions the verifier runs after the agent acts. Backtick-wrapped `curl` / `docker` / `grep` commands are extracted and run as shell subprocesses (pass if exit 0). Everything else is handed to a `claude-agent-sdk` judge agent with `Bash` + `Read` + `Grep` tools — so trajectory-style checks ("agent called X exactly once", "response renders a 'Verification Step' section") are first-class; no per-skill probe scripts required. |

### Eval-profile vs deploy-profile (deploy adapter only)

The `deploy` adapter exposes a small `PROFILES` dict that maps **eval-profile names** to the underlying `/deploy` invocation:

```python
PROFILES = {
  "base":       {"description": "..."},                  # key == deploy profile
  "alerts_cv":  {"profile": "alerts", "deploy_mode": "verification"},
  "alerts_vlm": {"profile": "alerts", "deploy_mode": "real-time"},
  "lvs":        {"description": "..."},
  "search":     {"description": "..."},
}
```

An empty or absent `profile` means the dict key *is* the deploy profile (the `base` case). When `profile` is set, the agent is told to invoke `/deploy -p <profile>`; the optional `deploy_mode` becomes `-m <mode>`. This is how one skill profile (`alerts`) produces multiple eval variants (`alerts_cv`, `alerts_vlm`) with distinct spec files and distinct container-check sets while still deploying a shared compose stack.

### Worked example — `skills/vios/eval/base_profile_ops.json`

Three-step thread against a deployed VSS base: upload video → snapshot URL → clip URL. Produces 3 chained tasks on the targeted platform.

```json
{
  "skills": ["vios"],
  "resources": {"platforms": {"L40S": {"modes": ["remote-all"]}}},
  "env": "A **full-remote deployed VSS base profile** (deploy mode = `remote-all` — LLM and VLM both via remote launchpad endpoints, no local NIMs). Run on ONE platform only — the vios skill exercises VIOS / VST which is GPU-independent, so there's no benefit to fanning out. Required: VST reachable at http://localhost:30888/vst/api/v1 AND the Brev secure-link env vars set (BREV_ENV_ID from /etc/environment, BREV_LINK_PREFIX defaulting to 77770). Without BREV_ENV_ID the returned media URLs will be raw http://localhost:... and the Brev-link checks will fail.",
  "expects": [
    {
      "query": "Upload the sample warehouse video to VIOS with timestamp 2025-01-01T00:00:00.000Z.",
      "checks": [
        "The upload API call (PUT /vst/api/v1/storage/file/<filename>?timestamp=...) returns HTTP 2xx",
        "The response JSON contains both a sensorId and a streamId (non-empty UUIDs)",
        "curl -sf http://localhost:30888/vst/api/v1/sensor/list returns a JSON array containing a sensor whose name matches the uploaded video's filename stem"
      ]
    },
    {
      "query": "Extract a snapshot from 5 seconds into the uploaded video and return a shareable URL.",
      "checks": [
        "GET /vst/api/v1/replay/stream/<streamId>/picture/url?startTime=2025-01-01T00:00:05.000Z returns a JSON object with a non-empty imageUrl field",
        "The returned imageUrl matches the Brev secure-link pattern: https://<BREV_LINK_PREFIX>-<BREV_ENV_ID>.brevlab.com/... (NOT http://localhost:... and NOT http://<internal-ip>:...)",
        "curl -sfI <imageUrl> returns HTTP 200"
      ]
    },
    {
      "query": "Extract a video clip from 3 to 5 seconds (mp4 container) from the uploaded video and return a shareable URL.",
      "checks": [
        "GET /vst/api/v1/storage/file/<streamId>/url?startTime=2025-01-01T00:00:03.000Z&endTime=2025-01-01T00:00:05.000Z&container=mp4&disableAudio=true returns a JSON object with a non-empty videoUrl field",
        "curl -sfI <videoUrl> returns HTTP 200",
        "The response Content-Length is greater than 10000 bytes"
      ]
    }
  ]
}
```

Source: [`skills/vios/eval/base_profile_ops.json`](../../skills/vios/eval/base_profile_ops.json)

What the agent derives from this spec:
- `env` says **"full-remote deployed VSS base profile"** → inject a `deploy` task with `mode=remote-all` + `profile=base` ahead of the vios tasks.
- `resources.platforms` is `{L40S: [remote-all]}` → one dataset, one platform. No fan-out.
- `expects[]` has 3 entries → 3 chained vios tasks, each gated on `requires_previous_passed`.
- `checks` use a mix of curl probes and trajectory-style assertions — the generic judge routes each to the right evaluator.

## Running a trial by hand

For debugging an adapter or verifier locally, outside CI:

```bash
set -a && source /home/ubuntu/eval-coordinator/.env && set +a

# 1. Generate the dataset for one spec.
python3 .github/skill-eval/adapters/vios/generate.py \
  --output-dir /tmp/skill-eval/datasets/vios \
  --skill-dir skills/vios \
  --platform L40S

# 2. Make sure you have a Brev instance for the target platform
#    (or let the skills-eval agent manage it).
export BREV_INSTANCE=vss-eval-l40s

# 3. Run one trial. The flags here mirror the canonical invocation in
#    AGENTS.md § Harbor invocation — don't improvise.
export PYTHONPATH="$(pwd)/.github/skill-eval:${PYTHONPATH:-}"

uvx harbor run \
  --environment-import-path "envs.brev_env:BrevEnvironment" \
  -p /tmp/skill-eval/datasets/vios/base_profile_ops \
  --include-task-name "l40s-remote-all" \
  -a claude-code \
  --model "$ANTHROPIC_MODEL" \
  --ak api_base="$ANTHROPIC_BASE_URL/v1" \
  --ae CLAUDE_CODE_DISABLE_THINKING=1 \
  --max-retries 0 -n 1 --yes \
  -o /tmp/skill-eval/results/manual-$(date +%Y%m%d-%H%M%S)
```

`CLAUDE_CODE_DISABLE_THINKING=1` is required when routing through the NVIDIA Anthropic proxy — claude-code ≥ 2.1.x otherwise emits a `context_management` field the proxy rejects with HTTP 400.

### Inspect a result

```
/tmp/skill-eval/results/<run_id>/<date>/<trial>/
├── config.json
├── trial.log
├── verifier/
│   ├── reward.txt        ← 0.0–1.0
│   └── test-stdout.txt   ← verifier output
└── agent/
    └── claude-code.txt   ← agent trace
```

To view in the browser, flatten into the viewer dir:

```bash
cd /tmp/skill-eval/results
mv "<run_id>/<date>" "_viewer/<run_id>__<date>"
rmdir "<run_id>" 2>/dev/null || true
```

Then open `https://harbor-<BREV_ENV_ID>.brevlab.com/jobs/<run_id>__<date>`.

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

**Instance creation fails.** Some Brev providers have capacity issues. Harbor's fallback chain (see [`AGENTS.md § Platform topology`](AGENTS.md)) cycles through alternatives. If all are exhausted, the agent posts a `csp_unavailable` blocker.

**Brev auth expired mid-run.** The CI run emits `BLOCKED: brev auth expired`. The `brev-keepalive.timer` systemd user unit keeps the access token warm, but only an interactive `brev login --auth nvidia` can refresh a fully-expired refresh token.

**Agent deployment fails with "pull access denied".** `NGC_CLI_API_KEY` missing or invalid — the agent needs it to pull VSS NIM containers from `nvcr.io`.

**Cancelled run leaves orphan Brev instances.** A cancelled CI job never gets to the cooldown teardown step. Clean up by listing owned instances in `/tmp/brev/started-by-<run_id>.txt` on the runner host and `brev delete` them manually.
