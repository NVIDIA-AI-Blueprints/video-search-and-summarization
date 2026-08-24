<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Verifying that traffic is routed

## Use Switchyard's routing log

Switchyard records every routed request itself. Start it with
`--routing-log-file` and read the JSONL:

```bash
docker run -d --name switchyard -p 4000:4000 \
  -e NVIDIA_API_KEY \
  -v "$PWD/config.toml:/etc/switchyard/config.toml:ro" \
  -v "$PWD/routing-logs:/var/log/switchyard" \
  switchyard:local \
    --config /etc/switchyard/config.toml --port 4000 \
    --routing-log-file /var/log/switchyard/routing.jsonl
```

One record per request, with the model that answered and token usage:

```json
{"ts":"2026-08-24T19:39:47.330Z","model":"openai/openai/gpt-5.6-sol",
 "prompt_tokens":7,"completion_tokens":9,"total_tokens":16}
```

Aggregate view, including per-model call share:

```bash
curl -s http://<router-host>:4000/v1/stats
```

A recording proxy is only a fallback for when you cannot restart or reconfigure
the router.

## What VSS shows, and does not

Switchyard reports the served model **twice**: in the
`x-model-router-selected-model` response header, and in the response body's
`model` field.

VSS surfaces neither. The agent builds its final message from content and tool
calls only, so response metadata does not reach the user or the scratchpad. That
is a property of the VSS agent, not a limitation of the router — and it is why
the routing log above is the place to look.

## Checks on the VSS side

**The endpoint VSS resolved.**

```bash
docker compose -f "_builds/<name>/resolved.yml" config | grep -E 'LLM_BASE_URL|LLM_MODE'
```

**That no service was added.**

```bash
diff <(docker compose --env-file "<foundation>/overrides.env" \
         -f "${VSS_APPS_DIR}/compose.yml" config --services | sort) \
     <(docker compose --env-file "_builds/<name>/override.env" \
         -f "_builds/<name>/compose.yml" config --services | sort)
```

Expect **no `>` line**. `<` lines are expected: `COMPOSE_PROFILES` carries
`llm_${LLM_MODE}_${LLM_NAME_SLUG}`, so remote mode resolves the local LLM NIM
away and frees its GPU.

## Reading a split honestly

Each response names one configured target. **Do not expect variation after a
handful of calls** — independent random selection can pick the same target
repeatedly, and a short run need not reflect the configured split. Judge the
split from `/v1/stats` or the routing log over a sufficient sample, not from
watching a few requests.
