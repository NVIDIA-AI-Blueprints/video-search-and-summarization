<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# OpenShell Harness for VSS

Runs the Hermes agent harness inside an [NVIDIA OpenShell](https://docs.nvidia.com/openshell/latest/) (`0.1.6`) sandbox against a VSS deployment.

## Layout

| Path | Purpose |
|---|---|
| `deploy/docker/services/openshell/compose.yml` | `openshell-init` + `openshell-gateway` (`--profile openshell`, bound to `127.0.0.1:8080`) |
| `deploy/docker/services/openshell/gateway.toml` | Gateway config (`backend = "docker"`, embedded SQLite control plane) |
| `deploy/docker/services/openshell/openshell.env` | Default tags, gateway endpoint, `HARNESS_PROVIDER`, `HARNESS_BASE_URL`, `HARNESS_MODEL`, and `VSS_PUBLIC_URL` |
| `services/openshell/providers/*.yaml` | Inference provider profiles (`agent-inference`, `agent-inference-build`, `agent-inference-local`) |
| `.hermes/` | Harness `Dockerfile`, `policy.yaml`, `boundary.yaml`, `vss_cli` plugin, and workspace docs |

## Prerequisites

- Linux (`x86_64` or `aarch64`), Docker Engine + Compose v2 (`docker info` without `sudo`), `curl`, `jq`, `openssl`.
- `openshell` and `openshell-prover` `0.1.6` on `PATH`:
  ```bash
  ARCH="$(uname -m)"   # x86_64 or aarch64
  BASE="https://github.com/NVIDIA/OpenShell/releases/download/v0.1.6"
  for bin in openshell openshell-prover; do
    TAR="${bin}-${ARCH}-unknown-linux-musl.tar.gz"
    curl -fsSLO "$BASE/$TAR" && curl -fsSLO "$BASE/$TAR.sha256"
    sha256sum -c "$TAR.sha256"
    tar -xzf "$TAR" -C ~/.local/bin "$bin"
  done
  ```
- Inference API key in `HARNESS_API_KEY` (exported or in gitignored `harness.env` at the repo root).

## Quickstart

Run all commands from the repository root:

### 1. Load environment defaults and verify policy

```bash
set -a
source deploy/docker/services/openshell/openshell.env
[ -f harness.env ] && source harness.env
set +a

openshell-prover check .hermes/policy.yaml --boundary .hermes/boundary.yaml
```

### 2. Start the gateway and register the CLI

```bash
docker compose \
  --env-file deploy/docker/services/openshell/openshell.env \
  -f deploy/docker/services/openshell/compose.yml \
  --profile openshell up -d

openshell gateway add --name vss "$OPENSHELL_GATEWAY_ENDPOINT"
openshell status
```

### 3. Build the Hermes harness image

```bash
VERSION="$(cd libs/vss/cli && uvx --with hatch-vcs hatch version)"
HARNESS_IMAGE="openshell-hermes-vss:${VERSION//+/-}-from-source"
docker build -f .hermes/Dockerfile \
  --build-arg HERMES_REF="$HERMES_REF" \
  --build-arg VSS_PACKAGE_VERSION="$VERSION" \
  -t "$HARNESS_IMAGE" .
```

### 4. Create the inference provider and sandbox

`HARNESS_PROVIDER` selects `services/openshell/providers/<HARNESS_PROVIDER>-profile.yaml`:
- `agent-inference` — `inference-api.nvidia.com:443` (`https://inference-api.nvidia.com/v1`)
- `agent-inference-build` — `integrate.api.nvidia.com:443` (`https://integrate.api.nvidia.com/v1`)
- `agent-inference-local` — `host.openshell.internal:8000` / `:11434` (local vLLM, NIM, or Ollama)

```bash
openshell profile import -f "services/openshell/providers/$HARNESS_PROVIDER-profile.yaml"
openshell provider create --name "$HARNESS_PROVIDER" --type "$HARNESS_PROVIDER" --credential HARNESS_API_KEY

API_SERVER_KEY="$(openssl rand -hex 32)"
openshell sandbox create --name hermes \
  --from "$HARNESS_IMAGE" \
  --policy .hermes/policy.yaml \
  --provider "$HARNESS_PROVIDER" \
  --env "HARNESS_BASE_URL=$HARNESS_BASE_URL" \
  --env "HARNESS_MODEL=$HARNESS_MODEL" \
  --env "API_SERVER_KEY=$API_SERVER_KEY" \
  --port 8642:http --no-tty -- sleep infinity
```

### 5. Verify and connect to VSS

```bash
# Placeholder check: real key never enters the workload environment
openshell sandbox exec -n hermes -- printenv HARNESS_API_KEY
# -> openshell:resolve:env:HARNESS_API_KEY

# Interactive or one-shot turn
openshell sandbox exec -n hermes -- hermes chat -q "Reply with READY."

# Point at a live VSS deployment and sync active skills
openshell sandbox exec -n hermes -- vss configure "$VSS_PUBLIC_URL"
openshell sandbox exec -n hermes -- vss configure check
openshell sandbox exec -n hermes -- vss-hermes-sync
```

To expose the sandbox behind the VSS UI (`:9100`), pass `OPENAI_API_BASE="$(openshell sandbox get hermes -o json | jq -r '.ports[0].url')/v1"`, `OPENAI_API_KEY="$API_SERVER_KEY"`, and `VSS_AGENT_MODEL=hermes-agent` to `vss-ui`.

## Changing Key, Model, or Endpoint

- **Key only**:
  ```bash
  openshell provider update "$HARNESS_PROVIDER" --credential HARNESS_API_KEY --wait
  openshell sandbox stop hermes && openshell sandbox start hermes
  ```
- **Model or endpoint**: update `HARNESS_MODEL`, `HARNESS_BASE_URL`, and `HARNESS_PROVIDER` in `deploy/docker/services/openshell/openshell.env`, re-source it, import/create the new `$HARNESS_PROVIDER` if changed, resolve the current image if `HARNESS_IMAGE` is unset:
  ```bash
  ID="$(docker inspect "$(docker ps -aqf label=openshell.sandbox=hermes)" --format '{{.Config.Image}}')"
  HARNESS_IMAGE="$(docker images --no-trunc --format '{{.ID}} {{.Repository}}:{{.Tag}}' | awk -v id="$ID" '$1 == id {print $2; exit}')"
  openshell sandbox delete hermes
  ```
  and re-run `openshell sandbox create` from Step 4.

## Teardown

```bash
openshell sandbox delete hermes
openshell provider delete "$HARNESS_PROVIDER"
openshell gateway remove vss
docker compose \
  --env-file deploy/docker/services/openshell/openshell.env \
  -f deploy/docker/services/openshell/compose.yml \
  --profile openshell stop
```
