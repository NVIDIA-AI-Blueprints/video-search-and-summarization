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
`eval/*.json` specs. For each spec, generate a matching Harbor adapter under
`tools/eval/harbor/adapters/<skill>/`, append one eval task per applicable
platform to the four per-platform subagent queues, wait for subagents to
report back, post the results (with a Harbor trace URL) as a comment on the
PR, and finally raise your own PR carrying the adapter code changes. Then
loop.

You do **not** run evals yourself. You do **not** maintain plan JSON files —
the per-platform subagent queues replace them.

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

1. **PR monitor** — polls `gh pr list --base develop --json number,headRefName,updatedAt`
   every 60 s. Filters `headRefName` matching `pull-request/*`. Tracks last-seen
   commit SHA per PR in `/tmp/subagents/_prs_seen.json` to detect new commits
   on existing PRs.
2. **Results monitor** — watches `/tmp/subagents/*.json` for new entries in
   the `results` array (tasks a subagent finished). Each new result triggers
   a PR comment and, if the PR's entire task batch is now done, a final PR
   to upstream the adapter changes.

Run both monitors concurrently (two nested poll loops in a single process,
or two long-running `Monitor` background tasks if available to you).

---

## 3. PR-event workflow

Fire whenever the PR monitor observes a new PR or a new commit on an
already-tracked PR whose base is `develop` and whose head branch matches
`pull-request/*`.

### Steps

1. **Fetch the PR diff:**
   ```bash
   gh pr diff <PR_NUMBER> --name-only
   ```

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
   - Generate/refresh the adapter at
     `tools/eval/harbor/adapters/<name>/generate.py` — see **§ 4**.
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

You regenerate `tools/eval/harbor/adapters/<skill>/generate.py` every time
the skill's eval spec changes. Pattern-match from the two existing adapters:

- **`tools/eval/harbor/adapters/deploy/generate.py`** — matrix generator
  (platform × mode). Only use this shape if the skill's spec declares a
  matrix (e.g., `resources.modes`).
- **`tools/eval/harbor/adapters/sensor-ops/generate.py`** — single-task-
  per-platform generator. This is the default shape for skills whose spec
  has a flat `expects[]` list.

The adapter must emit, per platform it supports:

```
datasets/<skill>/<profile>/<platform>/
  task.toml         # [metadata] with gpu_type, brev_search, min_vram_gb_per_gpu,
                    #            min_root_disk_gb, min_gpu_driver_version,
                    #            requires_deployed_vss (if applicable)
  instruction.md    # derived from the spec's queries + env notes
  tests/test.sh     # invokes the skill's probe; tallies PASS/FAIL → /logs/verifier/reward.txt
  tests/<probe>.py  # copied from skills/<skill>/scripts/<probe>.py if the spec references one
  tests/<spec>.json # copied from skills/<skill>/eval/<profile>.json
  solution/solve.sh # gold-standard or no-op
  skills/<skill>/   # full skill copy so the agent has it at runtime
  skills/deploy/    # include if requires_deployed_vss=true (agent can diagnose)
  environment/Dockerfile   # FROM scratch (BrevEnvironment takes over)
```

Probe authoring heuristic: if the spec's `checks` can all be evaluated via
shell one-liners, the test.sh inlines them. If any check requires stateful
logic (regex on Brev links, HEAD `Content-Type` probing, JSON traversal),
the skill must ship a Python probe under `scripts/` and the adapter copies
it into each task's `tests/` dir.

After generating, commit nothing yet — § 8 handles the PR.

---

## 5. Platform dispatch decision

Read the skill's eval spec to pick the subset of platforms:

- If the spec has `"resources": {"platforms": [...]}` — use exactly that list.
- If the spec declares `"requires_deployed_vss": true` — dispatch to every
  platform that can actually run a VSS deploy for the named `prerequisite_profile`
  (today: `l40s`, `h100`, `rtx`, `spark`).
- If the spec has no platform constraints — dispatch to all four.

For each selected platform, append a task entry to that subagent's queue:

```json
{
  "id": "<uuid>",
  "pr_number": <N>,
  "head_sha": "<sha>",
  "skill": "<name>",
  "profile": "<profile>",
  "platform": "<PLATFORM>",
  "dataset_dir": "tools/eval/harbor/datasets/<skill>/<profile>",
  "task_id": "<platform-short-name>",
  "requires_deployed_vss": true | false,
  "status": "pending",
  "added_at": "<utc-iso>"
}
```

If the skill chains behind another (e.g., `env.prerequisite_skill = "deploy"`),
also append the paired deploy task **first** with a matching `id`, and set
`requires_previous_passed = <deploy.id>` on the downstream task. Subagents
enforce that gate before dispatching.

