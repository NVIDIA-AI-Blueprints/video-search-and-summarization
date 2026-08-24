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

```bash
LLM_BASE_URL=http://10.0.0.5:4000        # correct
LLM_BASE_URL=http://10.0.0.5:4000/v1     # wrong: the agent appends /v1
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

## What `LLM_NAME` means here

With a router, `LLM_NAME` no longer names a model to load. It is the string the
router keys its routing profile on, so it selects a *policy*, not a checkpoint.
Set it to whatever profile name the router's config defines; see
[`config.example.toml`](config.example.toml).

This is the one place the mental model shifts, and it is worth stating to a user
who expects `LLM_NAME` to be a model.

## Rolling back

Point `LLM_BASE_URL` back at the in-cluster model and redeploy:

```bash
LLM_BASE_URL=http://vss-llm-nim:8000
```

There is no other state to undo. Nothing was installed, no service was added,
and no Compose file was edited, which is the main practical argument for the
configuration-only shape.
