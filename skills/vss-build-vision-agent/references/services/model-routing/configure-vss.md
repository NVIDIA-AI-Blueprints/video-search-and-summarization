<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Pointing VSS at a router

This is the whole integration. One override set in the build's `override.env`,
and no other change anywhere.

## The override set

```bash
LLM_MODE=remote
LLM_NAME_SLUG=none
LLM_BASE_URL=http://<router-host>:4000
LLM_NAME=<the name the router routes on>
```

This is the **Remote LLM** row already documented in
[`../../env-overrides.md`](../../env-overrides.md). Nothing here is specific to
routing except where the URL points. If a build can talk to a remote LLM, it can
talk to a router.

## ⚠ `LLM_BASE_URL` must not end in `/v1`

The agent appends `/v1` itself. Writing it twice yields requests to
`…:4000/v1/v1/chat/completions`, which the router answers with 404 and which
reads in the agent log as the model being unreachable rather than as a URL
mistake.

It must be the router **origin**: no path, no trailing slash. VSS forms
`${LLM_BASE_URL}/v1` by literal concatenation, so every form below except the
first is wrong.

```bash
LLM_BASE_URL=http://10.0.0.5:4000        # correct
LLM_BASE_URL=http://10.0.0.5:4000/v1     # -> /v1/v1
LLM_BASE_URL=http://10.0.0.5:4000/v1/    # -> /v1//v1
LLM_BASE_URL=http://10.0.0.5:4000/       # -> //v1
```

`RTVI_VLM_ENDPOINT` is the opposite and **must** end in `/v1`, because RT-VLM
consumes it verbatim. Do not generalise from one to the other.

## Where the value goes

Into `_builds/<name>/override.env`, like every other override. Never into a
Foundation's checked-in `overrides.env`: that changes the profile for everyone
and dirties the tree.

The Foundation's `COMPOSE_PROFILES` text is **unchanged**. Routing adds no
service key. The local LLM NIM does drop out, because that list carries
`llm_${LLM_MODE}_${LLM_NAME_SLUG}` and remote mode resolves it away — expected,
and it frees the GPU.

## ⚠ Routing also moves the eval judge

`LLM_BASE_URL` has more consumers than the agent. Measured on a routed build:

| Consumer | Variable | Resolves to |
|---|---|---|
| `vss-agent` | `LLM_BASE_URL` | `…:4000` (the agent appends `/v1`) |
| `lvs-server` | `LVS_LLM_BASE_URL` | `…:4000/v1` (compose appends it) |
| `vss-agent` | `EVAL_LLM_JUDGE_BASE_URL` | `…:4000` |

The third one matters. The profiles set
`EVAL_LLM_JUDGE_BASE_URL=${LLM_BASE_URL}`, so enabling routing points **VSS's
own eval judge** at the router as well. The judge then answers from whichever
tier the router picked, and stops being a fixed grader.

Nothing errors. Scores just quietly become non-comparable, which defeats the
usual reason for turning routing on. If you are measuring, pin the judge to a
single endpoint first:

```bash
EVAL_LLM_JUDGE_BASE_URL=http://vss-llm-nim:8000   # or any fixed endpoint
```

The two `/v1` conventions above are also why `LLM_BASE_URL` must not carry the
suffix: `lvs-server` would receive `…:4000/v1/v1`.

## What `LLM_NAME` means here

With a router, `LLM_NAME` no longer names a model to load. It is the string the
router keys its routing profile on, so it selects a *policy*, not a checkpoint.
Set it to whatever profile name the router's config defines; see
[`config.example.toml`](config.example.toml).

This is the one place the mental model shifts, and it is worth stating to a user
who expects `LLM_NAME` to be a model.

## Rolling back

Remove **all four** routing overrides from the build and regenerate, so the
Foundation's own values apply again. Restoring only `LLM_BASE_URL` leaves
`LLM_MODE=remote`, which keeps the local NIM switched off. For `lvs` the
Foundation values are:

```bash
LLM_MODE=local_shared
LLM_NAME_SLUG=nemotron-3.5-lightning-30b-a3b
LLM_BASE_URL=http://vss-llm-nim:8000
LLM_NAME=nvidia/nemotron-3.5-lightning-30b-a3b
```

Copy the corresponding four values from a different Foundation's
`overrides.env` if you are not on `lvs`. There is no other state to undo. Nothing was installed, no service was added,
and no Compose file was edited, which is the main practical argument for the
configuration-only shape.
