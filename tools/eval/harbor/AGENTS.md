# Harbor Eval Coordinator — Agent Playbook

This file is the operating manual for a **long-running coordinator agent**
that watches the repo for PRs containing new skill evals, dispatches those
evals across a fixed pool of per-platform subagents, and reports results
back onto the PR.

You are the coordinator. Read this end-to-end before doing anything.

---

## 1. Your job in one paragraph

Watch the GitHub repo for PRs targeting `develop` from branches matching
`pull-request/*`. When such a PR lands (or gets a new commit), inspect its
diff. If it modifies anything under `skills/`, scan the changed skills for
`eval/*.json` specs. For each spec, determine the **target platform(s) and
profile dependency** from the spec's `env` field (not all evals run on all
four platforms — some are platform-specific, some need a specific deployed
profile as prerequisite, some need only a GPU host with no VSS deploy at
all). Generate a matching Harbor adapter under
`tools/eval/harbor/adapters/<skill>/` that emits **one task per entry in
the spec's `expects[]`** (each `expects[]` item is a step in a thread —
they go onto the queue in order, chained via `requires_previous_passed`
within the same queue). Append those task sequences to the relevant
subagent queue(s). Wait for subagents to report back, post the results
(with Harbor trace URLs) as a comment on the source PR, and finally raise
your own PR (branch `feat/eval-adapter-<N>-<sha>`) carrying only the
adapter code changes. Then loop.

Four subagents exist — **L40S, H100, RTX 6000 Pro, and SPARK (DGX Spark
GB10)** — but any given eval spec may target only a subset of them (or
just one).

You do **not** run evals yourself. You do **not** modify `skills/`
(see § 10). All orchestration state lives in the per-platform subagent
queue files under `/tmp/subagents/`.

---

## 2. Fixed topology

### Four per-platform subagents

Spawn one long-running subagent per platform. Each one owns a queue file
in `/tmp/subagents/` and processes tasks sequentially on its dedicated
Brev instance.

| Subagent | Instance | Queue file | Notes |
|---|---|---|---|
| `l40s` | `vss-eval-l40s` (2× L40S, `l40s-48gb.2x`, stoppable) | `/tmp/subagents/l40s.json` | Runs remote-all and remote-{llm,vlm} modes. No local shared (48 GB too tight). |
| `h100` | `vss-eval-h100` (2× H100, `dmz.h100x2.pcie`, non-stoppable — delete after use) | `/tmp/subagents/h100.json` | Covers shared/dedicated/remote-* on H100. Use dmz (driver ≥580, 1 TB disk), NOT hyperstack. |
| `rtx` | `vss-eval-rtx` (2× RTX PRO 6000, `g7e.12xlarge`, stoppable) | `/tmp/subagents/rtx.json` | Baseline for shared/dedicated/remote-*. |
| `spark` | `SPARK` (1× GB10 BYOH, SSH alias `spark`) | `/tmp/subagents/spark.json` | Edge 4B mandatory for shared mode (see `skills/deploy/references/edge.md`). Never stop/delete — BYOH. |

Create `/tmp/subagents/` yourself on startup. Seed each JSON with
`{"subagent": "<name>", "tasks": [], "results": []}` if it doesn't exist.

Each subagent runs the playbook in **§ 7** below. You spawn them once at
startup and keep them alive across PR events.

### Two monitors (your main loop)

1. **Branch monitor** — polls the repo's branch list every 60 s, filtering
   for branches named `pull-request/<N>`. NVIDIA-AI-Blueprints'
   `copy-pr-bot` mirrors contributor PRs into these branches **without**
   opening a mirror PR, so `gh pr list` alone misses them. Query via
   ```bash
   gh api "repos/<org>/<repo>/branches?per_page=100" --paginate \
     --jq '.[] | select(.name | startswith("pull-request/")) |
             {name: .name, sha: .commit.sha}'
   ```
   For each `pull-request/<N>` branch whose commit SHA differs from the
   one tracked in `/tmp/subagents/_prs_seen.json["#<N>"].head_sha`, fire
   the §3 workflow with `<N>` as the PR number. The source PR `#<N>`
   still exists (same number as the branch suffix) for metadata and
   comment posting.
2. **Results monitor** — watches `/tmp/subagents/*.json` for new entries in
   the `results` array (tasks a subagent finished). Each new result triggers
   a PR comment and, if the PR's entire task batch is now done, a final PR
   to upstream the adapter changes.

Run both monitors concurrently (two nested poll loops in a single process,
or two long-running `Monitor` background tasks if available to you).

---

## 3. PR-event workflow

Fire whenever the branch monitor observes a new `pull-request/<N>` branch
or a commit SHA change on an already-tracked one. The source PR number is
`<N>` (from the branch suffix).

### Steps

1. **Fetch the mirror-branch diff vs the PR's actual base:**
   ```bash
   N=100
   BASE=$(gh pr view "$N" --json baseRefName --jq .baseRefName)
   gh api "repos/<org>/<repo>/compare/${BASE}...pull-request/$N" \
     --jq '.files[].filename'
   ```
   **Never hardcode `develop` as the base.** Some PRs target `develop` but
   others target stacked branches like `feat/skills` (PR #102 is an example:
   feat/skills is its base, and diffing vs `develop` conflates PR #102's
   delta with the 6+ commits feat/skills already has on top of develop).
   Always derive `<base>` from `pr.baseRefName`.

   Don't use `gh pr diff <N>` — the source PR's `headRefName` is usually
   a contributor-controlled branch (e.g. `feat/foo`), not
   `pull-request/<N>`, so the diff would be correct but conceptually
   different from the mirror. Always diff the mirror branch so the PR
   head SHA we're evaluating matches the one recorded in
   `_prs_seen.json`.

   Also fetch source PR metadata for comment posting and adapter-PR
   cross-linking:
   ```bash
   gh pr view $N --json number,title,author,url,baseRefName,state
   ```
   If the source PR is missing or closed, process the branch anyway
   (skill authors may still want the eval artefacts) but skip the §7
   comment step and log an alert to
   `/tmp/subagents/_prs_seen.json["#<N>"].alerts[]`.

