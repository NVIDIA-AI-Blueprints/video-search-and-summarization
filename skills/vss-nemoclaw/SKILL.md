---
name: vss-nemoclaw
description: Use to create and operate a NemoClaw express sandbox for VSS. Covers express sandbox creation from nemoclaw-sandbox:local, provider configuration, OpenClaw runtime patching, VSS plugin setup, recovery, status, and dashboard access.
license: Apache-2.0
metadata:
  version: "3.2.0"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational deployment nemoclaw openclaw sandbox"
---

# VSS NemoClaw

## Purpose

Create and operate a NemoClaw/OpenClaw sandbox for VSS using the express path. The express path prepares or reuses `nemoclaw-sandbox:local`, creates the sandbox directly through OpenShell, patches the OpenClaw runtime config, installs the VSS OpenClaw plugin, and uses the NemoClaw CLI for recovery/status/dashboard checks.

## Instructions

Run the sandbox creation workflow in this `SKILL.md` top-to-bottom. The required NemoClaw, OpenShell, Docker, Node, and npm prerequisites must already be installed and available in the current shell.

The workflow intentionally does not use full `nemoclaw onboard` for sandbox creation, because that would bypass the express/direct OpenShell creation path.

## Provider `.env` Example

From the repository root, put provider settings in `.env`. For the current custom endpoint shape:

```bash
NEMOCLAW_PROVIDER=custom
NEMOCLAW_ENDPOINT_URL=https://inference-api.nvidia.com/v1
NEMOCLAW_MODEL=aws/anthropic/bedrock-claude-opus-4-6
COMPATIBLE_API_KEY=sk-xxx
NEMOCLAW_SANDBOX_NAME=demo
```

## Prerequisites

- Docker must be available on the host.
- `node`, `npm`, `npx`, `nemoclaw`, and `openshell` must be available in `PATH`.
- `$NEMOCLAW_SRC` must point to a built NemoClaw source tree with `dist/lib/onboard.js` and `dist/lib/agent/runtime.js`.
- Provider credentials must be present in the environment or `.env`.
- The VSS repo must be available locally so the VSS OpenClaw plugin can stage `.openclaw` and `skills`.

## Load Provider Environment

Load provider and sandbox settings from `.env` in the current working directory. Override with `NEMOCLAW_EXPRESS_ENV_FILE=/path/to/file.env`.

```bash
ENV_FILE="${NEMOCLAW_EXPRESS_ENV_FILE:-.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
```

## Set Runtime Variables

Set the common variables once and reuse them through the image build, sandbox creation, config patch, plugin install, and dashboard forward:

