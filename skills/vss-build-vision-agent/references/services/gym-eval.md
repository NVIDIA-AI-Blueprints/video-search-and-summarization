<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Gym Eval Capability Owner

> **This owner is an evaluation overlay, not a vision capability.** It adds no
> ability to the built agent. It scores the stack that was already built, so it
> is offered after a build rather than in the Q2b capability multi-select, and it
> is **outside the forward closure** (see *Relationship to the delta contract*).

## Capabilities and service keys

| Capability | Canonical service profile key |
|---|---|
| Score a deployed stack with NVIDIA NeMo Gym, producing a scalar reward per task | `gym-eval` |

## Required peers

- **None.** `gym-eval` is a pure leaf: nothing may `depends_on` it. Compose
  hard-errors with "depends on undefined service" when an active service depends
  on one excluded by profile filtering, and this service is excluded by default.
- It reaches the deployed stack over the project network by service name, for
  example `http://vss-agent:8000`. It needs no published port.
- It is a job, not a service: `restart: "no"`.

## Relationship to the delta contract

[`../composition.md`](../composition.md) requires a delta to be symmetric: prune
every Foundation service no requested capability needs, and validation rejects
orphaned Foundation carryover.

**This owner does not participate in that pass.** Its subject *is* the Foundation
as deployed, so pruning to a closure would score a different system and the
numbers would not be comparable to anything. Apply it **after** resolution
completes, to the resolved service set, adding exactly one key and removing
none:

- On a **Stock** build this is already consistent, since Stock keeps the
  profile's authoritative `COMPOSE_PROFILES` unchanged.
- On a **Delta** build, resolve and prune first as normal, then add `gym-eval`
  to the resolved set. It never contributes owners or peers to the closure, so
  it cannot retain a Foundation service that resolution decided to drop.

Verify with the service diff before trusting any comparison: the build with the
overlay must differ from the same build without it by exactly one added service,
`gym-eval`.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_GYM_EVAL_IMAGE` | Registry path. Defaults to `nvcr.io/nvidia/eval-factory/nemo-gym`. |
| `VSS_GYM_EVAL_TAG` | **Required, fail-closed (`${VAR:?}`), no default.** Set only after the image gate passes. |
| `VSS_GYM_EVAL_OUTPUT_DIR` | Runner output path inside the container. Defaults to `/workspace/outputs`. |
| `VSS_DATA_DIR` | Host side of the rollout mount, `${VSS_DATA_DIR}/gym_eval`. |

## Image gate

**Do not pull a `nemo-gym` tag before checking its provenance.** Published tags
have carried royalty-bearing codec libraries that VSS containers must not ship.
The gate reads registry metadata only, roughly 20 KB, and never pulls a layer;
it fails closed on a build that predates the codec removal, on one whose layer
history records a codec install, and on provenance it cannot read at all.

The gate, the run lifecycle, and how to read the resulting reward are owned by
[`vss-run-gym-eval`](../../../vss-run-gym-eval/SKILL.md). Route there rather
than reimplementing any of it here.

## Status

The containerised runner described here is the packaging target and **has not
been exercised**: every published tag still fails the gate, so it cannot be run
until a codec-clean image exists. The verified path today is the host workflow
in [`../../../vss-run-gym-eval/references/run.md`](../../../vss-run-gym-eval/references/run.md),
which needs no build step and no image.

## Sources

- [`vss-run-gym-eval`](../../../vss-run-gym-eval/SKILL.md) — routing, image gate, reward interpretation
- [`vss-run-gym-eval/references/delta.md`](../../../vss-run-gym-eval/references/delta.md) — runner service definition and composition
- [`vss-run-gym-eval/references/run.md`](../../../vss-run-gym-eval/references/run.md) — verified host lifecycle
- [`vss-run-gym-eval/references/results.md`](../../../vss-run-gym-eval/references/results.md) — comparison protocol
