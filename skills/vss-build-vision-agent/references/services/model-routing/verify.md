<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Verifying that traffic is routed

## ⚠ VSS cannot tell you which model answered

Switchyard reports its choice on the response:

```
x-model-router-selected-model: openai/openai/gpt-5.6-luna
```

**VSS's LangChain client discards it.** Nothing downstream of VSS records which
model served which call, so an unaudited production rollout is inadvisable. Say
so to a user who asks whether they can just turn it on.

*(Measured on Switchyard v0.2.0: `-selected-model` is the only router header
returned. Earlier notes describing `-selected-tier` and `-rationale` predate it.)*

## What you can check

**The router itself.** Confirm it is up and that a call is routed:

```bash
curl -sf http://<router-host>:4000/health
curl -s -D - -o /dev/null http://<router-host>:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"<route-id>","messages":[{"role":"user","content":"OK"}]}' \
  | grep -i x-model-router-selected-model
```

Repeat it a few times on a split route: the model should vary.

**The endpoint VSS resolved.**

```bash
docker compose -f "_builds/<name>/resolved.yml" config | grep -E 'LLM_BASE_URL|LLM_MODE'
```

**That no service was added.** A routed build adds nothing to its Foundation:

```bash
diff <(docker compose --env-file "<foundation>/overrides.env" \
         -f "${VSS_APPS_DIR}/compose.yml" config --services | sort) \
     <(docker compose --env-file "_builds/<name>/override.env" \
         -f "_builds/<name>/compose.yml" config --services | sort)
```

Expect **no `>` line**. `<` lines are expected: `COMPOSE_PROFILES` carries
`llm_${LLM_MODE}_${LLM_NAME_SLUG}`, so remote mode resolves the local LLM NIM
away and frees its GPU.

## Capturing per-call decisions

The only way to record them today is a proxy between VSS and the router that
logs the header VSS drops:

```
vss-agent ──► :4001 recording proxy ──► :4000 router ──► model
```

Point `LLM_BASE_URL` at the proxy. It must forward the request unmodified; if it
rewrites anything the log describes the proxy, not the router. This is a
measurement tool, not a deployment component.