2. **Filter to skills touched:**
   ```
   changed_skills = { split-path[1]  for  each path in diff  if path starts with "skills/" }
   ```
   If empty, do nothing. Update `_prs_seen.json` and return.

3. **For each changed skill `<name>`:**
   - `git show <HEAD_SHA>:skills/<name>/` to list files at PR head.
   - Collect every `skills/<name>/eval/*.json`.
   - If the skill has no eval spec: comment on the PR with *"No eval spec
     found under `skills/<name>/eval/*.json` — skipping automated eval."*
     and move on.

4. **For each eval spec `<profile>.json`:**
   - If `tools/eval/harbor/adapters/<name>/generate.py` already exists,
     **re-run it** rather than regenerating. Only synthesize a fresh
     adapter for skills that don't have one yet — see **§ 4** for the
     exists-vs-missing decision tree.
   - Run the adapter to materialize datasets under
     `tools/eval/harbor/datasets/<name>/<profile>/<platform>/`.
   - Decide which platforms to dispatch to (see **§ 5**) and **append one
     task entry per platform** to that platform's queue file in
     `/tmp/subagents/`.
   - Record the PR number, eval spec path, and expected task count in
     `/tmp/subagents/_prs_seen.json[<pr>].batches[<spec>]` so the results
     monitor knows when the batch is complete.

5. Update `_prs_seen.json` with the new head SHA.

---

## 4. Adapter generation rules

**Before generating anything, check if the adapter already exists.**
List `tools/eval/harbor/adapters/<skill>/` — if a `generate.py` is
checked in (deploy + vios at time of writing), it is **human-maintained
and must not be regenerated from scratch**. These adapters encode
skill-specific structure (deploy's profile × platform × mode matrix;
vios's single-platform step chain) that would be destroyed by a naive
rewrite. For existing adapters, the coordinator's job is limited to:

1. Re-run the adapter with the new/refreshed spec as input, e.g.
   `python3 tools/eval/harbor/adapters/deploy/generate.py
   --output-dir tools/eval/harbor/datasets/deploy
   --skill-dir skills/deploy --profile <new-profile>`. The adapter
   owns the templating — specs travel through its `_render_eval_spec()`
   substitution pass, and the generated `tests/<spec>.json` is what the
   trial's generic judge actually reads.
2. If the existing adapter's CLI doesn't accept what the new spec
   requires (e.g. a brand-new profile dimension, a mode it doesn't
   know about), **post a comment on the source PR** asking the skill
   author to update the adapter. Do NOT edit the adapter yourself
   (adapters live under `tools/eval/harbor/`, technically coordinator
   territory — but the deploy/vios adapters encode cross-cutting
   skill knowledge, so treat them as skill-author-reviewed).
3. Append enqueue entries (§ 5) using the regenerated dataset paths.

Only regenerate from scratch when `generate.py` does NOT exist yet for
the skill — i.e., a brand-new skill PR that ships an eval spec but no
adapter. In that case, pattern-match from the two existing adapters:

- **`tools/eval/harbor/adapters/deploy/generate.py`** — matrix generator
  (platform × mode). Only use this shape if the skill's spec declares a
  matrix (e.g., `resources.modes`).
- **`tools/eval/harbor/adapters/vios/generate.py`** — single-task-
  per-platform generator. This is the default shape for skills whose spec
  has a flat `expects[]` list.

The adapter emits, **per (platform, expects-entry) pair**:

```
datasets/<skill>/<profile>/<platform>/step-<k>/
  task.toml              # [metadata]: gpu_type, brev_search, min_vram_gb_per_gpu,
                         #   min_root_disk_gb, min_gpu_driver_version,
                         #   requires_deployed_vss, profile_dependency,
                         #   step_index, step_count
                         # [verifier.env]: ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL /
                         #   JUDGE_MODEL forwarded so generic_judge.py can call Claude
  instruction.md         # derived from this single expects-entry:
                         #   just the spec's `query` + the spec's `env` (as
                         #   "Environment notes"). The agent NEVER sees the
                         #   verifier's `checks[]`. See "Never leak checks[]
                         #   into instruction.md" below.
  tests/test.sh          # 2-line wrapper: `python3 generic_judge.py --spec <spec>.json
                         #   --step <k>`. Writes /logs/verifier/reward.txt.
  tests/generic_judge.py # copied from tools/eval/harbor/verifiers/generic_judge.py
                         #   — routes each check to shell / trajectory / response /
                         #   rubric evaluator. Shell-wrapped checks (backtick
                         #   commands) never call the LLM; only narrative checks do.
  tests/<spec>.json      # rendered from skills/<skill>/eval/<profile>.json with
                         #   {{platform}}, {{mode}}, {{llm_mode}}, {{vlm_mode}}, and
                         #   {{llm,vlm}_remote_{url,model}} substituted in.
  solution/solve.sh      # gold-standard or no-op
  skills/<skill>/        # full skill copy
  skills/deploy/         # included if profile_dependency != null (agent can diagnose)
  environment/Dockerfile # FROM scratch (BrevEnvironment takes over)
```

Where `<k>` is the 1-based index into `expects[]` and `step_count` equals
`len(expects)`. Task IDs surface as e.g. `rtxpro6000bw-step-1` /
`rtxpro6000bw-step-2` / `rtxpro6000bw-step-3`.

If a spec has only one `expects[]` entry, emit the single dir without the
`step-<k>` subdir (directly under `<platform>/`) to keep the path flat.

**Never leak the verifier's `checks[]` into `instruction.md`.** The agent
sees `instruction.md`; the verifier sees the spec's `checks[]` (copied into
`tests/`). Keep them disjoint:

- ✅ `instruction.md` contains: the spec's `query`, the spec's `env`
  (as "Environment notes"), a platform hint, and a "run autonomously"
  directive. Nothing else.
- ❌ `instruction.md` must NOT enumerate `checks[]`, restate the expected
  status codes, expected JSON field names, regex patterns the verifier will
  match, Brev-link URL patterns, `Content-Type` expectations, or any other
  acceptance criteria. If the agent sees the checks, it teaches-to-the-test
  (writes output that matches the pattern without doing the underlying
  work), and the eval stops being a real signal.
- The skill author expresses acceptance in the spec's `checks[]` field;
  the agent's job is to figure out *how* to satisfy the user-facing query
  with only the skill's references as guidance. If an instruction would
  be unambiguous only by citing a check, that's a smell the skill's own
  docs / references should fill the gap instead.

Example (vios/base/step-2 query *"Extract a snapshot from 5 seconds into
the uploaded video and return a shareable URL"*):

- Spec's `checks[]` (verifier-only): `imageUrl matches Brev secure-link
  pattern`, `Content-Length > 2000`, etc.
- `instruction.md` (agent-facing): the query + env notes. No mention of
  `imageUrl`, no mention of Brev secure-link, no mention of Content-Length.
  The skill's own `/vios` references tell the agent how to return a
  shareable URL; whether that URL happens to match the Brev pattern is
  what the verifier checks.

Existing adapters that ever shipped `checks[]` in the instruction are a
bug — fix them and regenerate datasets before the next dispatch.

---

**Default verifier = generic judge + eval JSON.** The adapter never hand-rolls
checks. It ships two files into `tests/`: the skill's rendered eval spec and
`tools/eval/harbor/verifiers/generic_judge.py`. The judge classifies each
check by content:

- **Shell checks** — the check contains a backtick-wrapped command starting
  with a safe verb (`curl`, `docker`, `grep`, `ls`, `cat`, `jq`, `ss`,
  `netstat`, `nc`, `file`). The judge runs the command and uses its exit
  code. No LLM call.
- **Response checks** — the check mentions the agent's final reply (e.g.
  *"the agent's response contains a URL like ..."*). The judge feeds the
  trajectory's last assistant message to Claude with the natural-language
  assertion and a strict JSON schema.
- **Trajectory checks** — the check mentions tool calls / invocations /
  traces (e.g. *"the agent called VST's upload API exactly once"*). The
  judge feeds the full trajectory to Claude.

Skill authors write assertions in plain English; the judge does the routing.
Both the system prompt and the evidence are XML-quarantined to blunt prompt
injection from untrusted agent output.

Python probes are the **exception**, not the rule. Reach for one only when
the check can't be expressed as a one-liner *and* the LLM judge can't see
enough state (e.g. the check needs to sample a binary file's first bytes,
or tail a stream across multiple trial containers). When you do ship one,
place it at `skills/<skill>/scripts/<probe>.py`, have it accept
`--step <k>`, and have the adapter copy it alongside `generic_judge.py` —
but budget this as extra skill-author work you need to justify.

After generating, commit nothing yet — § 8 handles the PR.

### Canonical eval spec example

Every spec you read follows the same shape — use this as the pattern-match
source when processing a new skill:

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

Source of truth (never edit this — it's skill-author territory, see § 10):
[`skills/vios/eval/base_profile_ops.json`](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/feat/skills/skills/vios/eval/base_profile_ops.json)

Three things to extract from any spec before generating the adapter:

1. **`skills[]`** — folder name(s) under `skills/`. For multi-skill specs,
   each task's `skill` field names the primary one; the adapter copies all
   listed skills into the task's `skills/` dir.
2. **`env`** — parse for: platform targets (platforms named, GPU hints,
   "L40S" / "H100" keywords), VSS profile dependency (`"deployed VSS
   <profile> profile"` phrasing), required env vars (`HF_TOKEN`,
   `BREV_ENV_ID`, etc.). See § 5.
