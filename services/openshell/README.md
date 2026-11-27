<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# OpenShell Harness for VSS

Runs the Hermes agent harness inside an [NVIDIA OpenShell](https://docs.nvidia.com/openshell/latest/) (`0.1.6`) sandbox against a VSS deployment.

## Layout

| Path | Purpose |
|---|---|
| `deploy/docker/services/openshell/compose.yml` | `openshell-init` + `openshell-gateway` (`--profile openshell`, bound to `127.0.0.1:8080`, health on `127.0.0.1:8082`) |
| `deploy/docker/services/openshell/gateway.toml` | Version-2 gateway config (`compute_driver = "docker"`, embedded SQLite control plane) |
| `deploy/docker/services/openshell/openshell.env` | Default tags, gateway endpoint, `HARNESS_PROVIDER`, `HARNESS_BASE_URL`, `HARNESS_MODEL`, and `VSS_PUBLIC_URL` |
| `services/openshell/providers/*.yaml` | Inference provider profiles (`agent-inference`, `agent-inference-build`, `agent-inference-local`) |
| `.hermes/` | Harness `Dockerfile`, `policy.yaml`, `boundary.yaml`, `vss_cli` plugin, and workspace docs |

## Prerequisites

- Linux (`x86_64` or `aarch64`), Docker Engine + Compose v2 (`docker info` without `sudo`), `curl`, `jq`.
- Put `HARNESS_API_KEY` (unquoted; for `agent-inference-local`, use any non-empty placeholder such as `HARNESS_API_KEY=not-used`) and any overrides for `HARNESS_PROVIDER`, `HARNESS_BASE_URL`, `HARNESS_MODEL`, or `VSS_PUBLIC_URL` in gitignored `harness.env` at the repository root. The setup sources `openshell.env` first and `harness.env` second, so `harness.env` wins without dirtying tracked `openshell.env` (whereas bare shell exports of keys defined in `openshell.env` are overwritten when `openshell.env` is sourced).

## Quickstart

Run all commands from the repository root:

### 1. Install CLI tools, load settings, and verify policy

```bash
mkdir -p ~/.local/bin && export PATH="$HOME/.local/bin:$PATH"
command -v uvx >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

set -a; . deploy/docker/services/openshell/openshell.env; [ ! -f harness.env ] || . ./harness.env; set +a
case "$HARNESS_API_KEY" in ''|\"*|\'*) echo "HARNESS_API_KEY must be set unquoted" >&2; false;; esac

( cd "$(mktemp -d)" && V="$OPENSHELL_TAG" A="$(uname -m)" &&
  for p in openshell openshell-prover; do
    curl -fsSLO "https://github.com/NVIDIA/OpenShell/releases/download/v$V/$p-$A-unknown-linux-musl.tar.gz"
    curl -fsSLO "https://github.com/NVIDIA/OpenShell/releases/download/v$V/$p-checksums-sha256.txt"
    grep " $p-$A-unknown-linux-musl.tar.gz\$" "$p-checksums-sha256.txt" | sha256sum -c - &&
    tar -xzf "$p-$A-unknown-linux-musl.tar.gz" -C ~/.local/bin
  done )
openshell --version && openshell-prover --version
openshell-prover check .hermes/policy.yaml --boundary .hermes/boundary.yaml
```

### 2. Start and select the OpenShell gateway

```bash
docker compose --env-file deploy/docker/services/openshell/openshell.env \
  -f deploy/docker/services/openshell/compose.yml --profile openshell up -d
timeout 30 sh -c 'until curl -fs -o /dev/null http://127.0.0.1:8082/health; do sleep 1; done'

GW=$(openshell gateway list | awk -v ep="$OPENSHELL_GATEWAY_ENDPOINT" 'NF>4 && $(NF-3)==ep {print $(NF-4)}')
[ -n "$GW" ] || {
  openshell gateway add "$OPENSHELL_GATEWAY_ENDPOINT" --local --name openshell-gateway
  GW=openshell-gateway
}
openshell gateway select "$GW"
```

> **Developer profile integration**: A VSS developer profile can start the gateway itself — append `openshell` to `COMPOSE_PROFILES` in its `overrides.env` (on `dev-profile-alerts`, append `openshell` to both `COMPOSE_PROFILES_CV` and `COMPOSE_PROFILES_VLM`). When a profile already runs the gateway under its own `COMPOSE_PROJECT_NAME`, skip `docker compose ... up -d` above so a second container does not compete for `127.0.0.1:8080`, and run only the health wait and `openshell gateway` selection lines.

### 3. Build (or pull) the Hermes harness image

```bash
VERSION="$(cd libs/vss/cli && uvx --with hatch-vcs hatch version)"
HARNESS_IMAGE="openshell-hermes-vss:${VERSION//+/-}-from-source"
docker build -f .hermes/Dockerfile \
  --build-arg HERMES_REF="$HERMES_REF" \
  --build-arg VSS_PACKAGE_VERSION="$VERSION" \
  -t "$HARNESS_IMAGE" .
```

To use a published GHCR build instead, set `HARNESS_IMAGE="ghcr.io/nvidia-ai-blueprints/video-search-and-summarization/openshell-hermes-vss:<tag>"` and `docker pull "$HARNESS_IMAGE"`.

### 4. Register the inference provider and create the sandbox

`HARNESS_PROVIDER` selects `services/openshell/providers/<HARNESS_PROVIDER>-profile.yaml`:
- `agent-inference` (default) — `inference-api.nvidia.com:443` (`https://inference-api.nvidia.com/v1`)
- `agent-inference-build` — `integrate.api.nvidia.com:443` (`https://integrate.api.nvidia.com/v1`)
- `agent-inference-local` — `host.openshell.internal:8000` / `:11434` (host-local NIM, vLLM, or Ollama; still requires a non-empty placeholder `HARNESS_API_KEY=not-used`)

```bash
openshell profile import -f "services/openshell/providers/$HARNESS_PROVIDER-profile.yaml"
openshell provider create --name "$HARNESS_PROVIDER" --type "$HARNESS_PROVIDER" \
  --credential HARNESS_API_KEY

openshell sandbox create \
  --name hermes \
  --from "$HARNESS_IMAGE" \
  --policy .hermes/policy.yaml \
  --provider "$HARNESS_PROVIDER" \
  --env HARNESS_BASE_URL="$HARNESS_BASE_URL" \
  --env HARNESS_MODEL="$HARNESS_MODEL" \
  --env VSS_PUBLIC_URL="$VSS_PUBLIC_URL" \
  --restart-policy on-failure \
  --expose 8642 --expose-authorization-mode bearer-passthrough --detach \
  -- sh -c 'E=/sandbox/.hermes/.env
            grep -q "^API_SERVER_KEY=" "$E" 2>/dev/null || {
              K=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d " \n")
              printf "API_SERVER_KEY=%s\n" "$K" >> "$E"; }
            chmod 640 "$E"
            exec hermes gateway run'
```

### 5. Verify and point the sandbox at VSS

```bash
# Placeholder check: real key never enters the workload environment
openshell sandbox exec -n hermes -- printenv HARNESS_API_KEY
# -> openshell:resolve:env:HARNESS_API_KEY

# Shell turn and exposed HTTP API check
openshell sandbox exec -n hermes -- hermes chat -Q -q 'Reply with the single word: pong'
timeout 60 sh -c 'until curl -fs -o /dev/null http://default--hermes.openshell.localhost:8080/health; do sleep 2; done'
KEY=$(openshell sandbox exec -n hermes --no-tty -- \
  sh -c '. /sandbox/.hermes/.env; printf %s "$API_SERVER_KEY"')
curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  http://default--hermes.openshell.localhost:8080/v1/chat/completions \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"Reply with: pong"}]}'

# Point at a live VSS deployment and sync active operational skills
openshell sandbox exec -n hermes -- sh -c 'vss configure --base-url "$VSS_PUBLIC_URL"'
openshell sandbox exec -n hermes -- vss configure check
openshell sandbox exec -n hermes -- vss-hermes-sync
```

To copy a host video file into the sandbox workspace, run `openshell sandbox upload hermes <path/to/video.mp4> /sandbox/`.

## Optional: Connect the VSS Web UI Chat

`vss-ui` runs on a Docker bridge network and cannot reach the gateway's host-loopback listener (`127.0.0.1:8080`). Publish the sandbox's `:8642` API server on the Docker bridge gateway address (`host.docker.internal` inside `vss-ui`) and read the sandbox's `API_SERVER_KEY`:

```bash
HG=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')
openshell forward start -d "$HG:8642" hermes   # do not pipe: -d holds stdout open
openshell sandbox exec -n hermes --no-tty -- \
  sh -c '. /sandbox/.hermes/.env; printf "AGENT_BACKEND_TOKEN: \"%s\"\n" "$API_SERVER_KEY"'
```

For an already-running `_builds/<build>` deployment, write `_builds/<build>/ui-hermes.override.yml` (substituting `<API_SERVER_KEY>`) and recreate `vss-ui`:

```yaml
# _builds/<build>/ui-hermes.override.yml
services:
  vss-ui:
    environment:
      AGENT_ADAPTER_ENABLED: "true"
      AGENT_BACKEND_PROTOCOL: responses
      AGENT_BACKEND_URL: http://host.docker.internal:8642
      AGENT_BACKEND_MODEL: hermes-agent
      AGENT_BACKEND_TOKEN: "<API_SERVER_KEY>"
      NEXT_PUBLIC_AGENT_ADAPTER_ENABLED: "true"
      NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT: "true"
      NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR: "true"
      NEXT_PUBLIC_ENABLE_CHAT_TAB: "true"
```

```bash
cd _builds/<build>
docker compose -p "$(docker inspect vss-agent-ui \
  --format '{{index .Config.Labels "com.docker.compose.project"}}')" \
  -f resolved.yml -f ui-hermes.override.yml up -d --no-deps vss-ui
```

For a build not yet started, place `VSS_AGENT_ADAPTER_ENABLED=true`, `VSS_AGENT_BACKEND_PROTOCOL=responses`, `VSS_AGENT_BACKEND_URL=http://host.docker.internal:8642`, `VSS_AGENT_BACKEND_MODEL=hermes-agent`, `VSS_AGENT_BACKEND_TOKEN=<API_SERVER_KEY>`, `NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR=true`, and `NEXT_PUBLIC_ENABLE_CHAT_TAB=true` in `_builds/<build>/override.env` instead.

## Changing Key, Model, or Endpoint

Edit `harness.env` at the repo root and reload:

```bash
set -a; . deploy/docker/services/openshell/openshell.env; [ ! -f harness.env ] || . ./harness.env; set +a
```

- **Key only** (preserves `/sandbox` and `API_SERVER_KEY`; restart any `openshell forward` afterwards):
  ```bash
  openshell provider update "$HARNESS_PROVIDER" --credential HARNESS_API_KEY --wait
  openshell sandbox stop hermes && openshell sandbox start hermes
  ```
- **Model, endpoint, or `VSS_PUBLIC_URL`**: if switching to a new `$HARNESS_PROVIDER`, import its profile and create the provider (or `openshell provider update` if it already exists; use `HARNESS_API_KEY=not-used` for `agent-inference-local`). Resolve the running image if `HARNESS_IMAGE` is unset, delete the sandbox, re-run `openshell sandbox create` from Step 4, then **re-run Step 5 (`vss configure --base-url` + `vss-hermes-sync`) and the UI forward + `AGENT_BACKEND_TOKEN` update** (recreating the sandbox wipes `/sandbox` and mints a new `API_SERVER_KEY`):
  ```bash
  ID=$(docker ps -a --format '{{.Image}}' \
    --filter label=openshell.ai/sandbox-name=hermes \
    --filter label=openshell.ai/isolation-role=sandbox)
  HARNESS_IMAGE="${HARNESS_IMAGE:-$(docker images --format '{{.ID}} {{.Repository}}:{{.Tag}}' |
    awk -v id="$ID" '$1 == id || $2 == id {print $2; exit}')}"
  : "${HARNESS_IMAGE:?set HARNESS_IMAGE to an openshell-hermes-vss:<tag> image}"
  docker image inspect "$HARNESS_IMAGE" >/dev/null && openshell sandbox delete hermes
  ```

## Troubleshooting

- **Sandbox status, active policy, and workload logs**: `openshell sandbox list`, `openshell policy get hermes --full`, and `openshell logs hermes`.
- **Policy / egress `Connection error` or `403`**: inspect the sibling supervisor container's log (names the blocked host and rule):
  ```bash
  docker logs "$(docker ps -q \
    --filter label=openshell.ai/isolation-role=supervisor \
    --filter label=openshell.ai/sandbox-name=hermes)"
  ```
- **Gateway startup**: `docker compose --env-file deploy/docker/services/openshell/openshell.env -f deploy/docker/services/openshell/compose.yml --profile openshell logs openshell-gateway`.
- **UI `/api/agent/connection` says `connected` but chat runs fail**: on `responses`, `/api/agent/connection` does not dial Hermes. Check `openshell forward list` (forwards do not survive host reboots or sandbox recreation) and ensure `AGENT_BACKEND_TOKEN` matches `/sandbox/.hermes/.env`.

## Teardown

Delete sandboxes through `openshell` **before** running `dev-profile.sh down` or `docker compose down -v` (sandbox containers are gateway siblings, and `-v` wipes `openshell-data` along with sealed provider keys):

```bash
openshell forward stop 8642 hermes 2>/dev/null || true
openshell sandbox delete hermes
# Only if no other sandboxes share this gateway (check: openshell sandbox list --all-workspaces):
openshell provider delete "$HARNESS_PROVIDER"
openshell gateway remove "${GW:-openshell-gateway}"
docker compose --env-file deploy/docker/services/openshell/openshell.env \
  -f deploy/docker/services/openshell/compose.yml --profile openshell stop
```