```bash
export NEMOCLAW_SRC="${NEMOCLAW_SRC:-$HOME/.nemoclaw/source}"
export NEMOCLAW_SANDBOX_NAME="${NEMOCLAW_SANDBOX_NAME:-demo}"
export NEMOCLAW_PROVIDER="${NEMOCLAW_PROVIDER:-build}"
export NEMOCLAW_DASHBOARD_PORT="${NEMOCLAW_DASHBOARD_PORT:-18789}"
export GATEWAY_ENDPOINT="${NEMOCLAW_OPENSHELL_GATEWAY_ENDPOINT:-http://127.0.0.1:8080}"

case "$NEMOCLAW_PROVIDER" in
  build | cloud | nvidia | nvidia-nim | nvidia-prod)
    export NEMOCLAW_PROVIDER="build"
    export DEFAULT_NEMOCLAW_MODEL="nvidia/nemotron-3-super-120b-a12b"
    export GATEWAY_PROVIDER_NAME="nvidia-nim"
    export PROVIDER_TYPE="openai"
    export CREDENTIAL_ENV="NVIDIA_API_KEY"
    export ENDPOINT_CONFIG_KEY="OPENAI_BASE_URL"
    export ENDPOINT_URL="https://integrate.api.nvidia.com/v1"
    export NEMOCLAW_PROVIDER_KEY="inference"
    export NEMOCLAW_OPENCLAW_BASE_URL="https://inference.local/v1"
    export NEMOCLAW_OPENCLAW_API="openai-completions"
    ;;
  custom)
    export DEFAULT_NEMOCLAW_MODEL=""
    export GATEWAY_PROVIDER_NAME="compatible-endpoint"
    export PROVIDER_TYPE="openai"
    export CREDENTIAL_ENV="COMPATIBLE_API_KEY"
    export ENDPOINT_CONFIG_KEY="OPENAI_BASE_URL"
    export ENDPOINT_URL="$NEMOCLAW_ENDPOINT_URL"
    export NEMOCLAW_PROVIDER_KEY="inference"
    export NEMOCLAW_OPENCLAW_BASE_URL="https://inference.local/v1"
    export NEMOCLAW_OPENCLAW_API="openai-completions"
    ;;
  openai)
    export DEFAULT_NEMOCLAW_MODEL="gpt-5.4"
    export GATEWAY_PROVIDER_NAME="openai-api"
    export PROVIDER_TYPE="openai"
    export CREDENTIAL_ENV="OPENAI_API_KEY"
    export ENDPOINT_CONFIG_KEY="OPENAI_BASE_URL"
    export ENDPOINT_URL="https://api.openai.com/v1"
    export NEMOCLAW_PROVIDER_KEY="openai"
    export NEMOCLAW_OPENCLAW_BASE_URL="https://inference.local/v1"
    export NEMOCLAW_OPENCLAW_API="openai-completions"
    ;;
  anthropic)
    export DEFAULT_NEMOCLAW_MODEL="claude-sonnet-4-6"
    export GATEWAY_PROVIDER_NAME="anthropic-prod"
    export PROVIDER_TYPE="anthropic"
    export CREDENTIAL_ENV="ANTHROPIC_API_KEY"
    export ENDPOINT_CONFIG_KEY="ANTHROPIC_BASE_URL"
    export ENDPOINT_URL="https://api.anthropic.com"
    export NEMOCLAW_PROVIDER_KEY="anthropic"
    export NEMOCLAW_OPENCLAW_BASE_URL="https://inference.local"
    export NEMOCLAW_OPENCLAW_API="anthropic-messages"
    ;;
  gemini)
    export DEFAULT_NEMOCLAW_MODEL="gemini-2.5-flash"
    export GATEWAY_PROVIDER_NAME="gemini-api"
    export PROVIDER_TYPE="openai"
    export CREDENTIAL_ENV="GEMINI_API_KEY"
    export ENDPOINT_CONFIG_KEY="OPENAI_BASE_URL"
    export ENDPOINT_URL="https://generativelanguage.googleapis.com/v1beta/openai/"
    export NEMOCLAW_PROVIDER_KEY="inference"
    export NEMOCLAW_OPENCLAW_BASE_URL="https://inference.local/v1"
    export NEMOCLAW_OPENCLAW_API="openai-completions"
    export NEMOCLAW_OPENCLAW_COMPAT="supportsStore=false"
    ;;
  *)
    echo "Unsupported NEMOCLAW_PROVIDER: $NEMOCLAW_PROVIDER" >&2
    exit 1
    ;;
esac

export NEMOCLAW_MODEL="${NEMOCLAW_MODEL:-$DEFAULT_NEMOCLAW_MODEL}"
export NEMOCLAW_PRIMARY_MODEL_REF="${NEMOCLAW_PROVIDER_KEY}/${NEMOCLAW_MODEL}"

CREDENTIAL_VALUE="${!CREDENTIAL_ENV:-}"
if [[ -z "$CREDENTIAL_VALUE" ]]; then
  echo "$CREDENTIAL_ENV is required for provider $NEMOCLAW_PROVIDER" >&2
  exit 1
fi
```

## Prepare `nemoclaw-sandbox:local`

The express path prepares a local sandbox image. It first checks whether the image already exists:

