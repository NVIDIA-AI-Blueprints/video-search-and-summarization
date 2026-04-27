# Stale-marker auto-deploy fix + multi-instance selection

Status: accepted (2026-04-27)
Owner: skill-eval-main
Related: multi-agent.spec (separate concern)

## Context

Two related problems with the pre-deploy hook added to
`BrevEnvironment._ensure_prerequisite_deployed`:

**(1) Stale-marker silent skip.** Markers were per-(profile, mode) flag
files (`/tmp/skill-eval/deployed-<profile>-<mode>.flag`) that accumulated
as a deploy log instead of tracking what is currently running. Sequence:
`deploy/base` writes `deployed-base-remote-all.flag`, then `deploy/search`
tears down base + brings up search but the base flag stays on disk. A
subsequent `vios` trial requiring base sees the stale flag, skips
pre-deploy, and runs against search — silent wrong answer. The invariant
we wanted but didn't enforce: *the marker is what is currently RUNNING on
this box, not what has at any point been deployed here.*

**(2) Single-box bottleneck.** The orchestrator hardcodes
`BREV_INSTANCE=vss-eval-l40s` (one box per platform). Concurrent CI runs
serialize on this one box's flock — even when more `vss-eval-l40s-*`
boxes exist or could be added. The harness has no selection logic; it
just trusts the env var.

The two are related because the marker concept naturally extends per-box:
each `vss-eval-*` has its own `/tmp/skill-eval/active-deploy.txt`. With
selection over a fleet, the orchestrator can prefer a warm box (marker
matches desired profile-mode) over a cold one, turning what would
otherwise be a redeploy into a hot reuse across runs.

The **worker-pool model** (per zac): one skill-eval agent = one serial
worker processing trials in order. Multiple agents = multiple workers
running concurrently, each grabbing a different box from the fleet.
Within a worker, trials are serial; across workers, parallelism comes
for free from however many `vss-eval-*` boxes exist. No orchestrator
concurrency rework — it stays serial.

## Design

### 1. Single canonical "active marker" per instance — overwrite-only

Replace the per-profile flag fan-out with **one canonical file per Brev
instance** at `/tmp/skill-eval/active-deploy.txt`. Holds the literal
`<profile>-<mode>` of what is currently RUNNING on the box. Writes are
**overwrite, never append**. Empty / missing means "nothing is up."

