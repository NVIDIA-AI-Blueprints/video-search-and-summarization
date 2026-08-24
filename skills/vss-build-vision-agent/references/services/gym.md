<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Gym Eval Capability Owner

> Reference bundle for evaluating a built stack with **NVIDIA NeMo Gym**,
> consumed by build-vision-agent. Like the SOP family, the whole bundle
> (condensed contract here + detailed contracts of record + net-new assets)
> lives under this `references/services/gym/` folder, because it ships net-new
> assets (the runner overlay) rather than materializing services from the
> upstream Compose tree.

> **This owner is an evaluation overlay, not a vision capability.** It adds no
> ability to the built agent. It scores the stack that was already built, so it
> is offered after a build rather than in the Q2b capability multi-select, and it
> takes no part in forward closure or pruning.

## Capabilities and service keys

| Capability | Canonical service profile key |
|---|---|
| Score a deployed stack with NeMo Gym, producing a scalar reward per task | `gym-eval` |

## Required peers

- **None.** `gym-eval` is a pure leaf: nothing may `depends_on` it.
- It reaches the deployed stack over the project network by service name, for
  example `http://vss-agent:8000`. It needs no published port.
- It is a job, not a service: `restart: "no"`.

## Relationship to the delta contract

[`../composition.md`](../composition.md) requires a delta to be symmetric: prune
every Foundation service no requested capability needs, and validation rejects
orphaned Foundation carryover.

**This owner does not participate in that pass.** Its subject *is* the built
stack, so pruning to a closure would score a different system and the numbers
would not be comparable to anything. Apply it **after** resolution completes, to
the resolved service set, adding exactly one key and removing none:

- On a **Stock** build this is already consistent, since Stock keeps the
  profile's authoritative `COMPOSE_PROFILES` unchanged.
- On a **Delta** build, resolve and prune first as normal, then add `gym-eval`.
  It contributes no owners or peers to the closure, so it cannot retain a
  Foundation service that resolution decided to drop.

Verify before trusting any comparison: the build with the overlay must differ
from the same build without it by exactly one added service, `gym-eval`.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_GYM_EVAL_IMAGE` | Registry path. Defaults to `nvcr.io/nvidia/eval-factory/nemo-gym`. |
| `VSS_GYM_EVAL_TAG` | **Required, fail-closed (`${VAR:?}`), no default.** Set only after the image gate passes. |
| `VSS_GYM_EVAL_OUTPUT_DIR` | Runner output path inside the container. Defaults to `/workspace/outputs`. |
| `VSS_DATA_DIR` | Host side of the rollout mount, `${VSS_DATA_DIR}/gym_eval`. |

## ⛔ Image gate

**Do not pull a `nemo-gym` tag before checking its provenance.** Published tags
have carried royalty-bearing codec libraries that VSS containers must not ship.
The gate reads registry metadata only and fails closed on a build that predates
the codec removal, on one whose layer history records a codec install, and on
provenance it cannot read at all.

Full procedure: [`gym/image-gate.md`](gym/image-gate.md). Run it before any pull.

## Status

The containerised runner is the packaging target and **has not been exercised**:
every published tag still fails the gate, so it cannot run until a codec-clean
image exists. The verified path today is the host workflow in
[`gym/run-lifecycle.md`](gym/run-lifecycle.md), which needs no build step and no
image.

## Detailed contracts of record

| File | Covers |
|---|---|
| [`gym/image-gate.md`](gym/image-gate.md) | Provenance check, three registry hops, fail-closed rules |
| [`gym/compose-delta.md`](gym/compose-delta.md) | Foundation entry point, placeholder resolution, build artifacts, verification |
| [`gym/run-lifecycle.md`](gym/run-lifecycle.md) | Two-phase `gym env start` / `gym eval run`, verified host path |
| [`gym/results.md`](gym/results.md) | Where rollouts land, reading a reward honestly, comparison limits |
| [`gym/comparison.md`](gym/comparison.md) | Side-by-side protocol against VSS's own harness |
| [`gym/resources-server.md`](gym/resources-server.md) | The `vss_ask_video` resources server and prerequisites |

## Net-new assets

| File | Purpose |
|---|---|
| [`gym/gym-eval-override.yml`](gym/gym-eval-override.yml) | The runner service definition, composed in after resolution |