```bash
docker image inspect nemoclaw-sandbox:local
```

To force a fresh local image, remove the old image before building:

```bash
docker rmi nemoclaw-sandbox:local
```

If the image is missing and a cached image exists, load it:

```bash
if ! docker image inspect nemoclaw-sandbox:local >/dev/null 2>&1 \
  && [[ -f /var/cache/nemoclaw/sandbox-image.tar ]]; then
  docker load -i /var/cache/nemoclaw/sandbox-image.tar
fi
```

If no cached image is available, resolve the sandbox base image before building:

```bash
BASE_TAG="${NEMOCLAW_SANDBOX_BASE_TAG:-}"

if [[ -z "$BASE_TAG" && -f "${NEMOCLAW_SRC}/.version" ]]; then
  BASE_VERSION="$(tr -d '[:space:]' <"${NEMOCLAW_SRC}/.version")"
  [[ -n "$BASE_VERSION" ]] && BASE_TAG="v${BASE_VERSION#v}"
fi

if [[ -z "$BASE_TAG" && -f "${NEMOCLAW_SRC}/package.json" ]]; then
  BASE_VERSION="$(
    sed -nE 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' \
      "${NEMOCLAW_SRC}/package.json" | head -1
  )"
  [[ -n "$BASE_VERSION" && "$BASE_VERSION" != "0.0.0" ]] && BASE_TAG="v${BASE_VERSION#v}"
fi

BASE_TAG="${BASE_TAG:-${NEMOCLAW_INSTALL_REF:-latest}}"
BASE_TAG="${BASE_TAG#refs/tags/}"

case "$BASE_TAG" in
  latest) BASE_IMAGE="ghcr.io/nvidia/nemoclaw/sandbox-base:latest" ;;
  v*) BASE_IMAGE="ghcr.io/nvidia/nemoclaw/sandbox-base:${BASE_TAG}" ;;
  *) BASE_IMAGE="ghcr.io/nvidia/nemoclaw/sandbox-base:v${BASE_TAG}" ;;
esac

if [[ "$BASE_TAG" != "latest" ]] && ! docker manifest inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  BASE_IMAGE="ghcr.io/nvidia/nemoclaw/sandbox-base:latest"
fi

docker pull "$BASE_IMAGE"
```

Create the temporary build context from the NemoClaw source tree:

```bash
BUILD_CTX="$(mktemp -d)"

cp "$NEMOCLAW_SRC/Dockerfile" "$BUILD_CTX/"
cp -r "$NEMOCLAW_SRC/nemoclaw" "$BUILD_CTX/nemoclaw"
cp -r "$NEMOCLAW_SRC/nemoclaw-blueprint" "$BUILD_CTX/nemoclaw-blueprint"
cp -r "$NEMOCLAW_SRC/scripts" "$BUILD_CTX/scripts"

rm -rf "$BUILD_CTX/nemoclaw/node_modules"
```

Build the local image:

```bash
OPENCLAW_VERSION="$(
  awk '/^ARG OPENCLAW_VERSION=/ { sub(/^ARG OPENCLAW_VERSION=/, ""); print; exit }' \
    "$NEMOCLAW_SRC/Dockerfile" 2>/dev/null || true
)"

docker build \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --build-arg NEMOCLAW_DISABLE_DEVICE_AUTH=1 \
  ${OPENCLAW_VERSION:+--build-arg "OPENCLAW_VERSION=$OPENCLAW_VERSION"} \
  -t nemoclaw-sandbox:local \
  "$BUILD_CTX"
```

Clean up the temporary build context after the build:

```bash
rm -rf "$BUILD_CTX"
```

## Create And Start The Sandbox

The express path creates the sandbox directly through OpenShell instead of running full `nemoclaw onboard`.

Register or select the local gateway:

