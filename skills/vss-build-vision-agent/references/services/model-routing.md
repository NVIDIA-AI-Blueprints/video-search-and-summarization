<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Model Routing Owner

> Reference bundle for routing VSS's LLM traffic through a model router, consumed
> by build-vision-agent. **NVIDIA Switchyard** is the one backend documented
> today; the bundle is named for the capability so a second router can be added
> without renaming anything.

> **This owner adds no service and changes no Compose file.** It is a
> configuration of an endpoint VSS already supports. There is nothing to compose,
> nothing to prune, and nothing to deploy from this repository.

## Capabilities and service keys

| Capability | Canonical service profile key |
|---|---|
| Route LLM traffic across models and providers, selecting per request | **none — configuration only** |

The empty key is the contract. If a build adds a service to enable routing, it
has gone wrong: see [Why there is no service](#why-there-is-no-service).

## What it changes

One value, already a supported override:

```
before   VSS ──────────────────────────────► vss-llm-nim:8000
after    VSS ──► switchyard:4000 ──► weak  or  frontier
                 (VSS cannot tell the difference)
```

`references/env-overrides.md` already lists **Remote LLM** as a supported
override set: `LLM_MODE=remote`, `LLM_NAME_SLUG=none`, `LLM_BASE_URL=<host>`,
`LLM_NAME=<model>`. A router is a remote OpenAI-compatible endpoint, so it needs
no new mechanism. Point `LLM_BASE_URL` at the router instead of at the model.

VSS is not consulted and learns nothing. There is no callback and no
negotiation: the router stands where the model used to be.

## Why there is no service

The router is not part of the vision stack. It is infrastructure the operator
runs, like the remote LLM endpoint it replaces, and VSS already treats a remote
endpoint as somebody else's process. Adding a service would:

- put a non-VSS process in the deployment's lifecycle, teardown and readiness;
- require an image this repository would have to pin, and no router image is
  published today;
- make routing something a build *deploys* rather than something an operator
  *chooses*, which is the opposite of the intent.

**A build that enables routing adds no service to its Foundation.** It may
*remove* the local LLM NIM, because `COMPOSE_PROFILES` carries
`llm_${LLM_MODE}_${LLM_NAME_SLUG}` and remote mode resolves it away, freeing
that GPU. Added services are the failure; removals are the point. That is what
the eval spec checks.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `LLM_MODE` | Set to `remote`. |
| `LLM_NAME_SLUG` | Set to `none`, as for any remote endpoint. |
| `LLM_BASE_URL` | The router's base URL, for example `http://10.0.0.5:4000`. **Must not end in `/v1`** — the agent appends it. |
| `LLM_NAME` | The model name the router expects, which selects its routing profile rather than a concrete model. |
| `NVIDIA_API_KEY` | Only when the router's upstream requires it. |

## Status and limits

**Routing is not observable from VSS.** The router reports its choice in the
`x-model-router-selected-model` response header, and VSS's LangChain client
discards it. Nothing downstream of VSS can currently tell which model answered
a given call, which makes an
unaudited production rollout inadvisable. See
[`model-routing/verify.md`](model-routing/verify.md) for the recording-proxy
workaround and what it costs.

**No router image is published.** Build from source; see
[`model-routing/run.md`](model-routing/run.md).

## Detailed contracts of record

| File | Covers |
|---|---|
| [`model-routing/routing.md`](model-routing/routing.md) | What the router does, and what it is not told |
| [`model-routing/run.md`](model-routing/run.md) | Obtaining and running Switchyard, and where it must live |
| [`model-routing/configure-vss.md`](model-routing/configure-vss.md) | The override set, the `/v1` trap, and rollback |
| [`model-routing/verify.md`](model-routing/verify.md) | Confirming traffic is routed, and why VSS alone cannot tell you |

## Net-new assets

| File | Purpose |
|---|---|
| [`model-routing/config.example.toml`](model-routing/config.example.toml) | Minimal two-tier config, verified against v0.2.0 |
