# Deployment

Use the same build artifacts and resolved Compose lifecycle for stock and delta
builds. Before resolving:

- confirm Docker, the NVIDIA runtime, and the requested GPUs are available;
- export `NGC_CLI_API_KEY` for local NVIDIA images/models;
- export the required API key for explicitly requested remote endpoints;
- set or confirm host paths and browser-reachable ingress values;
- check the selected profile reference for stock-specific knobs and readiness.

## Resolve

Never copy or edit the Foundation files. Generate the exact deployment model
from the root Compose graph, optional changed-service patches, and four ordered
env layers:

```bash
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"
FOUNDATION="$(sed -n 's/^FOUNDATION=//p' "$BUILD_DIR/override.env")"
FOUNDATION_DIR="$REPO/deploy/docker/developer-profiles/dev-profile-$FOUNDATION"

env_args=(
  --env-file "$REPO/deploy/docker/containers.env"
  --env-file "$FOUNDATION_DIR/.env"
  --env-file "$FOUNDATION_DIR/overrides.env"
  --env-file "$BUILD_DIR/override.env"
)

docker compose "${env_args[@]}" \
  -f "$BUILD_DIR/compose.yml" \
  config --no-consistency > "$BUILD_DIR/resolved.yml"

uv run "$REPO/skills/vss-build-vision-agent/scripts/normalize_resolved_yml.py" \
  "$BUILD_DIR/resolved.yml"

uv run "$REPO/skills/vss-build-vision-agent/scripts/validate_resolved_yml.py" \
  "$BUILD_DIR/resolved.yml" --repo-root "$REPO"
```

## Review and deploy

Validate and review the exact standalone file that will be deployed:

```bash
docker compose -f "$BUILD_DIR/resolved.yml" config --quiet
docker compose -f "$BUILD_DIR/resolved.yml" config --services
docker compose -f "$BUILD_DIR/resolved.yml" config --images
```

Confirm the resolved services, fully filled environment, images, GPU placement,
model endpoints, public ingress, checked-in bind sources, and requested
capability checks. Run the mandatory check/create gate in
[`data-directory.md`](data-directory.md), then deploy that exact file:

```bash
docker compose -f "$BUILD_DIR/resolved.yml" up -d
```

`COMPOSE_PROFILES` has already filtered the source graph during resolution.
Normalization removes the remaining service profile gates, so no Foundation
env file or profile flag is needed at deployment time.

## Readiness

First require a non-empty expected service list and acceptable container states:

```bash
resolved_args=(-f "$BUILD_DIR/resolved.yml")

expected="$(docker compose "${resolved_args[@]}" config --services | wc -l)"
actual="$(docker compose "${resolved_args[@]}" ps --all -q | wc -l)"
[ "$expected" -gt 0 ] && [ "$actual" -ge "$expected" ]

if docker compose "${resolved_args[@]}" ps --all --format json |
   jq -e 'select((.State == "running" or
                  (.State == "exited" and .ExitCode == 0)) | not)' >/dev/null
then
  echo "A service is not ready" >&2
  exit 1
fi
```

Then run the Foundation's stock readiness checks plus checks for every added
capability owner. Allow cold NIM and RTVI model loads to finish. If a check
fails, report the failing service and its recent logs; do not declare a partial
deployment successful.

## Stop

Clean the complete `mdx` Compose project and its named volumes by default:

```bash
docker compose -p mdx -f "$BUILD_DIR/resolved.yml" down -v --remove-orphans
```

This removes data volumes and model caches. Use the cache-preserving path only
when the user explicitly requests it. Follow [`teardown.md`](teardown.md) for
leftover containers, stale volumes, and bind-mounted data cleanup.

## Sources

- `deploy/docker/README.md`
- `deploy/docker/compose.yml`
- `deploy/docker/containers.env`
- `deploy/docker/developer-profiles/dev-profile-*/.env`
- `deploy/docker/developer-profiles/dev-profile-*/overrides.env`
