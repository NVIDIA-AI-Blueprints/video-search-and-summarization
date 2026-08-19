# Results and comparison

## Where results land

The delta mounts `${VSS_DATA_DIR}/gym_eval` at `/workspace/outputs` and points the
runner's output directory there, so rollouts survive the container. That mount is
the only writable path the delta gives the runner into the host, and it is
**outside the repository** — the skill writes nothing under `deploy/docker/` and
nothing under `skills/`.

That describes what the delta *declares*. The containerised runner has not been
exercised (see the status note in [`compose-delta.md`](compose-delta.md)), so treat where its
files actually land as unconfirmed until a tag clears the image gate. On the host
path in [`run-lifecycle.md`](run-lifecycle.md) — the one that has produced a real reward — results go
wherever the Gym CLI is pointed, not to this mount.

A rollout file is JSONL, one object per task instance, each carrying the
`instance_id`, the trajectory the policy produced, and the scalar `reward` the
resources server returned.

## Reading a reward honestly

A reward is computed by **this server**, not forwarded from VSS's own eval:

    reward = 0.4 * (the policy called ask_vss) + 0.6 * quality

`quality` is 1.0 or 0.0 from an LLM judge applied to the task rubric, or a
recall-and-honesty score when the task carries ground truth. Three things follow:

- **It is not `passed / total` from VSS's CI eval**, and the two are not
  numerically comparable. See the warning in `../gym.md`.

- **A number that moved is a real change only if the scorer did not.** The judge
  model, the rubric and the reward formula are all part of the measurement, so a
  comparison is valid only across runs that share them. Record the judge used with
  the results.
- **A reward of 0.0 needs triage before interpretation.** See the reward traps in
  [`run-lifecycle.md`](run-lifecycle.md): an exception handler that degrades to zero looks exactly
  like a genuine miss. Reward 0.0 together with an empty `response.output` means
  the server errored.

## Comparing two harnesses

The comparison is **two harnesses scoring one identical stack**, so the only
valid procedure is sequential:

| Step | Action | Why it matters |
|---|---|---|
| 1 | Deploy the Foundation, run VSS's own eval | the baseline |
| 2 | **Persist those results outside the data directory** | ⚠️ they are destroyed in step 3 |
| 3 | Compose the delta, deploy, run the Gym eval | adds only the runner |
| 4 | Persist Gym's results, then compare | — |

**Step 2 is the one that gets skipped.** Every developer profile resolves to
`COMPOSE_PROJECT_NAME=vss` and the same host ports, and `dev-profile.sh` runs
`state_down` before every `state_up` — its `up` path is literally `state_down;
state_up`. That teardown does `rm -rf` on the **entire data directory**, not a
subset of it (`deploy/docker/scripts/dev-profile.sh`).

`${VSS_DATA_DIR}/gym_eval` is inside that directory. Results left there when the
stack is switched are gone, and the first indication is an empty directory after
a run that took hours. Persist anything you intend to compare **outside**
`${VSS_DATA_DIR}` before deploying the next profile.

> Do not reason from `blueprint-deploy.sh` here. Its teardown is much narrower —
> named volumes plus a fixed list of `data_log/*` subdirectories, which
> `gym_eval` is not on — but that is the **warehouse** path. The
> developer-profile comparison this skill supports runs `dev-profile.sh`, and
> that one deletes everything.

Running both stacks concurrently is not an alternative: they would contend for
the same GPU, so the measurement would reflect contention rather than harness
behaviour.

## What a valid comparison requires

Both runs must score the **same stack**, differing only by the eval runner. By
construction the delta **preserves every Foundation service and adds exactly one**
— it is the Foundation's `COMPOSE_PROFILES` plus a single key — so the two
service sets differ only by `gym-eval`. It does **not** make
resolved values identical: delta resolution does not read the Foundation's
`generated.env`, so a host-customised deployment can differ on values while the
service list matches exactly. See the identity warning in
[`../gym.md`](../gym.md), which is what to do about it. Confirm both — the
service list and the resolved environment — before trusting a comparison:

```bash
diff <(docker compose ... -f <foundation> config --services | sort) \
     <(docker compose ... -f _builds/<name>/compose.yml config --services | sort)
# exactly one line of output, "> gym-eval"
```

If that diff shows anything else, discard the comparison. A stack that drifted
from its Foundation produces numbers that look fine and mean nothing, which is
worse than a run that fails.

## Diagnosis with BLADE

BLADE is Gym's diagnostics pass over a completed run. It reads the rollouts,
metrics and configs and reports whether a failure was infrastructure, harness, or
a genuine capability gap — the question that is otherwise answered by hand, per
surface, by whoever built the harness.

Run it against the rollout directory after collection. Prefer it over reading
rollouts by hand when triaging a low score: distinguishing "the stack was broken"
from "the model could not do it" is exactly what it exists for, and it is the
diagnosis this migration is meant to make routine rather than bespoke.
