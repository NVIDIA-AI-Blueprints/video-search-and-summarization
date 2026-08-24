<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Running the router

The router is **not** deployed by this repository. It is infrastructure the
operator runs, in the same sense as a remote LLM endpoint. VSS only needs a URL
it can reach.

## Where it must live

Anywhere the VSS agent can reach over the network. Two common placements:

- **Same host, separate container or process.** Reachable from the agent as the
  host address and port. Do not assume `localhost` from inside a container.
- **A different host.** Reachable by IP or DNS name.

VSS containers run on Compose's default bridge network with published ports.
`network_mode: host` appears in `services/video-summarization/compose.yml` but
is commented out, so `localhost` inside the agent container is the container
itself and not the box. Give the router the host's routable address; that holds
whether or not the operator uncomments host mode.

## Obtaining Switchyard

Source: [`NVIDIA-NeMo/Switchyard`](https://github.com/NVIDIA-NeMo/Switchyard),
Apache-2.0, public.

**This integration pins and deploys no image.** Build from the repository's own
`Dockerfile`, which produces a `debian:bookworm-slim` runtime containing only
the `switchyard-server` binary and `ca-certificates`, running as a non-root
user and exposing `4000`:

```bash
git clone https://github.com/NVIDIA-NeMo/Switchyard.git
cd Switchyard
docker build -t switchyard:local .
```

Pin whatever tag or commit you build from and record it, so a routed evaluation
can be repeated. Do not treat `main` as stable.

## Serving

```bash
docker run --rm -p 4000:4000 \
  -v "$PWD/config.toml:/etc/switchyard/config.toml:ro" \
  switchyard:local --config /etc/switchyard/config.toml --port 4000
```

`--config` takes the TOML shown below. There is no `serve` subcommand in
v0.2.0: the entrypoint is `switchyard-server` and it errors on one.

Start from [`config.example.toml`](config.example.toml).

## Credentials

Two separate boundaries, and it is worth keeping them apart.

**Router to upstream.** The router's own credential belongs in its process or
container, referenced by `api_key_env`. It does not need to be copied into the
VSS build.

**Caller to router.** VSS clients may still send their configured credential to
whatever `LLM_BASE_URL` points at: `lvs-server` is wired with
`api_key: ${NVIDIA_API_KEY}` against `LVS_LLM_BASE_URL`. Do not assume the
incoming Authorization header is absent — the router will receive one.

In CI, use the key the deployment already has rather than introducing one for
routing.

## Confirming it is up

```bash
curl -sf http://<router-host>:4000/v1/models >/dev/null && echo "router reachable"
```

Note the `/v1` here. You type it when probing by hand; you do **not** put it in
`LLM_BASE_URL`, because the agent appends it. See
[`configure-vss.md`](configure-vss.md).
