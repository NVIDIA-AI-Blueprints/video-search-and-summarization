<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and limitations under the License.

-->

# TURN Server Prerequisite (Warehouse Apps)

All three Warehouse Apps profiles (`warehouse-2d-app`, `warehouse-3d-app`,
`warehouse-mv3dt-app`) use VIOS/VST for live/recorded camera playback in the web UI, over
WebRTC. WebRTC only works without a relay when the browser and the cluster are on the same
network. Anywhere else — different networks, NAT, a firewall blocking direct UDP — it needs
a TURN server, or playback silently fails. Background:

- [VIOS microservices](https://docs.nvidia.com/vss/latest/vios-microservices.html) — WebRTC/STUN/TURN config.
- [Warehouse 2D profile](https://docs.nvidia.com/vss/latest/warehouse-docs/2D-profile.html) — web UI, points back to VIOS/VST docs.
- [Known limitations](https://docs.nvidia.com/vss/latest/smartcity-docs/Known-Limitations.html) — WebRTC playback fails without a reachable TURN server.
- [Warehouse limitations (3.2.1)](https://docs.nvidia.com/vss/3.2.1/warehouse-docs/Known-Limitations.html) — WebRTC needs UDP; won't work over a TCP-only tunnel (e.g. Brev).

## Compose has this bundled; Helm does not

Docker-compose bundles a TURN server (`coturn`) in every Warehouse profile automatically —
`turnserver-init`/`turnserver` service profiles, wired into VST's config at container
startup by `apply_turn_config.sh`, no manual step.

Helm has no bundled TURN server or `turnserver` chart. `global.turnServerUrl` on each app
chart threads a static TURN URL list into `vios-nvstreamer`, `vios-sensor`, and
`vios-streamprocessing`'s `static_turnurl_list` — leave it unset and Helm deploys get no
TURN relay at all (only the two public Google STUN servers).

## Deploying a standalone TURN server

Until Helm ships its own `turnserver` chart, the existing Compose `coturn` service can be
reused standalone — it only depends on its own Docker volume, not the rest of the stack:

```bash
cd deploy/docker
export VSS_APPS_DIR="$(pwd)"
export TURN_USERNAME=vss
export TURN_REALM=vss.local
export TURN_PASSWORD_BYTES=32
# Must be a real, browser-reachable address -- not localhost or a cluster-internal IP.
export TURN_EXTERNAL_IP=<public-ip-or-dns>
export TURN_PUBLIC_HOST=<public-ip-or-dns>
export TURN_PORT=3478
export TURN_HOST_PORT=3478
export TURN_MIN_RELAY_PORT=49160
export TURN_MAX_RELAY_PORT=49200
export TURN_MIN_RELAY_HOST_PORT=49160
export TURN_MAX_RELAY_HOST_PORT=49200

docker compose -f services/infra/compose.yml \
  --profile turnserver-init --profile turnserver up -d
```

What this needs beyond the Compose defaults for a real (non-localhost) deployment:

- **A real public/reachable host.** `TURN_EXTERNAL_IP`/`TURN_PUBLIC_HOST` is what coturn
  advertises in its relay candidates; browsers on other networks connect to this address.
- **Firewall / security-group rules** opening the TURN listener (`3478` UDP+TCP) and the
  full relay port range (`49160-49200` UDP+TCP by default) to whatever clients need
  playback access.
- **The generated password.** `turnserver-init` generates the TURN password once into the
  `vss-turn-password` Docker volume; it is not printed to logs. Read it back out to build
  the connection URL:

  ```bash
  docker run --rm -v deploy_vss-turn-password:/run/secrets/vss-turn:ro alpine \
    cat /run/secrets/vss-turn/turn-password
  ```

  (Volume name may be prefixed by the compose project name — check `docker volume ls`.)

- **The final TURN URL**, in the `username:password@host:port` form VST expects:

  ```
  vss:<password>@<public-ip-or-dns>:3478
  ```

## Pointing a Warehouse Apps Helm deploy at it

Set `global.turnServerUrl` to the URL built above (comma-separate for multiple servers), in
a values override:

```yaml
global:
  turnServerUrl: "vss:<password>@<public-ip-or-dns>:3478"
```
