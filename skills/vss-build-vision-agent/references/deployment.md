# Deployment

## Stock mode

Use the checked-in helper for an unchanged developer profile:

```bash
./deploy/docker/scripts/dev-profile.sh up \
  --profile <base|alerts|lvs|search> \
  --hardware-profile <hardware> \
  --dry-run

# After reviewing the dry run:
./deploy/docker/scripts/dev-profile.sh up \
  --profile <base|alerts|lvs|search> \
  --hardware-profile <hardware>
```

For alerts, also select the requested verification or real-time mode using the
helper's current `--mode` option. Inspect `dev-profile.sh --help` for model,
endpoint, device, and dry-run flags; do not guess flags from memory.

Before running:

- confirm Docker, the NVIDIA runtime, and the requested GPUs are available;
- export `NGC_CLI_API_KEY` for local NVIDIA images/models;
- export the required API key for explicitly requested remote endpoints;
- set or confirm host paths and browser-reachable ingress values;
- check the selected profile reference for stock-specific knobs and readiness.

## Delta mode

Use the same four env layers used for composition validation. Never copy or edit
the Base Profile files.

```bash
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"
BASE_PROFILE="$(sed -n 's/^BASE_PROFILE=//p' "$BUILD_DIR/overrides.env")"
BASE_DIR="$REPO/deploy/docker/developer-profiles/dev-profile-$BASE_PROFILE"

compose_args=(
  --env-file "$REPO/deploy/docker/containers.env"
  --env-file "$BASE_DIR/.env"
  --env-file "$BASE_DIR/overrides.env"
  --env-file "$BUILD_DIR/overrides.env"
  -f "$REPO/deploy/docker/compose.yml"
)
[ ! -f "$BUILD_DIR/compose.override.yml" ] ||
  compose_args+=(-f "$BUILD_DIR/compose.override.yml")

docker compose "${compose_args[@]}" config --quiet
docker compose "${compose_args[@]}" config --services
docker compose "${compose_args[@]}" config --images
docker compose "${compose_args[@]}" up -d
```

Do not deploy until the resolved services, images, GPU placement, model
endpoints, public ingress, and requested capability checks have been reviewed.
If the user explicitly requested autonomous execution, the request itself is
the approval to continue.

## Readiness

First require a non-empty expected service list and acceptable container states:

```bash
expected="$(docker compose "${compose_args[@]}" config --services | wc -l)"
actual="$(docker compose "${compose_args[@]}" ps --all -q | wc -l)"
[ "$expected" -gt 0 ] && [ "$actual" -ge "$expected" ]

if docker compose "${compose_args[@]}" ps --all --format json |
   jq -e 'select((.State == "running" or
                  (.State == "exited" and .ExitCode == 0)) | not)' >/dev/null
then
  echo "A service is not ready" >&2
  exit 1
fi
```

Then run the Base Profile's stock readiness checks plus checks for every added
capability owner. Allow cold NIM and RTVI model loads to finish. If a check
fails, report the failing service and its recent logs; do not declare a partial
deployment successful.

## Stop

Use the same `compose_args`:

```bash
docker compose "${compose_args[@]}" down
```

Do not remove volumes unless the user explicitly requests data deletion.

## Sources

- `deploy/docker/README.md`
- `deploy/docker/scripts/dev-profile.sh`
- `deploy/docker/compose.yml`
- `deploy/docker/containers.env`
- `deploy/docker/developer-profiles/dev-profile-*/.env`
- `deploy/docker/developer-profiles/dev-profile-*/overrides.env`