```bash
if openshell gateway select nemoclaw >/dev/null 2>&1 \
  && openshell status 2>&1 \
    | sed -E 's/\x1B\[[0-9;]*[[:alpha:]]//g' \
    | grep -qiE 'Status:.*Connected'; then
  :
else
  openshell gateway remove nemoclaw >/dev/null 2>&1 || true
  openshell gateway add "$GATEWAY_ENDPOINT" --local --name nemoclaw
  openshell gateway select nemoclaw
fi
```

Start the gateway only when OpenShell is not connected:

```bash
if ! openshell status 2>&1 \
  | sed -E 's/\x1B\[[0-9;]*[[:alpha:]]//g' \
  | grep -qiE 'Status:.*Connected'; then
  node -e '
    const onboard = require(process.argv[1]);
    onboard.startGatewayForRecovery(null)
      .then(() => process.exit(0))
      .catch((e) => { console.error(e.message || e); process.exit(1); });
  ' "$NEMOCLAW_SRC/dist/lib/onboard.js"
fi
```

Create or update the OpenShell provider:

```bash
PROVIDER_LOG="$(mktemp)"

if openshell provider create \
  --name "$GATEWAY_PROVIDER_NAME" \
  --type "$PROVIDER_TYPE" \
  --credential "${CREDENTIAL_ENV}=${CREDENTIAL_VALUE}" \
  --config "${ENDPOINT_CONFIG_KEY}=${ENDPOINT_URL}" >"$PROVIDER_LOG" 2>&1; then
  rm -f "$PROVIDER_LOG"
elif grep -q "AlreadyExists" "$PROVIDER_LOG"; then
  rm -f "$PROVIDER_LOG"
  openshell provider update "$GATEWAY_PROVIDER_NAME" \
    --credential "${CREDENTIAL_ENV}=${CREDENTIAL_VALUE}" \
    --config "${ENDPOINT_CONFIG_KEY}=${ENDPOINT_URL}"
else
  sed -E 's/(API_KEY=)[^ ]+/\1[redacted]/g' "$PROVIDER_LOG" >&2
  rm -f "$PROVIDER_LOG"
  exit 1
fi
```

Set the inference route:

```bash
openshell inference set \
  --provider "$GATEWAY_PROVIDER_NAME" \
  --model "$NEMOCLAW_MODEL" \
  --no-verify
```

Delete an existing sandbox with the same name:

```bash
openshell sandbox delete "$NEMOCLAW_SANDBOX_NAME"
```

Create the sandbox from the local image:

```bash
openshell sandbox create \
  --from nemoclaw-sandbox:local \
  --name "$NEMOCLAW_SANDBOX_NAME" \
  --policy "$NEMOCLAW_SRC/nemoclaw-blueprint/policies/openclaw-sandbox.yaml" \
  --provider "$GATEWAY_PROVIDER_NAME" \
  -- env "${CREDENTIAL_ENV}=${CREDENTIAL_VALUE}"
```

Verify the sandbox is ready:

```bash
openshell sandbox list
```

Register the sandbox for NemoClaw:

```bash
mkdir -p "$HOME/.nemoclaw"
cat >"$HOME/.nemoclaw/sandboxes.json" <<JSON
{
  "sandboxes": {
    "$NEMOCLAW_SANDBOX_NAME": {
      "name": "$NEMOCLAW_SANDBOX_NAME",
      "model": "$NEMOCLAW_MODEL",
      "provider": "$GATEWAY_PROVIDER_NAME",
      "agent": "openclaw"
    }
  },
  "defaultSandbox": "$NEMOCLAW_SANDBOX_NAME"
}
JSON
chmod 600 "$HOME/.nemoclaw/sandboxes.json"
```

Patch the OpenClaw config inside the sandbox:

