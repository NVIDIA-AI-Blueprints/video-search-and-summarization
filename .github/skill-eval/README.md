# VSS Skills Eval

Evaluate `vss-build-vision-ai` and operational VSS skills against a live GPU deployment using [Harbor](https://github.com/laude-institute/harbor). Individual deployment skills are not independently dispatched.

Evaluation is **fully CI-driven**. [`.github/workflows/skills-eval.yml`](../workflows/skills-eval.yml) fires on every push to a `pull-request/<N>` mirror branch whose diff touches `skills/` or `.github/skill-eval/`, and runs a single claude-agent-sdk session ([`skills_eval_agent.py`](skills_eval_agent.py)) that:

1. Diffs the PR against its base branch and picks out changed skills with an eval spec at `skills/<skill>/evals/<name>.json` (legacy `skills/<skill>/eval/<name>.json` still accepted).
2. Generates Harbor datasets per `(skill, profile, platform, mode)` via the adapter at [`adapters/<skill>/generate.py`](adapters/).
3. Selects an operator-managed `vss-eval-*` pool member matching the target platform, per the fleet-selection algorithm in [`AGENTS.md`](AGENTS.md) § 5a. The harness does **not** auto-provision — if no pool member matches, the run blocks until one appears (or times out).
4. Calls [`run_leg.py`](run_leg.py), which acquires the per-instance `flock`, holds it while every Harbor subprocess for this `(spec, platform)` runs, and invokes Harbor 0.20.0 through Python 3.12 with the canonical flags from [`AGENTS.md § Harbor invocation`](AGENTS.md).
5. Verifies each trial (containers running, endpoints healthy, trajectory / response / rubric checks — see `verifiers/generic_judge.py`) and scores 0.0–1.0.
6. Posts one Markdown results summary per `(PR, eval-spec)` batch as a PR comment, with trace URLs served by `harbor view`.

The whole thing runs inside the 14-hour GitHub Actions job timeout. The `.github/skill-eval/AGENTS.md` file **is** the agent's system prompt — keep it readable.

## Prerequisites

The workflow runs on a self-hosted GitHub Actions runner installed on `vss-skill-validator` (a long-running Brev CPU instance in the NVIDIA org). That host needs:

- **[uv](https://github.com/astral-sh/uv)** — Harbor 0.20.0 is invoked in an isolated Python 3.12 environment.
- **[Brev CLI](https://docs.nvidia.com/brev/latest/cli/cli-overview)** — authenticated via `brev login --auth nvidia` (refresh token lasts ~30 days; a user-level `brev-keepalive.timer` keeps the access token warm).
- **`git`**, **`gh` (GitHub CLI)** — authenticated against the VSS repo.
- **Python 3.12** — the workflows pin this runtime for the coordinator, adapters, `run_leg.py`, and Harbor. Each matrix leg installs Claude Agent SDK 0.2.128 in its own virtual environment so parallel jobs never mutate a shared interpreter.
- **A `.env` at `/home/ubuntu/eval-coordinator/.env`** with the keys below — the workflow step `Load coordinator env` sources this file.

### GPU targets (operator-managed `vss-eval-*` pool)

The runner has no GPU. Eval trials run on a long-lived pool of `vss-eval-*` Brev instances that the **operator** provisions ahead of time with `brev create`; the skill-eval agent only locks, drives, and resets them — never creates, stops, or deletes pool members. Default pool today:

| Platform | Pool member(s) | Instance type |
|---|---|---|
| `l40s` | `vss-eval-l40s`, `vss-eval-l40s-1g`, `vss-eval-l40s-2` | `massedcompute_L40S` / `massedcompute_L40Sx2` |
| `h100` | `vss-eval-h100` (when needed) | launchpad `dmz.h100x2.pcie` preferred |
| `rtx` | Managed `vss-eval-rtx-*`, registered RTX PRO workers such as `vss-eval-rtx-2g-VM1b`–`VM4b`, and capability-routed `vss-eval-geforce-rtx4090-vm*` workers | AWS `g7e.4xlarge` / `g7e.12xlarge`, registered RTX PRO Server 6000, or approved RTX 4090 |
| `spark` | BYOH DGX Spark node registered via `brev register` | n/a |

Per-CI-run hygiene is the trial's own responsibility: each spec's first agent turn invokes `/vss-build-vision-ai` (or a standalone deploy runbook) to bring up whatever it needs, including `docker compose down` of any prior leftover containers on the box. The harness no longer pre-deploys profiles or maintains an `active-deploy.txt` marker — that machinery was removed in favour of putting deploy steps inside the trial trajectory where they're visible in the reward, judge, and `claude-code.txt`. Fleet-selection scoring + the wait-for-pool path on exhaustion live in [`AGENTS.md § Platform topology`](AGENTS.md).

Operational specs use two independent routes. Their first `expects[]` task
runs with the coding route and `/vss-build-vision-ai`; its query supplies the
deployment intent and its checks supply the readiness verdict. Remaining tasks
run with the operational route. When that route uses NemoClaw, the first query
also owns sandbox setup. Specs
that require the in-stack agent bootstrap NemoClaw as a separate evaluation
client with the checked-in notebook; they preserve the application backend
and disable its UI adapter. Remaining entries run through the ready sandbox.
Build Vision AI and other non-operational specs use the coding route throughout.

Default setup runs use **Codex with Sol 6.1** (`azure/openai/gpt-6.1-sol`);
operational queries use **NemoClaw with Opus 5.5**
(`aws/anthropic/bedrock-claude-opus-5-5`). Both default to hosted NVIDIA
Inference. These defaults apply to automatic PR evaluations and manual dispatch;
manual inputs can override each role independently. Selecting `local-nim`
requires replacing the hosted model ID with an available NIM image ID.

Manual runs configure both routes without changing the coordinator or judge:

| Workflow input | Meaning |
|---|---|
| `coding_harness` | Build Vision AI/setup runtime: `codex` (default) or `claude-code` |
| `coding_deployment` | `hosted-nvidia-inference` (default) or `local-nim` for coding/setup |
| `coding_model` | Hosted: [Inference Hub](https://inference.nvidia.com/) model ID, such as `nvidia/nvidia/nemotron-3.5-lightning`. Local NIM: self-hosted NIM image ID from [build.nvidia.com](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b?nim=self-hosted), such as `nvidia/nemotron-3.5-lightning-30b-a3b`. Default `azure/openai/gpt-6.1-sol`; select a NIM image ID explicitly for `local-nim` |
| `operational_harness` | Operational runtime: `nemoclaw` (default), `claude-code`, or `codex` |
| `operational_deployment` | Independent `hosted-nvidia-inference` (default) or `local-nim` for operational tasks |
| `operational_model` | Same ID rules as `coding_model`; default `aws/anthropic/bedrock-claude-opus-5-5`, independently selected for operational tasks |
| `spark_runner` | Run on Brev external node `extnode-3I3rYbpIyfB6TcEXWk2k0wabSR8` (`Spark-ba-WiFi`); default false |


The runner owns credentials. Hosted routes use the fixed
`https://inference-api.nvidia.com/v1` endpoint. Local routes use a fixed,
non-secret client placeholder, never the hosted inference key. No arbitrary endpoint
input is exposed. Coordinator and judge routing stays unchanged.

### Local NIM lifecycle

Select `local-nim` independently for either role. Provide a model-specific NIM
ID (`publisher/model`, optionally prefixed by `nvidia_nim/`) and configure
`NGC_CLI_API_KEY` or `NGC_API_KEY` on the coordinator. Proprietary hosted-only
models cannot run locally. For Nemotron 3.5 Lightning, enter
`nvidia/nemotron-3.5-lightning-30b-a3b`, the ID of its
[self-hosted NIM image](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b?nim=self-hosted),
instead of the hosted Inference Hub ID `nvidia/nvidia/nemotron-3.5-lightning`.
The workflow validates this format before selecting a GPU worker. The worker
authenticates to `nvcr.io`, discovers released model-specific NIM tags, selects
the newest release with a Linux image matching the worker CPU architecture,
and pins its digest. Qwen3-32B on ARM64
also resolves its documented `qwen3-32b-dgx-spark` packaging variant. There is
no fallback to a different model, a model-free container, or hosted inference.
A missing image, incompatible architecture, registry access failure, and
startup failure are distinct errors. This checks architecture only; it does
not estimate GPU capacity, memory, disk, or combined VSS/inference demand.
When set, `NGC_API_KEY` is sent over the provisioning command's stdin; it is
not written to a worker key file or forwarded into `~/.eval_env`.
The existing VSS deploy path still forwards `NGC_CLI_API_KEY` to the evaluated
agent because that agent performs the VSS deployment.

After the existing first-task Docker reset, the worker starts one NIM per
unique selected local model. Identical coding and operational models share
one container; later tasks reuse that deployment. Different models run as
separate containers. A pinned LiteLLM adapter provides Anthropic Messages,
OpenAI Responses, and Chat Completions for NemoClaw. The ephemeral, job-owned
adapter runs without authentication: its config has no `master_key`, and
readiness/protocol probes send no API key. Clients that require a non-empty
key receive the fixed, non-secret `local-nim` placeholder; the proxy does not
validate it. Hosted and NGC credentials retain their existing authentication.
NIM ports bind to loopback; NemoClaw reaches the adapter on the worker's private
address.
The worker exports that exact host in
`NEMOCLAW_TRUSTED_PRIVATE_INFERENCE_HOSTS`, so NemoClaw's private-endpoint
preflight admits the owned inference adapter without granting a subnet or
relaxing other URL checks. Startup and reuse both restore this declaration.
Startup and reuse smoke requests exercise each selected protocol.
The NIM and VSS run on the same worker.

Onboarding owns NemoClaw's provider binding; operational prompts do not rewrite
it or refresh proxy credentials. The headless runner checks gateway health and
collects the native OpenClaw session and token usage from the actual prompt.
This provides evidence of inference through OpenShell, which a host smoke
request does not cover. Failure stops the prompt and leaves its error in
`agent.log`.

Startup is bounded to 5,400 seconds within the existing environment deadline;
cold downloads may exceed this and fail explicitly. The worker needs access
to NGC, Docker Hub (`python:3.12-slim`), and PyPI (`litellm[proxy]==1.103.0`).
The adapter listens on port 18400 and is advertised on the worker's private address.
NIM ports 18410+ bind to loopback.
Job-owned containers are removed when the leg ends or is cancelled. The next
first-task Docker reset reconciles leftovers after an uncatchable SIGKILL.
Weights persist under `~/.cache/skill-eval-nim-models/`, outside Docker volumes.
Sanitized image/tag/digest, model, architecture, startup errors, and bounded
container logs appear in each trial's `artifacts/local-nim` directory (under
Harbor's collected `/logs/artifacts` tree). `model-deployments.json` records
role choices and the actual worker at the leg results root.

### Spark selection

The checkbox selects the **Brev execution worker**, not the GitHub Actions
coordinator. Spark is opt-in: with the checkbox off (including automatic PR
evals), the plan excludes `DGX-SPARK` rows even when a skill spec supports that
platform. Specs that support only Spark produce no eval jobs until it is
selected. The runner also rejects Spark platform, hardware, or instance hints
without the explicit selection, before acquiring a worker lock.
`run_leg.py` resolves the registered node by external node ID
(or the supplied name on older Brev versions), then holds the existing
per-worker lock across all tasks and NIM cleanup. Missing nodes or conflicting
explicit instance overrides fail; no other worker is selected. If Brev reports
the selected node disconnected, a bounded SSH probe from the coordinator must
succeed before proceeding. This handles stale registry status without accepting
an unreachable worker.
The coordinator needs its Brev SSH alias configured, just as for other
registered workers. Spark must report ARM64. Existing GPU/memory/disk guards
are bypassed for this explicit Spark override; normal pool runs retain their
existing VSS resource checks. The manual plan selects only declared `DGX-SPARK`
specs and fails if none exist; `machine.txt` records the actual worker. Selecting Spark
does not rewrite a spec's deployment instructions or guarantee that all VSS
images in that scenario support ARM64.

### API keys (`/home/ubuntu/eval-coordinator/.env` on the runner)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude Code authentication (NVIDIA inference API key works) |
| `ANTHROPIC_MODEL` | Model ID (e.g. `aws/anthropic/bedrock-claude-sonnet-4-6`) |
| `SKILLS_EVAL_CODING_API_KEY` | Optional coding-route credential override |
| `SKILLS_EVAL_OPERATIONAL_API_KEY` | Optional operational-route credential override |
| `NGC_CLI_API_KEY` | Pull VSS NIM containers from `nvcr.io` |
| `LLM_REMOTE_URL` / `LLM_REMOTE_MODEL` | Remote-LLM endpoint used by `remote-*` deploy modes |
| `VLM_REMOTE_URL` / `VLM_REMOTE_MODEL` | Remote-VLM endpoint used by `remote-*` deploy modes |
| `HF_TOKEN` | Required by RT-VLM / RT-Embed when loading Hugging Face checkpoints |
| `GITHUB_TOKEN` | Issued to `gh pr comment` when the agent posts results |
| `BREV_REGISTERED_POOL` | Comma/space-separated registered-node names approved for automatic pool selection |
| `BREV_RTX4090_POOL` | Registered RTX 4090 workers; routed only to the proven tests in `run_leg.py::RTX4090_TESTS` / `RTX4090_ALL_TESTS` |

Operational setup must finish successfully before later tasks reuse its
deployment. The runner checks both the reward and Harbor's structured
`result.json`: an agent timeout or other recorded exception stops the chain
even if Harbor exits zero and the verifier awards full credit.

Specs that require sample videos also declare fixture preparation in their
setup query. When NemoClaw is selected, setup downloads the pinned bundle on
the host, copies the needed MP4 files into the sandbox using NemoClaw's upload
command, and verifies matching hashes. Later tasks use those sandbox files;
NGC credentials stay on the host.

## Layout

```
.github/skill-eval/
├── README.md              ← you are here
├── AGENTS.md              ← skills-eval agent's system prompt
├── skills_eval_agent.py   ← the CI entrypoint (spawns the agent)
├── run_leg.py             ← structural per-box lock + Harbor launcher
├── adapters/              ← per-skill dataset generators
│   ├── vss-build-vision-ai/           ← profile × platform × mode matrix
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
| `expects` | `array` | Ordered list — **each entry becomes one Harbor task**, chained to the previous via `requires_previous_passed`. There is no separate `env` field: every prerequisite (deployed profile, required env vars, ports, sample-data ingest, platform notes) goes **inside the relevant `expects[].query`** — usually the first/setup query, often a `/vss-build-vision-ai …` deploy step. |
| `expects[].query` | `string` | What the agent is asked to do at this step, in plain English — including any prerequisites/environment the step needs. Can embed `{{platform}}`, `{{mode}}`, `{{llm_mode}}`, `{{vlm_mode}}`, `{{repo_root}}` — the adapter substitutes these per-dataset. |
| `expects[].checks` | `string[]` | Assertions the verifier runs after the agent acts. Backtick-wrapped `curl` / `docker` / `grep` commands are extracted and run as shell subprocesses (pass if exit 0). Everything else is handed to a `claude-agent-sdk` judge agent with `Bash` + `Read` + `Grep` tools — so trajectory-style checks ("agent called X exactly once", "response renders a 'Verification Step' section") are first-class; no per-skill probe scripts required. |

### Build Vision AI Specs

`vss-build-vision-ai` evals are spec-driven like the other skills. Each spec names a build/profile label, lists the target platforms under `resources.platforms`, and puts the stock-workflow or custom-build instruction directly in `expects[].query`.

For stock deployments, write the query in the same terms the skill routes on, such as "use the `/vss-build-vision-ai` stock Search workflow with remote LLM/VLM placement" or "use the stock Alerts workflow in verification mode (`MODE=2d_cv`)". Do not use legacy `-p` / `-m` command flags.

Manual dispatch with `skills=operations` selects all runtime specs under `skills/operations/` and excludes Build Vision AI's own evals. Fleet sweeps run at most two legs concurrently. With `spark_runner=true`, matrix legs queue one at a time on the shared Spark worker.

Worker preparation probes connectivity before repository sync. The probe, sync,
and file transfers allow three attempts for transient transport failures, with
exponential backoff and jitter. Authentication and command errors fail immediately;
arbitrary remote commands are not automatically retried. Timed-out managed-worker
commands retain their stderr tail for diagnosis.

NIM container creation allows ten minutes per attempt within the existing overall
startup budget. On timeout, the launcher inspects the deterministic container name:
a running container with the expected job owner and pinned image proceeds to
readiness checks; only a confirmed absent container is retried. Unknown ownership,
unavailable inspection, or a stopped container fails explicitly. The model and
LiteLLM readiness budgets are unchanged.

The runner passes the selected runtime as `SKILLS_EVAL_OPERATIONAL_HARNESS` to the worker. Operational setup queries explicitly invoke `/vss-build-vision-ai` and specify conditional NemoClaw setup, skill installation, and readiness. Adapters include the declared Build Vision AI skill when generating tasks; `run_leg.py` never rewrites generated instructions.

NemoClaw operational legs also receive a per-run/per-leg gateway, dashboard and
relay port triplet. NemoClaw v0.0.127 uses the gateway port to scope its host
registry and shared inference provider under `~/.nemoclaw/gateways/<port>/`.
Docker cleanup alone leaves the default registry intact; a unique sandbox name
does not prevent conflicts with its previous inference routes. Before setup,
the coordinator allocates and claims an unused namespace on the locked worker
before Harbor starts. It skips existing unowned state, another eval's receipt,
and occupied ports without deleting registries or stopping listeners. A retry
reuses its own receipt. Setup and all operational steps receive the selected
triplet and keep the same deployment. Explicit port overrides remain strict:
they must be distinct, available, and use a non-default gateway port.
The image build persists the dashboard port from onboarding into OpenClaw's
`gateway.port`. NemoClaw's canonical warm-up and pairing approval clear runtime
port overrides, so an inherited default port would prevent scope approval.

Before each operational prompt, `agent/readiness.json` records separate checks
for sandbox access, gateway health, authenticated gateway health, and the
sandbox-installed `vss configure check`. A listening HTTP endpoint alone does
not establish successful pairing. Readiness failure stops before model work
and records stage/exit metadata without gateway credentials or raw config.
Pending pairing receives full probe budgets; when no full attempt fits, the
report preserves the last pairing failure with a `pairing_deadline` reason.
The same report and the initial namespace ownership/port receipt are included
under `artifacts/logs/artifacts/nemoclaw/` so the workflow archive preserves
them even though it excludes raw agent trajectories.

The setup checks require trajectory evidence of Build Vision AI use and,
when selected, a ready NemoClaw sandbox with its VSS CLI configured. An answer
that only mentions the skill or sandbox does not satisfy those checks.

### Worked example — `skills/operations/vss-manage-video-io-storage/evals/vios_ops.json`

The first query explicitly asks `/vss-build-vision-ai` to deploy VIOS in SDRC-routed mode, without uploading the evaluation video. It also describes how to attach NemoClaw when selected. Later queries exercise upload, snapshot, clip, recording, and replay APIs on the preserved deployment.

The spec declares both `vss-manage-video-io-storage` and `vss-build-vision-ai` in `skills`. Its platform matrix determines the generated datasets; each `expects[]` entry becomes one task, gated on the preceding task passing. The adapter renders the spec query and keeps verifier checks separate from the agent instruction.

## Running a trial by hand

For debugging an adapter or verifier locally, outside CI:

```bash
set -a && source /home/ubuntu/eval-coordinator/.env && set +a

# 1. Generate the dataset for one spec.
python3 .github/skill-eval/adapters/vss-manage-video-io-storage/generate.py \
  --output-dir /tmp/skill-eval/datasets/vss-manage-video-io-storage \
  --skill-dir skills/operations/vss-manage-video-io-storage \
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

`run_leg.py` publishes each finished trial into the viewer dir itself
(copying, not moving — the workflow's collector still tars the leg's
results root afterwards) and appends the browsable URL to
`<results-root>/trace-urls.tsv`:

```
step-7	step-7__E6dBECL	https://harbor-<ENV_ID>.brevlab.com/jobs/<job>/tasks/<source>/<agent>/<provider>/<model>/<task>
```

Open the URL from that file rather than composing one: the trailing
`<task>` is Harbor's fully-qualified `task_name`
(`nvidia-vss/<dataset>-step-N`), not the `step-N` filter, and the
viewer is an SPA that renders a wrong path as a **blank page**, never
a 404.

`harbor view` runs persistently on the CI runner host. If it's down:

```bash
nohup uvx --python 3.12 --from 'harbor==0.20.0' harbor view /tmp/skill-eval/results/_viewer --jobs \
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

### Codex scratch and trajectory isolation

Each Codex invocation uses a fresh temporary home and credential directory.
Harbor appends provider and MCP configuration, so an interrupted invocation's
configuration must never be reused by a later invocation. Codex resume still
restores the explicitly requested session through Harbor's resume mechanism.

Before each trial, the Brev environment archives the full `/logs/agent/sessions`
tree, including Codex date directories and Claude project directories, along
with root agent outputs. A failed launch therefore cannot borrow prior-trial
sessions, token counts, or deployment evidence. Archives remain under
`~/.claude-archive/` for runner-side investigation.

Specs can declare `sandbox_fixtures` as MP4 basenames from the pinned bundle
at `/tmp/vss-sample-data/dev-profile-sample-data/`. For NemoClaw setup, after
the coding agent succeeds and before grading, the harness uploads only these
files into the supplied sandbox and requires matching SHA-256 checksums.
Missing host files, upload failures and mismatches fail setup. It neither
downloads fixtures nor registers them with VSS. Sanitized results are retained
in `nemoclaw/fixtures.json` in the workflow artifacts.
