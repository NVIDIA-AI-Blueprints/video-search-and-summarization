# Delta Profile Composition

- [Model](#model)
- [Select the foundation](#select-the-foundation)
- [Compute the delta](#compute-the-delta)
- [Artifact contract](#artifact-contract)
- [Resolve](#resolve)
- [Validate](#validate)
- [Sources](#sources)

## Model

A Foundation is one reviewed, current developer profile selected as the closest
starting point for a request. A Delta Profile is the smallest environment and
optional Compose service-definition patch applied to exactly one Foundation.
The Foundation remains in place; the delta does not copy its `.env`,
`overrides.env`, Compose files, configs, or skill bundle.

Current Foundations:

- `base`
- `alerts`
- `lvs`
- `search`

Use only developer profiles. Do not route warehouse or industry profiles through
this workflow.

## Select the foundation

1. Translate the request and any eval specification into required and forbidden
   capabilities.
2. Compare those requirements with all files under `profiles/`.
3. Prefer an exact capability match and use stock mode.
4. Otherwise minimize service-set additions, removals, and definition changes,
   in that order.
5. Ask the user when two profiles have an equally small delta.

The selected profile's checked-in `overrides.env` is authoritative for its
Profile Service Set. The copied list in `profiles/` is a routing aid and must be
checked against source before writing a delta.

## Compute the delta

Start with the Foundation's effective `COMPOSE_PROFILES`.

- Add an existing service by adding its exact, self-named profile key.
- Remove a service by omitting its exact key.
- Keep dynamic NIM keys in their existing form:
  `llm_${LLM_MODE}_${LLM_NAME_SLUG}` and
  `vlm_${VLM_MODE}_${VLM_NAME_SLUG}`.
- Never invent an umbrella profile, a `bp_developer_*` name, or a `*-patched`
  name.
- For a genuinely new service, use the user- or source-provided service key as
  its self-profile. Do not derive a separate aggregate profile name.
- Read every selected owner's `Required peers`. Add a peer only when it is not
  already present and the requested capability needs it.
- Put user-configurable values in the env delta. Do not copy default values that
  are unchanged.

Service activation alone is never a Compose-definition change.

## Artifact contract

Always write:

```text
_builds/<name>/
├── override.env
├── compose.yml
├── resolved.yml
└── patches/               # optional; changed or new services only
```

`<name>` is a filesystem label supplied by the user or a neutral description of
the requested build. It is never a Compose profile.

`override.env` contains:

1. `FOUNDATION=<base|alerts|lvs|search>`.
2. The full effective `COMPOSE_PROFILES` after additions and removals.
3. Every customized environment value and every Foundation value transitively
   derived from it. Do not repeat unrelated Foundation defaults.

Compose expands each env file as it is read; values expanded in a Foundation
file are not recomputed when a later file changes one of their inputs.
Therefore, materialize the complete dependent-value closure in `override.env`.
For example:

- changing `VSS_APPS_DIR` also requires the effective `VST_CONFIG_PATH`,
  `SDR_CONTROLLER_CONFIG_PATH`, and any selected profile-specific config paths;
- changing `HOST_IP` also requires the effective `EXTERNAL_IP`,
  `VSS_PUBLIC_HOST`, public VIOS/Agent URLs, and selected UI/API endpoints.

Find the exact closure by following variable references in the selected
Foundation's `.env` and `overrides.env`; do not assume a later primitive
override will update an earlier derived value.

`compose.yml` is the build entrypoint. With no service-definition changes:

```yaml
include:
  - path:
      - ../../deploy/docker/compose.yml
```

When a service definition must change, append its patch after the root Compose
file:

```yaml
include:
  - path:
      - ../../deploy/docker/compose.yml
      - ./patches/<service>.yml
```

The ordered `path` list merges the patch into the included root model before
including it in the build. Use Docker Compose 2.20.3 or newer.

Create a file under `patches/` only when:

- a requested service does not exist in the root Compose graph; or
- an existing service definition must change in a way Compose env interpolation
  cannot express.

A patch may contain only the changed or new `services:` entries. Reuse the
canonical service key. Do not copy unchanged services, volumes, networks, or
profile files. Add multiple patch paths after the root file when multiple
service definitions change.

Generated runtime path redirection is a valid service-definition change. If a
selected upstream service uses relative writable bind mounts that would create
generated files or directories under `deploy/docker/` (for example SDRC
`./log`, `./.wdm-env`, or rendered `config.yml` outputs), create a minimal patch
that changes only those bind sources to `_builds/<name>/generated/...` or to a
`VSS_DATA_DIR` path. Copy only the needed template/config inputs into the build
generated directory when the service must render them. Never create `.wdm-env`,
rendered config files, logs, model engines, sample videos, or data directories
under `deploy/docker/`.

`resolved.yml` is the fully interpolated output of `docker compose config`.
Resolution filters the root graph through `COMPOSE_PROFILES`, so only the
effective service set and its dependencies are serialized. Normalization then
removes their now-redundant service profile gates. It is the exact, standalone
Compose model used directly for validation, deployment, readiness, and teardown.

All three primary files are required in stock and delta mode. `_builds/` is
gitignored because `override.env` and `resolved.yml` can contain credentials.
Keep them local and never commit them.

## Resolve

From the repository root:

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

The env layers are ordered from broad defaults to build-specific customization;
later values override earlier values. Regenerate `resolved.yml` whenever
`override.env`, `compose.yml`, a patch, or a Foundation source changes.
Normalization removes only optional dependency references to services omitted
by profile filtering, then removes service profile gates from the already
filtered model. It fails rather than remove a missing required dependency.
If validation reports real unresolved `${...}` Compose interpolation, do not
deploy the raw output. Add only the missing concrete value or derived value to
`override.env`, regenerate `resolved.yml` from the same ordered env layers, and
rerun normalization and validation. Escaped container-shell variables such as
`$${HOST_IP}`, `$${NUM_STREAMS}`, or `$${VAR:-default}` are valid in
`resolved.yml` and must not be counted as unresolved Compose interpolation.

## Validate

```bash
REPO="$(git rev-parse --show-toplevel)"
BUILD_DIR="$REPO/_builds/<name>"

uv run "$REPO/skills/vss-build-vision-agent/scripts/validate_resolved_yml.py" \
  "$BUILD_DIR/resolved.yml" --repo-root "$REPO"

docker compose -f "$BUILD_DIR/resolved.yml" config --quiet
docker compose -f "$BUILD_DIR/resolved.yml" config --services
docker compose -f "$BUILD_DIR/resolved.yml" config --images
```

Then verify:

- `FOUNDATION` names one current developer profile.
- Every `COMPOSE_PROFILES` token exists in the current Compose graph after env
  interpolation.
- The resolved service list is non-empty.
- Added capability owners and their required peers resolve.
- Removed services do not resolve.
- No unrequested service definition is present in a patch.
- Any patch contains only changed or new service entries; generated path
  redirection patches change only the affected bind sources or derived config
  paths.
- `resolved.yml` contains no real unresolved `${...}` Compose interpolation and
  every selected service's environment is filled in. Escaped `$${...}` variables
  are container-shell expressions, not Compose interpolation failures.
- `resolved.yml` contains no stock sentinels such as
  `/path/to/deploy/docker` or `<HOST_IP>`.
- Every checked-in bind source exists and a file target is not backed by a
  directory.
- No generated file or directory is created under `deploy/docker/`.
- The resolved services and knobs satisfy every observable check from the user
  request or eval specification.

## Sources

- `deploy/docker/compose.yml`
- `deploy/docker/containers.env`
- `deploy/docker/developer-profiles/dev-profile-*/.env`
- `deploy/docker/developer-profiles/dev-profile-*/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-*/compose.yml`
- `deploy/docker/services/**/compose.yml`
- `deploy/docker/services/**/compose.yaml`
- `deploy/docker/services/**/docker-compose.yaml`
- `deploy/docker/services/**/docker-compose.yml`