---

## 6. Subagent lifecycle

Each subagent is a long-running process you spawned at startup. It owns
exactly one queue file (`/tmp/subagents/<name>.json`) and one Brev instance.

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
5. Run harbor:
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
     "status": "passed" | "failed" | "blocked",
     "reward": 1.0,
     "result_path": "tools/eval/harbor/results/<run_id>/<task_id>__<hash>",
     "finished_at": "<utc-iso>",
     "error_notes": "<short description or null>"
   }
   ```
8. Budget: 60 min per deploy task, 30 min per downstream task. On timeout
   kill harbor, mark `failed` with `error_notes="timeout"`, and move on.
9. Max 3 attempts per task. If all fail, `status=failed` (not blocked).
10. On SPARK: **never** stop/delete the instance. On the others:
    — stoppable → `brev stop` once the queue is empty.
    — non-stoppable (h100 dmz) → `brev delete` once empty.
    Don't delete the instance between tasks in the same queue.

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
## Harbor Eval — `skills/sensor-ops/eval/base_profile_ops.json`

Head: `<sha>` · 3 queries × up to 15 checks · dispatched to 4 platforms

| Platform | Result | Reward | Trace |
|---|---|---|---|
| L40S | ✅ passed | 1.0 (15/15) | [traces](https://harbor-8yq51k0qt.brevlab.com/jobs/2026-04-20__05-13-22) |
| H100 | ✅ passed | 1.0 (15/15) | [traces](https://harbor-8yq51k0qt.brevlab.com/jobs/2026-04-20__05-17-01) |
| RTX PRO 6000 | ❌ failed | 0.87 (13/15) | [traces](https://harbor-8yq51k0qt.brevlab.com/jobs/2026-04-20__05-21-44) — *imageUrl not Brev-formatted* |
| DGX Spark | ✅ passed | 1.0 (15/15) | [traces](https://harbor-8yq51k0qt.brevlab.com/jobs/2026-04-20__05-30-19) |

<sub>Adapter changes will land in a follow-up PR once this batch is clean.</sub>
```

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

For this topology: `<service-name>=harbor` and the path is the relative
result directory harbor wrote to, i.e.
`jobs/<run_id_dir>/<task_id>__<hash>`. Full URL:

```
https://harbor-8yq51k0qt.brevlab.com/jobs/<run_id>/<task_id>__<hash>
```

Resolve `BREV_ENV_ID` dynamically from `/etc/environment` — do NOT hardcode
`8yq51k0qt`; a different deployment of this coordinator would have a
different id.

---

## 8. Final PR with adapter changes

When every eval spec batch for a given PR has been processed (pass or fail
doesn't matter — we still want the adapter committed so future CI can rerun
it), raise a PR from this repo to upstream the adapter:

1. `git checkout -b coordinator/adapter-pr-<N>-<short-sha>`
2. `git add tools/eval/harbor/adapters/<skill>/generate.py` (and any new
    `__init__.py`). Also add any probe/spec files you copied that don't yet
    live in the skills tree.
3. Do **not** commit `tools/eval/harbor/datasets/` (gitignored) or
   `tools/eval/harbor/results/` (gitignored) or the per-platform queue
    JSONs (ephemeral under `/tmp`).
4. `git commit -m "eval adapter: add coverage for skills/<name> (PR #<N>)"`
   with a body that includes a link to the source PR.
5. `gh pr create --base develop --head coordinator/adapter-pr-<N>-<short-sha>`
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

- Don't commit datasets, results, or queue JSONs to the skill repo.
- Don't run evals yourself — always dispatch through the per-platform queue.
- Don't write plan JSONs (the old `tools/eval/harbor/plans/` concept is
  abandoned). If you see existing plan files, leave them alone but never
  create new ones.
- Don't stop / delete SPARK. BYOH.
- Don't stop / delete other pre-existing Brev instances (`vss-skill-validator`
  itself lives on one).
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
   `skills/sensor-ops/eval/base_profile_ops.json` with a trivial whitespace
   change.
2. Verify PR monitor detects it within 60 s.
3. Verify adapter regenerates and dataset tree appears under
   `tools/eval/harbor/datasets/sensor-ops/base/`.
4. Verify four task entries appear (one per platform) across the queue
   JSONs.
5. Verify subagents pick them up and post results (expect 4 passes if
   nothing has changed in the skill).
6. Verify the PR gets a comment with a 4-row table and trace links.
7. Verify the adapter-change PR is raised against `develop`.

Only after all seven steps pass do you start acting on real PRs.