```bash
TMPCONF="$(mktemp -d)"

openshell sandbox download \
  "$NEMOCLAW_SANDBOX_NAME" \
  /sandbox/.openclaw/openclaw.json \
  "$TMPCONF"

TMPCONF="$TMPCONF" \
NEMOCLAW_MODEL="$NEMOCLAW_MODEL" \
NEMOCLAW_PROVIDER_KEY="$NEMOCLAW_PROVIDER_KEY" \
NEMOCLAW_PRIMARY_MODEL_REF="$NEMOCLAW_PRIMARY_MODEL_REF" \
NEMOCLAW_OPENCLAW_BASE_URL="$NEMOCLAW_OPENCLAW_BASE_URL" \
NEMOCLAW_OPENCLAW_API="$NEMOCLAW_OPENCLAW_API" \
NEMOCLAW_OPENCLAW_COMPAT="${NEMOCLAW_OPENCLAW_COMPAT:-}" \
CHAT_UI_URL="${CHAT_UI_URL:-}" \
python3 <<'PY'
import json
import os
import secrets

path = os.path.join(os.environ["TMPCONF"], "openclaw.json")
with open(path, "r", encoding="utf-8") as fh:
    cfg = json.load(fh)

model = os.environ["NEMOCLAW_MODEL"].strip()
provider_key = os.environ.get("NEMOCLAW_PROVIDER_KEY", "inference").strip() or "inference"
primary_model_ref = os.environ.get("NEMOCLAW_PRIMARY_MODEL_REF", "").strip()
base_url = os.environ.get("NEMOCLAW_OPENCLAW_BASE_URL", "https://inference.local/v1").strip()
api = os.environ.get("NEMOCLAW_OPENCLAW_API", "openai-completions").strip()
compat = os.environ.get("NEMOCLAW_OPENCLAW_COMPAT", "").strip()
chat_ui_url = os.environ.get("CHAT_UI_URL", "").strip()

providers = cfg.setdefault("models", {}).setdefault("providers", {})
provider = providers.get(provider_key)
if not isinstance(provider, dict):
    provider = {}
provider["baseUrl"] = base_url
provider["apiKey"] = provider.get("apiKey") or "unused"
provider["api"] = api
if compat:
    for item in compat.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            provider[key.strip()] = value.strip().lower() == "true"
existing_model = next((entry for entry in provider.get("models", []) if isinstance(entry, dict)), {})
existing_model["id"] = model
existing_model["name"] = primary_model_ref or f"{provider_key}/{model}"
provider["models"] = [existing_model]
providers.clear()
providers[provider_key] = provider
cfg["models"]["mode"] = "merge"
cfg.setdefault("agents", {}).setdefault("defaults", {}).setdefault("model", {})["primary"] = primary_model_ref or f"{provider_key}/{model}"

cfg.setdefault("gateway", {}).setdefault("auth", {})["token"] = secrets.token_hex(32)
origins = cfg.setdefault("gateway", {}).setdefault("controlUi", {}).get("allowedOrigins", [])
for origin in ["http://127.0.0.1", "http://127.0.0.1:80", "http://localhost", chat_ui_url]:
    if origin and origin not in origins:
        origins.append(origin)
cfg["gateway"]["controlUi"]["allowedOrigins"] = [origin for origin in origins if origin]

with open(path, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
os.chmod(path, 0o600)
PY

DASHBOARD_TOKEN="$(
  python3 -c "import json; print(json.load(open('$TMPCONF/openclaw.json')).get('gateway',{}).get('auth',{}).get('token',''))"
)"

openshell sandbox upload \
  "$NEMOCLAW_SANDBOX_NAME" \
  "$TMPCONF/openclaw.json" \
  /sandbox/.openclaw/openclaw.json

rm -rf "$TMPCONF"
```

## Install The VSS OpenClaw Plugin

Install the VSS plugin after the sandbox exists and `/sandbox/.openclaw/openclaw.json` has been patched. The plugin source is the repo's `.openclaw` directory plus the repo `skills` directory.

Set the local and remote paths:

```bash
export VSS_REPO_DIR="${VSS_REPO_DIR:-$PWD}"
export OPENCLAW_PLUGIN_DIR="${OPENCLAW_PLUGIN_DIR:-$VSS_REPO_DIR/.openclaw}"
export OPENCLAW_PLUGIN_VARIANT="${OPENCLAW_PLUGIN_VARIANT:-nemoclaw}"
export REMOTE_PLUGIN_DIR="/tmp/vss-openclaw-plugin"
```

Stage the plugin locally:

```bash
STAGE="$(mktemp -d)"
cp -a "$OPENCLAW_PLUGIN_DIR/." "$STAGE/"
cp -a "$VSS_REPO_DIR/skills" "$STAGE/skills"
rm -f "$STAGE/index.ts"
```

Compile the OpenClaw entrypoint to JavaScript:

```bash
npx --yes esbuild "$OPENCLAW_PLUGIN_DIR/index.ts" \
  --platform=node \
  --format=esm \
  --outfile="$STAGE/index.js"
```

Patch `package.json` so OpenClaw loads the compiled entrypoint and packages the skills/workspace assets:

```bash
STAGE="$STAGE" node <<'NODE'
const fs = require("fs");
const path = require("path");
const pkgPath = path.join(process.env.STAGE, "package.json");
const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
pkg.openclaw = { extensions: ["./index.js"] };
const files = new Set([
  ...(pkg.files || []),
  "index.js",
  "skills/",
  "workspace/",
  "openclaw.plugin.json",
  "README.md",
]);
pkg.files = [...files].filter((f) => f !== "index.ts");
fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n");
NODE
```

Copy the staged plugin into a local Docker-backed sandbox:

```bash
SANDBOX_CONTAINER="$(
  docker ps --format '{{.Names}}' \
    | awk -v sandbox="$NEMOCLAW_SANDBOX_NAME" '$0 ~ "^openshell-" sandbox "-" { print; exit }'
)"

docker exec -u 0 "$SANDBOX_CONTAINER" rm -rf "$REMOTE_PLUGIN_DIR"
docker exec -u 0 "$SANDBOX_CONTAINER" mkdir -p "$REMOTE_PLUGIN_DIR"
docker cp "$STAGE/." "$SANDBOX_CONTAINER:$REMOTE_PLUGIN_DIR/"
docker exec -u 0 "$SANDBOX_CONTAINER" sh -lc \
  'mkdir -p /sandbox/.openclaw/workspace && chown -R sandbox:sandbox /sandbox/.openclaw/workspace'
```

Install the plugin inside the sandbox:

```bash
openshell sandbox exec -n "$NEMOCLAW_SANDBOX_NAME" -- sh -lc \
  "OPENCLAW_PLUGIN_VARIANT=$OPENCLAW_PLUGIN_VARIANT openclaw plugins install $REMOTE_PLUGIN_DIR --force --dangerously-force-unsafe-install"
```

Reload OpenClaw so the plugin takes effect. Prefer the NemoClaw CLI for recovery because it owns the current gateway and dashboard-forward behavior:

```bash
nemoclaw "$NEMOCLAW_SANDBOX_NAME" recover
```

Verify status and dashboard access with the NemoClaw CLI:

```bash
nemoclaw status || true
nemoclaw "$NEMOCLAW_SANDBOX_NAME" dashboard-url --quiet || true
```

If the dashboard URL is unavailable, refresh the direct OpenShell port forward:

```bash
openshell forward stop "$NEMOCLAW_DASHBOARD_PORT" "$NEMOCLAW_SANDBOX_NAME" >/dev/null 2>&1 || true
openshell forward start --background "$NEMOCLAW_DASHBOARD_PORT" "$NEMOCLAW_SANDBOX_NAME"
```

## Answering Guidance

When explaining this flow:

1. Separate image preparation from sandbox creation.
2. Prefer the concrete commands above.
3. Include provider-specific substitutions when the user gives provider env details.
4. Call out that the express path is fast because it reuses `nemoclaw-sandbox:local` or `/var/cache/nemoclaw/sandbox-image.tar` and skips full onboard.