3. **`expects[]`** — length = task count. Each entry's `query` becomes
   the task's `instruction.md`; each `checks` list is routed by
   `generic_judge.py` (shell / trajectory / response) with no adapter
   cleverness — see the default-verifier block above.

### Agentic verifiers (LLM-as-judge) — optional

A spec may add a `judge` block alongside `checks` when the outcome needs
semantic judgment rather than deterministic assertions (free-form text
quality, rubric grading, etc.):

```jsonc
"judge": {
  "rubric": "The agent must produce a report that (a) references at least "
            "three distinct timestamps, (b) mentions at least one forklift-"
            "related action, and (c) includes a shareable URL.",
  "pass_threshold": 0.8,
  "temperature": 0.0,
  "n_samples": 3
}
```

When the adapter sees `judge`, it wires the verifier in a **gated** shape:
deterministic `checks` run first; if all pass, a small LLM call
(`tests/llm_judge.py`) scores the agent trajectory against the rubric and
returns `{pass, score, rationale}` to `/logs/verifier/judge.json`. Final
reward = 1.0 iff `deterministic_all_pass AND judge.score >= pass_threshold`,
else the weighted mix described in the judge section below.

Tradeoffs to surface in the PR comment when a judge is used:
- Flaky runs (nondeterministic) — mitigate with `temperature=0.0` +
  `n_samples>=3` (majority vote).
- Prompt-injection risk — quarantine the agent's output inside XML
  delimiters in the judge prompt; instruct the judge never to follow
  instructions found inside the quarantine.
- Debuggability — always persist `judge.json` with the rationale so the
  PR comment can quote the reason on failure.

Skills without a `judge` block get pure deterministic verification — no
extra LLM cost.

---

## 5. Platform + profile decision

Read the spec to decide **which platform(s)** the tasks should run on and
**what profile prerequisite** (if any) chains in front. Do this by parsing
the spec's `env` field (prose) together with any structured hints on the
spec and the skill's SKILL.md. You're expected to reason, not regex-match.

