# VSS Harbor Evaluation

Evaluate VSS skills (deploy, alerts, vios, incident-report, video-analytics, video-search, video-summarization) against a live GPU deployment using [Harbor](https://github.com/laude-institute/harbor).

The framework is run by a **long-running coordinator agent** ([`AGENTS.md`](AGENTS.md) is the playbook). You launch it once with `claude` + `/loop AGENTS.md`, and it:

1. Watches the `pull-request/<N>` mirror branches for CPR-vetted contributor PRs
2. Detects which skill specs changed, generates Harbor datasets per spec via the adapters
3. Provisions / reuses Brev GPU instances per platform (L40S, H100, RTX 6000 Pro, SPARK)
4. Fans out eval tasks to per-platform subagents, each of which invokes Claude Code against a goal-oriented instruction
5. Verifies the outcome (containers running, endpoints healthy, trajectory checks, etc.) and scores each trial 0.0–1.0
6. Posts a Markdown results summary as a PR comment, with links to traces served by `harbor view`
7. Stops / deletes Brev instances when queues drain

You don't run skills manually — you let the coordinator pick them up from PRs.

## Prerequisites

Run these on the **coordinator host** (a long-running Brev CPU instance; `vss-skill-validator` in the NVIDIA org serves this role):

- **[uv](https://github.com/astral-sh/uv)** (Python package manager) — harbor is invoked via `uvx harbor`
- **[Brev CLI](https://docs.brev.nvidia.com/)** — authenticated via `brev login --auth nvidia` (refresh token lasts ~30 days; a user-level systemd timer `brev-keepalive.timer` runs `brev ls` every 15 min to keep the access token warm)
- **[Claude Code](https://docs.claude.com/claude-code)** — the coordinator runs as a `claude` session using `/loop AGENTS.md`
- **`git`**, **`gh` (GitHub CLI)** — authenticated against the VSS repo fork
- **Python 3** — for the dataset generators

### GPU requirements (eval targets, not the coordinator host)

The coordinator dispatches across four first-class platform subagents. You don't need all four running at all times — the coordinator spins them up on demand:

| Subagent | Instance type | Lifecycle |
|---|---|---|
| `l40s` | `l40s-48gb.2x` (2× L40S 48 GB) | `brev start` on demand, `brev stop` after queue drains |
| `h100` | `dmz.h100x2.pcie` (2× H100 80 GB) | non-stoppable; `brev delete` after queue drains |
| `rtx` | `g7e.12xlarge` (RTX PRO Server 6000) | `brev start` / `brev stop` like L40S |
| `spark` | BYOH DGX Spark node | never stopped; stays online across runs |

Each instance needs Ubuntu/Debian, network egress to `nvcr.io`, and passwordless `sudo` for the default user (standard on Brev).

### API keys

| Variable | Purpose | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude Code authentication (NVIDIA inference API key works) | Yes |
| `ANTHROPIC_BASE_URL` | Custom API base (e.g. `https://inference-api.nvidia.com`) | If using a proxy |
| `ANTHROPIC_MODEL` | Model ID (e.g. `aws/anthropic/bedrock-claude-sonnet-4-6`) | If using a proxy |
| `NGC_CLI_API_KEY` | Pull VSS NIM containers from `nvcr.io` | For deploy skill |
| `BREV_INSTANCE` | Name of pre-existing Brev instance | Optional (auto-creates `vss-eval-gpu` if unset) |
| `BREV_INSTANCE_TYPE` | Instance type when auto-creating | Optional (default `l40s-48gb.1x`) |

Create a `.env` file at the repo root (see [`.env.example`](.env.example)):

```bash
cp tools/eval/harbor/.env.example .env
# edit .env with your keys
```

The eval script auto-loads `.env` at the repo root.

## Quick start

From a fresh clone on the coordinator host:

```bash
# 1. Clone the repo (feat/skills until the skills work merges to develop)
git clone --branch feat/skills https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization

# 2. Log in to Brev (needs to be repeated every ~30 days when the refresh token expires)
brev login --auth nvidia

# 3. Create your .env — place it at the coordinator's eval-coordinator workspace,
#    not in the VSS repo (the coordinator sources it on every wake).
cp tools/eval/harbor/.env.example ~/eval-coordinator/.env
$EDITOR ~/eval-coordinator/.env

# 4. Launch the coordinator agent inside a `claude` session:
claude --dangerously-skip-permissions
# then at the prompt:
/loop AGENTS.md
```

`/loop AGENTS.md` starts the self-paced coordinator loop described in [`AGENTS.md`](AGENTS.md). Each wake-up it:

- Sources `~/eval-coordinator/.env` (API keys, remote endpoints, git identity)
- Lists `pull-request/<N>` mirror branches and diffs any with new SHAs against their base
- For each changed skill with an eval spec, regenerates the Harbor dataset via the adapter and enqueues tasks in the per-platform queue JSON under `/tmp/subagents/`
- Spawns a subagent per platform with pending tasks (using the `Agent` tool in background mode)
- Monitors results, posts PR comments, raises adapter PRs as needed
- Tears down Brev instances when queues drain
- Sleeps 25 min when idle, wakes immediately on subagent completion events

You interact with the coordinator by pushing commits to PRs, adding `/ok to test` for the copy-pr-bot to re-mirror, and reading the comments it posts. It does not need imperative CLI commands.

## Architecture

```
Coordinator host                     Per-platform Brev instance
(vss-skill-validator)                (vss-eval-l40s, -h100, -rtx, spark)
────────────────────                 ──────────────────────────────────

claude /loop AGENTS.md               (provisioned on demand)
  │
  ├── Sources .env                   Claude Code + NIMs/Docker stack
  ├── gh api branches                Per-trial: /tests /skills /logs
  │     pull-request/*
  ├── python3 adapters/*/generate.py
  │     → datasets/<skill>/<profile>/<platform>/<mode>/
  │
  ├── Agent tool (background)
  │     └── platform subagent
  │           └── uvx harbor run
  │                 └── BrevEnvironment (tools/eval/harbor/envs/brev_env.py)
  │                       └── brev exec → Claude Code on the GPU host
  │                             └── /<skill> executes the task
  │
  ├── Watches results/<run_id>/
  │     → moves to results/_viewer/<run_id>__<date>/
  │     → posts PR comment with trace URLs
  │
  └── harbor view (persistent, port 8080, Cloudflare-tunnelled)
        → https://harbor-<BREV_ENV_ID>.brevlab.com/jobs/<run_id>__<date>
```

State lives on the coordinator host in `/tmp/subagents/`:

- `{l40s,h100,rtx,spark}.json` — per-platform queue + results
- `_prs_seen.json` — PR → (head_sha, batches, draft comment) ledger
- `<platform>.pid` — subagent liveness markers
- `_alerts.json` — blocker alerts the coordinator can't self-resolve (e.g. expired Brev auth)

`tools/eval/harbor/envs/brev_env.py` is the Harbor environment provider. It connects to a pre-existing Brev instance (does not create one per trial) and transfers files via `tar` over `brev exec` (faster and more reliable than `brev copy`).

## Layout

```
tools/eval/harbor/
├── README.md              ← you are here
├── AGENTS.md              ← coordinator playbook (source of truth for /loop)
├── .env.example           ← template for the coordinator's .env
├── adapters/              ← skill-specific dataset generators (human-maintained)
│   ├── deploy/            ← profile × platform × mode matrix
│   │   └── generate.py
│   ├── vios/              ← single-task-per-platform, step-chained
│   │   └── generate.py
│   └── <skill>/           ← coordinator may raise an adapter PR when a new
│       └── generate.py      eval spec lands for a skill that lacks one
├── envs/
│   └── brev_env.py        ← Harbor environment for pre-existing Brev instances
├── verifiers/
│   └── generic_judge.py   ← routes checks to shell / trajectory /
│                            response / rubric evaluators
├── datasets/              ← generated per spec, gitignored
│   └── <skill>/<profile>/<platform>-<mode>/
│       ├── environment/Dockerfile  (placeholder; Brev env pre-exists)
│       ├── skills/<skill>/         (uploaded into the trial)
│       ├── solution/solve.sh       (gold solution, for oracle agent)
│       └── tests/{instruction.md, task.toml, test.sh, <spec>.json}
└── results/               ← harbor run outputs, gitignored
    ├── <run_id>/<date>/…            (raw harbor output)
    └── _viewer/<run_id>__<date>/…   (flattened for `harbor view`)
```

Each generated task contains:

- `instruction.md` — goal + context + success criteria (the agent figures out the how)
- `task.toml` — metadata, environment config, `skills_dir = "/skills"`
- `tests/test.sh` — verifier, writes reward to `/logs/verifier/reward.txt`
- `solution/solve.sh` — gold solution (for oracle agent)
- `skills/<skill>/` — copy of the skill that harbor registers with Claude Code
- `environment/Dockerfile` — placeholder (not used — Brev env is pre-existing)

## Eval spec format

Each evaluable skill ships a spec at
`skills/<skill>/eval/<profile>.json`. This is the **only file a skill
author writes** — the coordinator agent (see [`AGENTS.md`](AGENTS.md))
derives the Harbor adapter, dataset, and queue entries from it.

The **spec is the source of truth** for dispatch. Adapters iterate
exactly what `resources.platforms` lists; they never invent platforms
or modes a spec did not declare. This keeps PR authors in control of
which `(platform, mode)` combos actually run.

Schema:

| Key | Type | Description |
|---|---|---|
| `skills` | `string[]` | Skill names this spec exercises (usually just one). |
| `resources.platforms` | `object` | `{<platform>: {"modes": [...]}}` — the Cartesian matrix the adapter will fan out. E.g. `{"L40S": {"modes": ["remote-all"]}}` produces exactly one dataset. Platforms: `H100`, `L40S`, `RTXPRO6000BW`, `DGX-SPARK`. Omitted → adapter falls back to its internal defaults (back-compat only; new specs should declare explicitly). |
| `env` | `string` | Prose describing prerequisites: target platform(s), deployed VSS profile (if any), required env vars, Brev secure-link assumptions, etc. The coordinator parses this for prerequisite deploy injection and platform intent. |
| `expects` | `array` | Ordered list — **each entry becomes one Harbor task in the subagent queue**, chained to the previous via `requires_previous_passed`. |
| `expects[].query` | `string` | What the agent is asked to do at this step, in plain English. Can embed `{{platform}}`, `{{mode}}`, `{{llm_mode}}`, `{{vlm_mode}}`, `{{repo_root}}` — the adapter substitutes these per-dataset. |
| `expects[].checks` | `string[]` | Assertions the verifier runs after the agent acts. Backtick-wrapped `curl`/`docker`/`grep`/etc. commands are extracted and run as shell subprocesses (pass if exit 0). Everything else is handed to a `claude-agent-sdk` judge agent with `Bash` + `Read` + `Grep` tools — so trajectory-style checks ("agent called X exactly once", "response renders a 'Verification Step' section") are first-class; no per-skill probe scripts required. |

### Eval-profile vs deploy-profile (deploy adapter only)

The `deploy` adapter also exposes a small `PROFILES` dict that maps
**eval-profile names** to the underlying `/deploy` invocation:

```python
PROFILES = {
  "base":       {"description": "..."},                  # key == deploy profile
  "alerts_cv":  {"profile": "alerts", "deploy_mode": "verification"},
  "alerts_vlm": {"profile": "alerts", "deploy_mode": "real-time"},
  "lvs":        {"description": "..."},
  "search":     {"description": "..."},
}
```

An empty or absent `profile` means the dict key *is* the deploy profile
(the `base` case). When `profile` is set, the agent is told to invoke
`/deploy -p <profile>`; the optional `deploy_mode` becomes `-m <mode>`.
This is how one skill profile (`alerts`) produces multiple eval variants
(`alerts_cv`, `alerts_vlm`) with distinct spec files and distinct
container-check sets while still deploying a shared compose stack.

### Worked example — `skills/vios/eval/base_profile_ops.json`

Three-step thread against a deployed VSS base: upload video → snapshot URL → clip URL. Produces 3 queued tasks per targeted platform.

```json
{
  "skills": ["vios"],
  "env": "A **full-remote deployed VSS base profile** (deploy mode = `remote-all` — LLM and VLM both via remote launchpad endpoints, no local NIMs). Run on ONE platform only — the vios skill exercises VIOS / VST which is GPU-independent, so there's no benefit to fanning out. Pick the cheapest available host (L40S recommended). Required: VST reachable at http://localhost:30888/vst/api/v1 AND the Brev secure-link env vars set (BREV_ENV_ID from /etc/environment, BREV_LINK_PREFIX defaulting to 77770 per launchable convention — see skills/deploy/references/brev.md). Without BREV_ENV_ID the returned media URLs will be raw http://localhost:... and the Brev-link checks will fail.",
  "expects": [
    {
      "query": "Upload the sample warehouse video to VIOS with timestamp 2025-01-01T00:00:00.000Z.",
      "checks": [
        "The upload API call (PUT /vst/api/v1/storage/file/<filename>?timestamp=...) returns HTTP 2xx",
        "The response JSON contains both a sensorId and a streamId (non-empty UUIDs)",
        "curl -sf http://localhost:30888/vst/api/v1/sensor/list returns a JSON array containing a sensor whose name matches the uploaded video's filename stem",
        "curl -sf http://localhost:30888/vst/api/v1/sensor/<sensorId>/streams returns a non-empty streams array whose main stream's url is a local file path under /home/vst/... or similar (NOT rtsp://)"
      ]
    },
    {
      "query": "Extract a snapshot from 5 seconds into the uploaded video and return a shareable URL.",
      "checks": [
        "GET /vst/api/v1/replay/stream/<streamId>/picture/url?startTime=2025-01-01T00:00:05.000Z returns a JSON object with a non-empty imageUrl field",
        "The returned imageUrl matches the Brev secure-link pattern: https://<BREV_LINK_PREFIX>-<BREV_ENV_ID>.brevlab.com/... (NOT http://localhost:... and NOT http://<internal-ip>:...)",
        "curl -sfI <imageUrl> returns HTTP 200",
        "The response Content-Type starts with image/ (typically image/jpeg)",
        "The response Content-Length is greater than 2000 bytes (rejects empty / error-placeholder images)"
      ]
    },
    {
      "query": "Extract a video clip from 3 to 5 seconds (mp4 container) from the uploaded video and return a shareable URL.",
      "checks": [
        "GET /vst/api/v1/storage/file/<streamId>/url?startTime=2025-01-01T00:00:03.000Z&endTime=2025-01-01T00:00:05.000Z&container=mp4&disableAudio=true returns a JSON object with a non-empty videoUrl field",
        "The returned videoUrl matches the Brev secure-link pattern: https://<BREV_LINK_PREFIX>-<BREV_ENV_ID>.brevlab.com/... (NOT http://localhost:... and NOT http://<internal-ip>:...)",
        "curl -sfI <videoUrl> returns HTTP 200",
        "The response Content-Type starts with video/ (typically video/mp4)",
        "The response Content-Length is greater than 10000 bytes (rejects empty / error-page responses)",
        "The response JSON's startTime is within a minute of the requested 00:00:03 and expiryISO is in the future"
      ]
    }
  ]
}
```

Source: [`skills/vios/eval/base_profile_ops.json`](../../../skills/vios/eval/base_profile_ops.json)

What the coordinator derives from this spec:
- `env` says **"full-remote deployed VSS base profile"** → inject a `deploy` task with `mode=remote-all` + `profile=base` ahead of the vios tasks.
- `env` says **"Run on ONE platform only … L40S recommended"** → target a single subagent (`l40s`), not a fan-out. VIOS/VST is GPU-independent, so there's no value in running it four times.
- `expects[]` has 3 entries → 3 sequential vios tasks on that one platform, each chained via `requires_previous_passed`.
- `checks` use regex on Brev-link URLs + HEAD `Content-Type` probing → the generic judge (`tools/eval/harbor/verifiers/generic_judge.py`) routes them: the curl-prefixed ones run as shell probes, the regex-style ones go through the LLM judge.

## Running individual commands

The coordinator does all of this automatically — these manual commands are for debugging or bootstrapping a new skill's adapter.

### Generate a dataset for one spec

```bash
set -a && source ~/eval-coordinator/.env && set +a

# Deploy skill — profile × platform × mode matrix
python3 tools/eval/harbor/adapters/deploy/generate.py \
  --output-dir tools/eval/harbor/datasets/deploy \
  --skill-dir skills/deploy \
  --profile base --platform L40S

# Single-platform skill (e.g. vios)
python3 tools/eval/harbor/adapters/vios/generate.py \
  --output-dir tools/eval/harbor/datasets/vios \
  --skill-dir skills/vios \
  --platform L40S
```

### Run a single Harbor trial by hand

```bash
set -a && source ~/eval-coordinator/.env && set +a
export BREV_INSTANCE=vss-eval-l40s

uvx harbor run \
  --environment-import-path "tools.eval.harbor.envs.brev_env:BrevEnvironment" \
  -p tools/eval/harbor/datasets/deploy/base \
  -i l40s-remote-all \
  -a claude-code \
  --model "$ANTHROPIC_MODEL" \
  --ak api_base="$ANTHROPIC_BASE_URL/v1" \
  --ae CLAUDE_CODE_DISABLE_THINKING=1 \
  --max-retries 0 -n 1 --yes \
  -o tools/eval/harbor/results/manual-$(date +%Y%m%d-%H%M%S)
```

`CLAUDE_CODE_DISABLE_THINKING=1` is required when routing through the NVIDIA Anthropic proxy — claude-code ≥ 2.1.x otherwise emits a `context_management` field the proxy rejects with HTTP 400.

### Inspect a result

After harbor exits, flatten the run into the viewer directory so `harbor view` can index it:

```bash
cd tools/eval/harbor/results
mv "<run_id>/<date>" "_viewer/<run_id>__<date>"
rmdir "<run_id>" 2>/dev/null || true
```

Then browse `https://harbor-<BREV_ENV_ID>.brevlab.com/jobs/<run_id>__<date>`. The viewer is launched once per coordinator host:

```bash
nohup uvx harbor view tools/eval/harbor/results/_viewer --jobs \
  --host 0.0.0.0 --port 8080 > /tmp/harbor-view.log 2>&1 &
disown
```

### Spawn the coordinator headlessly

If you want the coordinator running in the background of a tmux session (so it survives your ssh disconnect):

```bash
tmux new -d -s coordinator \
  "cd ~/eval-coordinator && claude --dangerously-skip-permissions"
# then attach and type:
tmux attach -t coordinator
# at the claude prompt:
/loop AGENTS.md
# detach: Ctrl-b d
```

## Interpreting results

After a run, find the result in `tools/eval/harbor/results/<timestamp>/<skill>/`:

```
results/20260416-192758/deploy/base/
├── 2026-04-16__19-27-59/
│   └── base__<trial-id>/
│       ├── config.json
│       ├── trial.log
│       ├── verifier/
│       │   ├── reward.txt        ← 0.0–1.0
│       │   └── test-stdout.txt   ← verifier output
│       └── agent/
│           └── claude-code.txt   ← agent trace
└── result.json                   ← aggregate stats
```

`result.json` shows the mean reward across trials:

```json
{
  "stats": {
    "n_trials": 1,
    "n_errors": 0,
    "evals": {
      "claude-code__anthropic/bedrock-claude-sonnet-4-6__deploy": {
        "metrics": [{"mean": 1.0}]
      }
    }
  }
}
```

## Troubleshooting

**Agent returns "Not logged in"**
API key not set or invalid. Check `ANTHROPIC_API_KEY` and (if using a proxy) `ANTHROPIC_BASE_URL`.

**"Invalid beta flag"**
Your API endpoint doesn't support Claude Code's beta headers. Harbor's `--ak api_base=...` handles most proxies automatically.

**`AddTestsDirError` / `DownloadVerifierDirError`**
File upload/download to the Brev instance failed. Check `brev exec <instance> "echo ok"` works manually. Clear `/tests /logs /skills` on the instance and retry.

**Instance creation fails**
Some Brev providers have capacity issues. Retry manually, or flag it in the PR comment — the coordinator won't auto-select an alternate instance type; platform → instance mapping is fixed in AGENTS.md §2.

**Brev auth expired mid-run**
Look for a `brev_auth_expired` entry in `/tmp/subagents/_alerts.json`. Fix by running `brev login --auth nvidia` on the coordinator host. The `brev-keepalive.timer` systemd user unit fires `brev ls` every 15 min to keep the access token warm, but it cannot recover when the refresh token itself expires — only an interactive login can.

**Agent deployment fails with "pull access denied"**
`NGC_CLI_API_KEY` missing or invalid. The agent needs it to pull VSS NIM containers from `nvcr.io`.
