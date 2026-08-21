<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Comparison protocol

How to score one stack with both harnesses and get numbers that mean something.

## Foundation selection

Default to **`lvs`**. Its `config.yml` registers `lvs_video_understanding`;
`base`'s registers it zero times and offers only `video_understanding`, the
sparser of the two. A comparison run on `base` therefore risks conflating a
harness difference with the weaker sampling, which is the confound the whole
exercise exists to avoid — so prefer a Foundation that registers the denser
variant.

Use a different Foundation only when the user names one, or when the deployment
already running is a different profile — in which case the Foundation **must** be
that profile, because the comparison scores the stack that is actually up.

## Comparison protocol (side-by-side)

The comparison is **two harnesses scoring one identical stack**, so stack
identity is the control variable.

> ### ⚠ Identity is guaranteed for the service set, NOT for resolved values
>
> The delta adds exactly one service key, so by construction it **preserves every
> Foundation service and adds only `gym-eval`** — the two service sets differ by
> that one runner and nothing else. **Resolved values do not.** `dev-profile.sh` writes
> host-specific values — model modes, endpoints, device IDs, host paths — into the
> profile's `generated.env`, and delta resolution reads the checked-in `.env`,
> the checked-in `overrides.env`, and the build's `override.env`, **not
> `generated.env`**. A delta composed against an already-running, host-customized
> deployment can therefore deploy a *different* stack while looking identical.
>
> **So before comparing, do one of these:**
>
> - **Preferred:** carry the running deployment's resolved values into the build's
>   `override.env`. Read them from the Foundation's `generated.env` rather than
>   assuming the checked-in defaults apply.
> - Or deploy the Foundation from checked-in values with no host customization, so
>   `generated.env` adds nothing the delta would miss.
>
> Then verify rather than trust — compare the resolved environment, not just the
> service list:
>
> ```bash
> diff <(docker compose ... -f <foundation> config | grep -E '^\s+[A-Z_]+:' | sort) \
>      <(docker compose ... -f _builds/<name>/compose.yml config | grep -E '^\s+[A-Z_]+:' | sort)
> ```
>
> Differences confined to the `gym-eval` service are expected. Any difference on a
> Foundation service means the two stacks are not the same stack, and the
> comparison is void.

The two runs must also be sequential:

1. Deploy the Foundation. Run VSS's own eval. **Capture and persist the results
   now.**
2. Compose and deploy the eval delta, carrying the resolved values above. Run the
   Gym eval. Capture its results.
3. Compare.

**Step 1's capture is not optional.** Every developer profile resolves to
`COMPOSE_PROJECT_NAME=vss` and the same host ports, and `dev-profile.sh` runs
`state_down` before every `state_up` — which tears down the previous deployment
and its data directory. Results not persisted before the switch are gone.

Running both stacks concurrently is not supported and would not be a better
experiment: they would contend for the same GPU, so the measurement would reflect
contention rather than harness behaviour.