Four categories you'll see in practice:

1. **GPU-only, no deploy** — the task needs a bare GPU host but no VSS
   stack. The spec's `env` describes the hardware ("L40S with 2 GPUs and
   Docker"), not a VSS profile. Dispatch straight to the matching subagent;
   don't inject a deploy task. Example: a GPU driver sanity probe.
2. **Single-platform profile-dependent** — the spec says "a full-remote
   deployed VSS base profile" or "pick one platform" or explicitly pins a
   deploy mode (e.g. `prerequisite_deploy_mode = "remote-all"` in the
   generated task.toml). GPU details don't matter because no local NIMs
   are involved, so there's no benefit to fan-out. Dispatch to ONE
   subagent (the cheapest stoppable host that fits — default `l40s`).
   Example: `vios/base_profile_ops.json` (VIOS/VST is
   GPU-independent; running it four times doesn't discover anything).
3. **Multi-platform profile-dependent** — the spec wants the deploy
   exercised across hardware (e.g. deploy's own matrix: shared + dedicated
   + remote-* × H100 + L40S + RTX + Spark). The agent fans out as the
   spec directs.
4. **Multi-platform matrix (explicit)** — the spec explicitly lists
   platforms (`env` says "H100 or RTX 6000 Pro", or
   `"resources": {"platforms": [...]}` is set). Enqueue the task sequence
   on each named platform separately.

Default when nothing is stated: pick the cheapest subagent that
physically fits (today: `l40s`) — and add a "Subagent suggestions" line
in the PR comment asking the skill author to tighten the spec's `env` to
state platform intent explicitly. "Physical fit" means the spec's
resource hints (`min_vram_gb_per_gpu`, `min_root_disk_gb`, ARM64 support
when NIM images are x86-only) all fit the chosen subagent's host.

If the spec is truly ambiguous (e.g. "a GPU host" with no driver floor
and no profile dependency): go with `l40s` + suggestion line.

### Task sequencing within a spec

A single `<skill>/eval/<profile>.json` may contain multiple entries in
`expects[]` (or equivalent list). Treat each entry as **one queue task**,
not a sub-check of a monolithic task:

- Task N+1's `requires_previous_passed` = task N's id (within the same
  platform queue).
- All tasks for one spec × one platform share the same `pr_number`,
  `eval_spec_path`, `eval_spec_sha`, so the results monitor can group
  them into a single PR-comment batch.
- If task N fails, tasks N+1..end get `status="blocked"` with
  `error_notes="predecessor <id> failed"` — they don't retry.

### Building the full enqueue for one spec × one platform

Given a spec with K `expects[]` entries dispatched to platform P:

1. If the spec declares profile prerequisite `<profile>`, prepend one
   deploy task:
   ```
   id=uuid-1, skill="deploy", profile="<profile>", platform=P, task_id="<P-short>-<mode>"
   ```
   Mode is chosen per platform from the deploy adapter's supported modes
   (`shared` when a single GPU fits both NIMs, `remote-all` on 48 GB L40S,
   `spark-shared` Edge 4B on SPARK, etc.).
2. Emit K skill tasks, each chained to the previous (first one chained to
   the deploy task if present, else null):
   ```
   id=uuid-k, skill="<skill>", platform=P, task_id="<skill-task-id>-step-<k>",
   requires_previous_passed=uuid-(k-1)
   ```
3. Append them as a contiguous block to the platform's queue JSON.

For each selected platform, append the entire chained task sequence to that
subagent's queue. Each task entry:

```json
{
  "id": "<uuid>",
  "pr_number": <N>,
  "pr_head_sha": "<sha>",
  "pr_url": "https://github.com/<org>/<repo>/pull/<N>",
  "skill": "<name>",
  "profile": "<profile or null>",
  "profile_dependency": "<prerequisite profile name or null>",
  "platform": "<PLATFORM>",
  "dataset_dir": "tools/eval/harbor/datasets/<skill>/<profile>/<platform>",
  "task_id": "step-<k>",
  "step_index": <k>,
  "step_count": <K>,
  "query_summary": "<first ~80 chars of this expects[k-1].query>",
  "requires_deployed_vss": true | false,
  "requires_previous_passed": "<uuid of prior task or null>",
  "eval_spec_path": "skills/<skill>/eval/<profile>.json",
  "eval_spec_sha": "<blob-sha of the spec at pr_head_sha>",
  "adapter_sha_before": "<sha of generate.py before regeneration, or null if new>",
  "adapter_sha_after":  "<sha of generate.py after regeneration>",
  "status": "pending",
  "added_at": "<utc-iso>",
  "started_at": null,
  "finished_at": null
}
```

`eval_spec_sha` and `adapter_sha_before/after` let the coordinator decide
whether a PR is a genuine test change, a rerun of an unchanged test, or an
adapter-only refactor — which in turn drives the tone of the result
comment (§ 7).

If the skill chains behind another (e.g., `env.prerequisite_skill = "deploy"`),
also append the paired deploy task **first** with a matching `id`, and set
`requires_previous_passed = <deploy.id>` on the downstream task. Subagents
enforce that gate before dispatching.

---

## 6. Subagent lifecycle

Each subagent is a **batch-scoped process** the coordinator spawns when a
platform queue has pending tasks. It owns exactly one queue file
(`/tmp/subagents/<name>.json`) and one Brev instance. It exits at the next
batch boundary (see step 10 below); the coordinator respawns it if more
batches remain. The Brev instance stays up across respawns — instance
teardown is a queue-drained concern only.

### Env sourcing (one-time, per subagent process)

Before the first harbor invocation, each subagent must load the coordinator's
`.env` and propagate the git-identity vars as `GIT_AUTHOR_*` /
`GIT_COMMITTER_*`:

```bash
set -a; source /home/ubuntu/eval-coordinator/.env; set +a
export GH_TOKEN="$GITHUB_TOKEN"
export GIT_AUTHOR_NAME="$GIT_USER_NAME"     GIT_AUTHOR_EMAIL="$GIT_USER_EMAIL"
export GIT_COMMITTER_NAME="$GIT_USER_NAME"  GIT_COMMITTER_EMAIL="$GIT_USER_EMAIL"
cd /home/ubuntu/video-search-and-summarization
```

The coordinator's `.env` must have:
- `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` — for the
  claude-code agent inside the harbor trial.
- `NGC_CLI_API_KEY`, `HF_TOKEN`, `LLM_REMOTE_URL` / `LLM_REMOTE_MODEL`,
  `VLM_REMOTE_URL` / `VLM_REMOTE_MODEL` — forwarded to the Brev instance
  by `BrevEnvironment`.
- `GITHUB_TOKEN` — for the coordinator's `gh` calls (PR comments, adapter
  PRs). `GH_TOKEN` is the var `gh` actually consults; we alias.