Single owner: whoever last successfully ran `/deploy` on the box. Two
write paths in practice — the harness pre-deploy hook (when called) and
the deploy/* trial's `test.sh` (its scored task IS running /deploy) —
both overwrite the same file with `<profile>-<mode>`. Equivalent
semantics; pick one or both, just never `touch` per-flag.

### 2. Pre-deploy hook reads canonical marker, compares contents

In `BrevEnvironment._ensure_prerequisite_deployed`:

1. `cat /tmp/skill-eval/active-deploy.txt 2>/dev/null || echo ""` on the
   box.
2. If `stdout.strip() == f"{profile}-{deploy_mode}"` → skip pre-deploy.
   Box is hot.
3. Else → run `/deploy -p <profile> -m <mode>` via
   `claude --print --dangerously-skip-permissions`; the deploy skill's
   own step-0 teardown handles any prior stack. On success, **overwrite**
   `active-deploy.txt` with `<profile>-<mode>`. On failure, leave the
   marker alone — next trial re-evaluates.

After the trial: do not teardown, do not clear the marker. Same-profile
back-to-back trials hit the marker hot.

### 3. Multi-instance selection at orchestrator level

In AGENTS.md § 5a (instance pick) and § 5b (lock acquisition), the
orchestrator stops hardcoding `BREV_INSTANCE=vss-eval-<short>`. Selection
algorithm, executed once per trial (cheap — three short brev exec
calls):

```
candidates = [b for b in brev_ls_json
              if b.name.startswith("vss-eval-")
              and platform_matches(b, trial.platform)
              and b.status in ("RUNNING", "READY")]
# Score: warm marker beats cold; free lock beats waiting; tiebreak is
# instance name (deterministic).
for b in sorted(candidates, key=(marker_match, lock_free, name)):
    if try_flock(b.name, nonblocking=True):
        BREV_INSTANCE=b.name; proceed
        break
else:
    # Nothing free in fleet — block on the best-by-marker candidate
    # for the existing 3-hour wall.
    flock -w 10800 candidates[0].lock
    BREV_INSTANCE=candidates[0].name; proceed
```

Per-box lock files: `/tmp/brev/<resolved-instance-name>.lock` (already
keyed on `$INSTANCE_NAME` in today's flock; just stop hardcoding the
name). With fleet=1 this collapses to today's behavior (one candidate,
same lock path). With fleet>1, two concurrent CI runs land on different
boxes naturally.

Selection lives in the orchestrator (AGENTS.md prose + small shell
helper in step 5a), NOT in `BrevEnvironment.start()`. The contract for
`BrevEnvironment` is unchanged: it honors `BREV_INSTANCE` if set and
validates the chosen box.

### 4. Cross-worker safety

- Each box's `active-deploy.txt` is on its own /tmp; no shared state.
- Per-box flock prevents two workers from running trials on the same
  box simultaneously, even within /deploy.
- The `started-by-<run_id>.txt` cleanup marker (used by
  `cleanup_instances`) is already per-run — workers don't step on each
  other at teardown.

### 5. Concurrency from CI run multiplicity, not orchestrator code

No change to AGENTS.md § 5c trial-by-trial serial loop. No change to
`skills_eval_agent.py`'s tool loop. If two PRs trigger runs at the same
time, GitHub Actions launches two workflow runs, each instantiates one
serial-worker skill-eval agent, and the per-box flock arbitrates fleet
access. With fleet=1: workers wait, behaviour unchanged. With fleet=N:
up to N parallel.

## Net effect

- Stale-marker silent-skip bug is gone — one marker, one truth.
- Same-profile back-to-back trials redeploy zero times after the first
  (within a worker AND across workers, via warm-marker selection).
- Profile transitions redeploy once per box per transition.
- Concurrent CI runs naturally use the fleet: with fleet=N, up to N PRs
  evaluated in parallel without orchestrator changes.
- Fleet sizing is an ops concern (manually `brev create` more
  `vss-eval-l40s-2`, etc.) — harness picks them up automatically.
- Redeploy cost falls under `PRE_DEPLOY_TIMEOUT_SEC` (1800s harness
  budget), not the agent_timeout, so trial scoring measures skill
  quality, not deploy time.

## Verification

1. **Stale-flag regression test.** Reproduce the run 24969145586 pattern:
   `deploy/lvs` → `deploy/search` → `vios step-1`. Confirm `vios` now
   redeploys base (marker reads `search-remote-all`, not
   `base-remote-all`) instead of skipping on a stale flag.
2. **Same-profile reuse.** `deploy/base` → `vios step-1` → `vios step-2`
   → `vios step-3`. Three vios trials share the marker set by
   `deploy/base`; pre-deploy hook fires zero times.
3. **Profile transition.** `deploy/base` → `vios step-1` →
   `deploy/search` → `video-search step-1`. Each transition triggers
   exactly one /deploy.
4. **Fleet=1 baseline.** With one `vss-eval-l40s`, behaviour is
   indistinguishable from today.
5. **Fleet>1 concurrency smoke test.** Manually `brev create
   vss-eval-l40s-2`. Trigger two PRs simultaneously. Confirm the two
   workflow runs land on different boxes and complete in parallel.
6. **Marker file shape.** After any run, `cat
   /tmp/skill-eval/active-deploy.txt` returns one profile-mode token;
   no `deployed-*.flag` files exist (or they're harmless leftovers
   from before the migration).

## Out of scope

- Auto-creating fleet members (`brev create vss-eval-*` from the
  orchestrator). Today fleet sizing is manual via ops.
- Per-trial parallelism inside a single worker. The orchestrator still
  dispatches trials serially within one CI run; cross-run parallelism
  is the model.
- Marker GC for the bug-period `deployed-*.flag` orphans — one-time
  manual `rm -f` on existing boxes.
- Multi-agent harness support — separate spec at `multi-agent.spec`.
