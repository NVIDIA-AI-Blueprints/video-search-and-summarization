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

VSS containers on the developer profiles run with `network_mode: host`, so a
router on the same box is reachable on that box's address. A router run as a
container on the default bridge is *not* reachable at `172.17.0.1` from a
host-mode VSS container; use the host's real address.

## Obtaining Switchyard

Source: [`NVIDIA-NeMo/Switchyard`](https://github.com/NVIDIA-NeMo/Switchyard),
Apache-2.0, public.

**No container image is published.** Build it from the repository's own
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

The router holds the upstream credentials, not VSS. Whatever API keys the
weak and frontier targets require live in the router's config or environment.
VSS sends no key to the router unless the router itself demands one.

Keep that boundary: moving upstream keys into the VSS build's `override.env`
would put them in a build artifact for no benefit. In CI, use the key the
deployment already has rather than introducing one for routing.

## Confirming it is up

```bash
curl -sf http://<router-host>:4000/v1/models >/dev/null && echo "router reachable"
```

Note the `/v1` here. You type it when probing by hand; you do **not** put it in
`LLM_BASE_URL`, because the agent appends it. See
[`configure-vss.md`](configure-vss.md).