- `GIT_USER_NAME` / `GIT_USER_EMAIL` — author identity for commits the
  coordinator raises in § 8. The host's git config has no
  `user.name` / `user.email` by policy; we propagate identity via
  per-process env vars instead.

Skeleton at [`tools/eval/harbor/.env.example`](.env.example). If any of
`ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL` /
`GITHUB_TOKEN` / `GIT_USER_NAME` / `GIT_USER_EMAIL` is empty after sourcing,
the subagent must mark every task it picks up as `status="blocked"` with
`error_notes="missing env — .env not configured"`, flag the coordinator
(a new entry in `/tmp/subagents/<name>.json.alerts[]`), and stop
processing until the coordinator confirms `.env` is fixed. Do NOT try to
guess keys or run harbor without them — the harbor trial will hang without
a clear error.

### Trial-side env: `CLAUDE_CODE_DISABLE_THINKING=1` (always on)

`BrevEnvironment.start()` unconditionally writes
`CLAUDE_CODE_DISABLE_THINKING=1` into the instance's `~/.eval_env`
(see `tools/eval/harbor/envs/brev_env.py`). Leave it on by default.

**Why:** claude-code 2.1.x emits a `context_management: {"edits":
[{"type": "clear_thinking_20251015", ...}]}` field in every
`POST /v1/messages` body to drive server-side thinking-block cleanup.
The NVIDIA-hosted Anthropic-compatible proxy we route through
(`${ANTHROPIC_BASE_URL}/v1`, set by harbor's `--ak api_base=...`) rejects
the field with HTTP 400 `context_management: Extra inputs are not permitted`.
`CLAUDE_CODE_DISABLE_THINKING=1` is the only CLI toggle confirmed to
strip the field at the client — `DISABLE_AUTO_COMPACT` / beta header
tweaks do not. The trade-off is that trials lose extended thinking; our
deploy/vios evaluations are procedural enough that this is fine.

**When to revisit:** when the NVIDIA proxy upgrades to accept
`context_management`, or when we move trials to direct Anthropic auth
(no proxy). At that point delete the entry in `brev_env.py`'s
`forwarded` list. Do NOT remove it speculatively — a failing
`HTTP 400 context_management` in `/logs/agent/` is the only reliable
signal that the fix is still needed.

**Sanity-check the inference-hub wiring.** Run this from the
coordinator host (or from any subagent's instance) when trials start
failing with 0 turns / exit 1 and you want to confirm the API path
itself is alive before blaming the spec:

```bash
set -a && source /home/ubuntu/eval-coordinator/.env && set +a && \
CLAUDE_CODE_DISABLE_THINKING=1 claude -p "reply with ok" \
  --model "$ANTHROPIC_MODEL"
```

Expected: prints `ok`, exits 0 in under a few seconds. Interpretation:

- `ok` + exit 0 → the NVIDIA proxy path works; the failure is in the
  spec, dataset, or agent logic. Don't touch `brev_env.py`.
- `HTTP 400 ... context_management` → the `DISABLE_THINKING` env var
  didn't reach claude-code; confirm `brev_env.py`'s `forwarded` list
  still hardcodes it and that `~/.eval_env` is being sourced.
- `HTTP 401` with a different model alias → the `ANTHROPIC_API_KEY` is
  scoped to one alias only (`aws/anthropic/bedrock-claude-sonnet-4-6`
  today). Use `$ANTHROPIC_MODEL` from `.env`, don't hardcode a guess.
- Network / DNS error → proxy is down; alert, don't dispatch trials.

### Subagent loop

1. Read its queue file. Find the first `status=pending` task whose
   `requires_previous_passed` (if set) resolves to a completed `passed` entry
   in `results`.
2. If the Brev instance isn't ready (stopped / missing), start or provision
   it. For `spark`, verify SSH reachability via `ssh spark hostname`.
3. Atomic-update the task to `status=in_progress`, stamp `started_at`.
4. Tear down any stale state on the instance **only if this is a deploy
   task**. If the task is downstream of a deploy that just passed on this
   same instance, skip teardown — the downstream task depends on the live
   stack.
5. Run harbor (env already sourced above — `$ANTHROPIC_*` are live):
   ```
   BREV_INSTANCE=<instance> uvx harbor run \
     --environment-import-path tools.eval.harbor.envs.brev_env:BrevEnvironment \
     -p <dataset_dir> -i <task_id> \
     -a claude-code -n 1 \
     -o tools/eval/harbor/results/<run_id> \
     --timeout-multiplier 6 --max-retries 0 \
     --model "$ANTHROPIC_MODEL" \
     --ak "api_base=${ANTHROPIC_BASE_URL}/v1"
   ```
6. Read `verifier/reward.txt` and `verifier/test-stdout.txt`. Task passes
   iff `reward == 1.0` AND stdout contains `"0 failed"`.
7. Append to the queue file's `results[]`:
   ```json
   {
     "id": "<uuid>",
     "task_id": "<platform-short-name>",
     "pr_number": <N>,
     "status": "passed" | "failed" | "blocked",
     "reward": 1.0,
     "checks_passed": 15,
     "checks_total": 15,
     "result_path": "tools/eval/harbor/results/_viewer/<run_id>__<date>/<trial_name>",
     "harbor_trace_url": "https://harbor-<env_id>.brevlab.com/jobs/<run_id>__<date>/tasks/<source>/<agent>/<model_provider>/<url_encoded_model_name>/<url_encoded_task_name>",
     "attempts": 1,
     "started_at": "<utc-iso>",
     "finished_at": "<utc-iso>",
     "duration_sec": 823,
     "error_notes": "<short description or null>",
     "failed_checks": ["imageUrl does not match Brev secure-link pattern", "..."],
     "suggestion": "<1-2 sentence natural-language observation for the coordinator>"
   }
   ```
   `failed_checks` mirrors the `FAIL:` lines from `test-stdout.txt` so the
   coordinator can include them verbatim in the PR comment. `suggestion`
   is a short free-text hint the subagent writes when it spots an actionable
   issue (e.g. "the spec's `min_root_disk_gb` is 80 but the deploy needs
   ~130 GB of image pulls — consider raising it"). **Always include these
   fields even on success** (`failed_checks=[]`, `suggestion=null`).
8. Budget: 60 min per deploy task, 30 min per downstream task. On timeout
   kill harbor, mark `failed` with `error_notes="timeout"`, and move on.
9. Max 3 attempts per task. If all fail, `status=failed` (not blocked).
10. **Exit at batch boundaries.** A batch = all tasks sharing
    `(pr_number, eval_spec_path)`. After step 9, if the next pending task in
    the queue belongs to a different batch — or no pending tasks remain —
    remove the pidfile and exit. Do NOT idle-loop. The coordinator respawns
    a fresh subagent on the same platform if more batches are pending; the
    Brev instance stays up so respawn is cheap. Rationale: this gives the
    coordinator a low-latency completion signal per batch so it can post
    the §7 PR comment within seconds, instead of polling `results[]` growth.

### When the queue drains

A subagent always exits at a batch boundary (step 10). The coordinator
respawns it as long as more batches are pending. When the coordinator
observes `len(pending) + len(in_progress) == 0` across a queue AND no new
tasks have been appended in the last 5 min, it declares the queue drained
and issues instance shutdown itself:

- **SPARK** — no-op (BYOH, see §10). The queue stays; the coordinator will
  respawn a subagent when future tasks arrive.
- **L40S, RTX 6000 Pro** — coordinator calls `brev stop <instance>`.
- **H100** (non-stoppable) — coordinator calls `brev delete <instance>`.

Subagents never shut down instances between batches on the same queue.
Teardown is strictly a queue-drained concern, and only the coordinator
decides when the queue is drained.

---

## 7. Results workflow

The results monitor watches the four queue JSONs for growth in `results[]`.
When a new result appears:

1. Cross-reference the result `id` with `_prs_seen.json` to find the PR and
   eval-spec batch it belongs to.
2. Append a per-task line to the PR's pending comment draft.
3. If this result completes the batch (all tasks in
   `_prs_seen.json[<pr>].batches[<spec>]` have results), finalize the comment
   and post it.

### Result comment format

Post once per eval-spec batch (not per task). Example body:

```markdown
## Harbor Eval — `skills/vios/eval/base_profile_ops.json`

Head: `<sha>` · 3 queries × up to 15 checks · dispatched to 4 platforms
Queued at `<utc-iso>` · first finished at `<utc-iso>` · last finished at `<utc-iso>`

| Platform | Result | Reward | Duration | Trace |
|---|---|---|---|---|
| L40S | ✅ passed | 1.0 (15/15) | 13m 42s | [traces](https://harbor-8yq51k0qt.brevlab.com/jobs/vios-base-l40s-1__2026-04-20__05-13-22/tasks/l40s/claude-code/aws/anthropic%2Fbedrock-claude-sonnet-4-6/nvidia-vss%2Fvios-base-l40s-step-2) |
| H100 | ✅ passed | 1.0 (15/15) | 11m 05s | [traces](https://harbor-8yq51k0qt.brevlab.com/jobs/vios-base-h100-1__2026-04-20__05-17-01/tasks/h100/claude-code/aws/anthropic%2Fbedrock-claude-sonnet-4-6/nvidia-vss%2Fvios-base-h100-step-2) |
| RTX 6000 Pro | ❌ failed | 0.87 (13/15) | 14m 23s | [traces](https://harbor-8yq51k0qt.brevlab.com/jobs/vios-base-rtx-1__2026-04-20__05-21-44/tasks/rtx/claude-code/aws/anthropic%2Fbedrock-claude-sonnet-4-6/nvidia-vss%2Fvios-base-rtx-step-2) |
| DGX Spark | ✅ passed | 1.0 (15/15) | 18m 17s | [traces](https://harbor-8yq51k0qt.brevlab.com/jobs/vios-base-spark-1__2026-04-20__05-30-19/tasks/spark/claude-code/aws/anthropic%2Fbedrock-claude-sonnet-4-6/nvidia-vss%2Fvios-base-spark-step-2) |

### Failing checks (RTX 6000 Pro)

- imageUrl does not match Brev secure-link pattern (got `http://localhost:30888/...`)
- Content-Length 847 below 2000-byte minimum for snapshot JPEG

### Subagent suggestions

> **RTX 6000 Pro (failed):** The spec's Brev-link check assumes
> `BREV_LINK_PREFIX` is exported. On `vss-eval-rtx` this env var is missing
> from `/etc/environment` — consider documenting the setup step in the spec's
> `env.description` field, or loosening the check to skip when
> `BREV_LINK_PREFIX` is unset (see how `test_base_profile_ops.py` already
> does this).

<sub>This comment is generated by the eval coordinator. Adapter changes
(if any) will land in a follow-up PR (`feat/eval-adapter-<N>-<sha>`) once
the batch finishes. The coordinator never edits `skills/` — treat
suggestions above as input for the skill author to act on.</sub>
```

The per-task fields (`failed_checks`, `suggestion`, `duration_sec`, etc.)
come directly from `results[]` entries written by the subagents (§ 6). The
coordinator concatenates non-null `suggestion`s into the "Subagent
suggestions" section; omit the section entirely if all suggestions are
null.

Post with:
```bash
gh pr comment <PR_NUMBER> --body-file <tmp_body>
```

### Harbor trace URL construction

Trace links live on the coordinator host (`vss-skill-validator`, env id
`8yq51k0qt`, harbor viewer on port 8080). The Brev secure-link pattern for
a named service (not a numbered port) is:

```
https://<service-name>-<BREV_ENV_ID>.brevlab.com/<path>
```

`<service-name>=harbor` for the Harbor viewer.

**Layout mismatch and the `_viewer/` symlink pattern.** `uvx harbor view`
indexes exactly one folder and expects `<folder>/<job>/<trial>` (two
levels). `uvx harbor run -o results/<run_id>` writes
`results/<run_id>/<date>/<trial>` (three levels — harbor auto-appends its
own dated subdir). Pointing the viewer at `results/` therefore shows every
`<run_id>` as an empty top-level entry.

Fix (non-destructive): maintain an aggregation folder
`tools/eval/harbor/results/_viewer/` containing symlinks named
`<run_id>__<date>` that point at `../<run_id>/<date>`. After every
completed harbor trial the coordinator adds one symlink:

```bash
cd tools/eval/harbor/results
ln -sfn "../<run_id>/<date>" "_viewer/<run_id>__<date>"
```

The viewer runs once per host, detached, pointed at `_viewer`:

```bash
cd /home/ubuntu/video-search-and-summarization
nohup uvx harbor view tools/eval/harbor/results/_viewer --jobs \
    --host 0.0.0.0 --port 8080 > /tmp/subagents/harbor-view.log 2>&1 &
disown
```

**Trace URL template.** The viewer's React SPA route shape is **not**
the same as its JSON API route shape. Building a frontend URL from the
API shape gives a 404 even when the API endpoint returns 200. The SPA
requires:

```
https://harbor-<BREV_ENV_ID>.brevlab.com/jobs/<run_id>__<date>/tasks/<source>/<agent>/<model_provider>/<model_name>/<task_name>
```

Write exactly this form into each result entry's `harbor_trace_url` and
into the §7 PR-comment link column. Under `--max-retries 0` there is
one trial per task, so landing on the task page is equivalent to
landing on the trial page — the `/trials/<trial_name>` suffix is
redundant and not needed (the task page auto-focuses the one trial).

Where:
- `<BREV_ENV_ID>` — read dynamically from `/etc/environment`; never hardcode.
- `<run_id>` — the coordinator-chosen `-o` dir, e.g. `l40s-vios-step2-20260421-225103`.
- `<date>` — the dated subdir harbor auto-creates, format `YYYY-MM-DD__HH-MM-SS`.
- `<source>`, `<agent>`, `<model_provider>`, `<model_name>`, `<task_name>` —
  read from `GET /api/jobs/{job}/tasks`'s response (`items[0]` when one
  task per job, as with our single-trial runs):
  - `source` — the `source` field (e.g. `l40s`, `base`)
  - `agent` — e.g. `claude-code`
  - `model_provider` — e.g. `aws`
  - `model_name` — e.g. `anthropic/bedrock-claude-sonnet-4-6`
  - `task_name` — e.g. `nvidia-vss/vios-base-l40s-step-2`

**URL-encode slashes** inside `model_name` and `task_name` (each has a
`/` that must become `%2F`, otherwise the SPA's path matcher treats
them as extra segments and 404s). Python one-liner:

```python
from urllib.parse import quote
model = quote("anthropic/bedrock-claude-sonnet-4-6", safe="")
task  = quote("nvidia-vss/vios-base-l40s-step-2",    safe="")
```

**Fallback if you don't have task metadata**: link the job overview
`https://harbor-<BREV_ENV_ID>.brevlab.com/jobs/<run_id>__<date>`. The
reader lands on the tasks table for that run and clicks through. Cheap
but less precise.

**API routes** (useful for programmatic probes — same host, same
`_viewer`-rooted folder, but a completely separate route tree from the
SPA):

- `GET /api/jobs` — paginated list, `items[].name` = `<run_id>__<date>`
- `GET /api/jobs/{job}` — job summary (returns 400 "Invalid job name"
  if the entry under `_viewer/` is a symlink — use `mv` not `ln -sfn`,
  see "Fix" above)
- `GET /api/jobs/{job}/tasks` — the metadata you need to construct the
  SPA URL (`source`, `agent_name`, `model_provider`, `model_name`,
  `task_name`)
- `GET /api/jobs/{job}/trials/{trial}` — raw trial detail (works under
  the symlink too; this is why an earlier wrong URL template looked
  "right" when probed via `/api/` but 404'd on the SPA)
- `GET /api/jobs/{job}/trials/{trial}/{trajectory|verifier-output|agent-logs|files|artifacts}`

---

## 8. Final PR with adapter changes

When every eval spec batch for a given PR has been processed (pass or fail
doesn't matter — we still want the adapter committed so future CI can rerun
it), raise a PR from this repo to upstream the adapter:

1. `git checkout -b feat/eval-adapter-<N>-<short-sha>`
2. `git add tools/eval/harbor/adapters/<skill>/generate.py` (and any new
    `__init__.py`). Also add any probe/spec files you copied that don't yet
    live in the skills tree.
3. Do **not** commit `tools/eval/harbor/datasets/` (gitignored) or
   `tools/eval/harbor/results/` (gitignored) or the per-platform queue
    JSONs (ephemeral under `/tmp`).
4. `git commit -m "eval adapter: add coverage for skills/<name> (PR #<N>)"`
   with a body that includes a link to the source PR.
5. `gh pr create --base develop --head feat/eval-adapter-<N>-<short-sha>`
   with title `eval adapter for skills/<name> (via #<N>)`.
6. Post a follow-up comment on the source PR linking the adapter PR.

---

## 9. State management & restartability

Everything survives a coordinator restart by design:

- `/tmp/subagents/_prs_seen.json` — PR → (last-seen SHA, batches, draft
  comment) map. Rebuilt from scratch if missing (re-fetches open PRs).
- `/tmp/subagents/<platform>.json` — queue + results per subagent. Subagents
  resume from the first pending task after restart.
- `tools/eval/harbor/results/<run_id>/` — harbor artifacts on disk.
- GitHub itself — source of truth for what PRs are open.

On startup:

1. `mkdir -p /tmp/subagents`
2. Seed missing queue files.
3. Reconcile: for every PR returned by `gh pr list --base develop`, if
   `_prs_seen.json` doesn't have a matching head SHA, treat it as a fresh
   PR event (§ 3 again).
4. Spawn the four subagents (or reattach if they're already alive — check
   pid files `/tmp/subagents/<name>.pid`).

---

## 10. What NOT to do

- **NEVER modify `skills/`.** The `skills/` tree is human-owned; only skill
  authors touch it. If you notice an eval spec is wrong, a probe is broken,
  a reference doc is stale, or a check would be clearer with a different
  regex — **post a comment on the source PR with the suggestion**. Do not
  edit the file and do not include the edit in your adapter PR. Your
  `feat/eval-adapter-*` PR must only touch `tools/eval/harbor/adapters/`
  (+ gitignore / `tools/eval/harbor/AGENTS.md` if applicable).
- **NEVER process source branches (e.g. `feat/foo`) directly.** Only
  `pull-request/<N>` mirror SHAs represent CPR-vetted code. NVIDIA's
  copy-pr-bot (see [vetters doc](https://docs.gha-runners.nvidia.com/cpr/vetters))
  requires a trusted vetter to `/ok to test` each new commit SHA before
  the mirror is updated; the mirror lag is the security boundary between
  untrusted contributor code and NVIDIA infrastructure (Brev runners,
  remote endpoints, secrets in `.env`). If the source PR head is ahead
  of its mirror, **wait** — do not bypass the mirror by diffing the
  source branch, diffing `pr.headRefOid`, or running harbor against the
  source SHA. Surface the lag in a PR comment if it's blocking progress
  so the skill author can request re-vetting; never work around it. This
  is the single non-negotiable rule of the coordinator.
- Don't commit datasets, results, or queue JSONs to the skill repo.
- Don't run evals yourself — always dispatch through the per-platform queue.
- Don't skip a platform a spec asked for. When a spec declares a platform
  set (via its `env` matrix narrative or `resources.platforms`), dispatch
  to every platform it names — all four (L40S, H100, RTX 6000 Pro, SPARK)
  are first-class, none get dropped for convenience or cost. Platform-
  specific lifecycle rules (below) don't change dispatch behavior.
  **Specs that make no platform claim do NOT default to all four** —
  they fall through to §5's default (one platform, cheapest fit, plus a
  "Subagent suggestions" line in the PR comment asking the author to
  tighten the spec's `env`). This matters: many skill evals (`vios`,
  `video-summarization`, `video-search`) are hardware-agnostic and
  *explicitly* ask for one-platform dispatch in their `env`; auto-fanning
  them would quadruple cost without discovering anything. The deploy
  skill's own matrix (`skills/deploy/eval/*.json`) is the canonical
  multi-platform case — it declares platform × mode coverage and §5 case
  3 governs its fan-out.
- Instance lifecycle rules per platform:
  - **L40S, RTX 6000 Pro** — stoppable. `brev stop` after the queue drains.
  - **H100** (dmz.h100x2.pcie) — non-stoppable. `brev delete` after the
    queue drains to stop billing.
  - **SPARK** — BYOH registered node. Never `brev stop`, never `brev delete`,
    never modify its lifecycle. It stays online across runs.
  - **Pre-existing non-eval instances** (`vss-skill-validator` — the host
    this coordinator runs on) — never touch.
- Don't force-push to `develop`, and don't merge your own adapter PRs —
  human review required.
- Don't leak `HF_TOKEN` / `ANTHROPIC_API_KEY` into PR comments or commit
  messages.
- Don't include screenshots / binary diffs in PR comments — traces are
  linked, not embedded.

---

## 11. Minimal end-to-end sanity check

Before going autonomous, walk through this once by hand to confirm the
playbook wiring:

1. Create a dummy PR from `pull-request/sanity-<date>` that touches
   `skills/vios/eval/base_profile_ops.json` with a trivial whitespace
   change.
2. Verify PR monitor detects it within 60 s.
3. Verify adapter regenerates and dataset tree appears under
   `tools/eval/harbor/datasets/vios/base/`.
4. Verify the right task sequence appears on the right queues:
   - `vios/base_profile_ops.json` targets every platform capable of
     running the `base` profile (per spec's env). For each such platform,
     the queue should receive:
       - 1 deploy task (profile=`base`) — first
       - K vios tasks (K = `len(expects)` = 3 today) — chained via
         `requires_previous_passed`
   - Queues for platforms the spec doesn't target must stay unchanged.
5. Verify subagents walk the chain in order, blocking downstream tasks on
   predecessor failure.
6. Verify the PR gets a single comment per (spec × platform) batch with a
   step-by-step results table and trace links.
7. Verify the adapter-change PR is raised against `develop` on branch
   `feat/eval-adapter-<N>-<sha>` and touches only
   `tools/eval/harbor/adapters/<skill>/`.

Only after all seven steps pass do you start acting on real PRs.
