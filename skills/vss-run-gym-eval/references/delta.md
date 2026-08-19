# The eval-runner delta

> ## ⚠ Status: this is the packaging target, and it has NOT been run
>
> **What has been verified** is the host workflow in [`run.md`](run.md): the Gym
> CLI and the `vss_ask_video` resources server running on the host, scoring a VSS
> deployment reachable at a published port. That path produced a real reward
> against a live deployment and is what to use today.
>
> **What is described below** is the eventual packaging — the runner as a
> `gym-eval` container composed into the Compose project. It cannot be exercised
> yet: running it means pulling a `nemo-gym` image, and every tag checked so far
> — `26.05` is the one with recorded evidence — is rejected by the image gate in
> [`../SKILL.md`](../SKILL.md) for carrying royalty-bearing codec libraries. No
> tag is known to pass; that is not the same as having checked them all, so run
> the gate rather than assuming. A patch release is expected upstream.
>
> **Known gaps to close before this path is usable**, listed so nobody assumes
> they are handled:
>
> - the runner service mounts only the output directory — it does not yet mount a
>   Gym checkout or this server
> - it passes no judge settings, which the server now requires at startup
> - `vss_base_url` must become the agent's service name on the project network
>   (`http://vss-agent:8000`), not the host default
> - there are no `docker compose` commands here for invoking the two-phase
>   lifecycle inside the container
>
> Treat the sections below as a design record. Do not follow them expecting a
> working eval, and do not document them as working until a run says so.

The Gym eval runner is added to a deployment as a **Delta Profile** on exactly
one Foundation, using the composition rules in
[`vss-build-vision-agent/references/composition.md`](../../vss-build-vision-agent/references/composition.md).
Read that file first; this one records only what is specific to the eval runner.

**Nothing here is checked in.** The only writable location is `_builds/<name>/`,
which is gitignored and is never a Compose profile. This skill adds no developer
profile, no service to any shipped compose file, and nothing to
`container-inventory.json` or `containers.env`.

## Build artifact

```
_builds/<name>/
├── override.env           # VSS_GYM_EVAL_OUTPUT_DIR (the runner's output dir,
│                          # /workspace/outputs), COMPOSE_PROFILES, and the Foundation's
│                          # CHECKED-IN values -- copied from its overrides.env,
│                          # NOT its generated.env, so host-resolved values are
│                          # absent (see the identity warning in ../SKILL.md).
│                          # The image tag is deliberately NOT written here: it
│                          # is fail-closed with no default and is supplied at
│                          # run time, only after the image gate passes.
├── compose.yml
├── resolved.yml
└── patches/
    └── gym-eval.yml       # the runner service definition
```

`<name>` is a filesystem label, never a Compose profile key.

## The delta

Start from the Foundation's effective `COMPOSE_PROFILES` — read it from the
profile's checked-in `overrides.env`, which is authoritative — and **add exactly
one key**, `gym-eval`. Add nothing else and remove nothing.

That single-key rule is not tidiness. The comparison this skill supports is two
eval harnesses scoring one identical stack, so any other divergence from the
Foundation is a confound in every score reported — and a silent one, because
nothing would fail.

> ### ⚠ This is a deliberate exception to the general delta contract
>
> [`composition.md`](../../vss-build-vision-agent/references/composition.md)
> requires a delta to be **symmetric**: compute the forward closure from the
> requested capabilities and *prune every Foundation service outside it*, with
> validation rejecting "orphaned Foundation carryover". **This skill does the
> opposite: it prunes nothing.**
>
> The reason is that pruning would destroy the thing being measured. An evaluation
> overlay's "requested capability" is *the Foundation's own behaviour as
> deployed*; a pruned stack is a different system, so its scores are not
> comparable to the Foundation's and the comparison has no meaning. Pruning is
> right when the goal is the smallest stack that satisfies a request; it is wrong
> when the goal is to measure an existing one.
>
> **This exception is not yet recorded in the parent contract.** Until it is,
> treat `composition.md`'s pruning rule as authoritative for every other delta and
> this one as a documented departure. If the composition contract's owner rejects
> the exception, this skill must change — not the contract silently.

Composing the delta as *Foundation + one key* **preserves every Foundation
service and adds only `gym-eval`** by construction, so the two service sets
differ by exactly that runner. It does **not** make resolved values identical:
see the identity warning in [`../SKILL.md`](../SKILL.md), because delta
resolution does not read the Foundation's `generated.env`.

`gym-eval` is a genuinely new service, so it uses its own service key as its
self-profile. Do not derive an aggregate profile name and never invent a
`bp_developer_*` name.

## The runner service

```yaml
services:
  gym-eval:
    image: ${VSS_GYM_EVAL_IMAGE:-nvcr.io/nvidia/eval-factory/nemo-gym}:${VSS_GYM_EVAL_TAG:?set only after the image gate in SKILL.md passes}
    profiles: ["gym-eval"]
    container_name: vss-gym-eval
    restart: "no"
    environment:
      - VSS_GYM_EVAL_OUTPUT_DIR=${VSS_GYM_EVAL_OUTPUT_DIR:-/workspace/outputs}
    volumes:
      - ${VSS_DATA_DIR}/gym_eval:/workspace/outputs
```

Four properties, each load-bearing:

- **`VSS_GYM_EVAL_TAG` uses the fail-closed form `${VAR:?message}`.** A bare
  `${VAR}` is *not* a gate: Compose substitutes an empty string and warns, which
  yields `image: …nemo-gym:` and fails only incidentally, as a malformed
  reference. `:?` makes resolution fail with the message, which is the difference
  between a check and an accident. Every other value carries a plain default,
  because Compose interpolates *before* it filters on profiles.
- **A pure leaf.** Nothing may `depends_on` it. Compose hard-errors with
  "depends on undefined service" when an active service depends on one excluded
  by profile filtering.
- **`restart: "no"`.** The runner is a job, not a service.
- **No `command:` yet.** The image has no `ENTRYPOINT` and its `Cmd` is
  `["/bin/bash"]`, so with no command it starts, reads EOF and exits 0 — a
  container that looks deliberate and does nothing. The run lifecycle supplies the
  command; see `references/run.md`.

## Networking

The runner joins the project network, so it reaches VSS by service name — for
example `http://vss-agent:8000`. Do **not** copy `172.17.0.1` from the prototype
runbook: that is the host bridge gateway, correct only when reaching a
host-mode VSS from a container on the default bridge, and wrong inside the
project network.

## Verify before running

```bash
# The delta adds exactly one service to the Foundation.
diff <(docker compose ... -f <foundation> config --services | sort) \
     <(docker compose ... -f _builds/<name>/compose.yml config --services | sort)
# Expect exactly one line: > gym-eval
```

If that diff shows anything else, the delta has drifted from its Foundation and
any comparison run against it is invalid. Fix the delta rather than explaining
the difference.
