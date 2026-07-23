# Delta Profile Composition

- [Model](#model)
- [Select the base](#select-the-base)
- [Compute the delta](#compute-the-delta)
- [Artifact contract](#artifact-contract)
- [Validate](#validate)
- [Sources](#sources)

## Model

A Delta Profile is the smallest environment and optional Compose overlay applied
to exactly one current developer Base Profile. The Base Profile remains in place;
the delta does not copy its `.env`, `overrides.env`, Compose files, configs, or
skill bundle.

Current Base Profiles:

- `base`
- `alerts`
- `lvs`
- `search`

Use only developer profiles. Do not route warehouse or industry profiles through
this workflow.

## Select the base

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

Start with the Base Profile's effective `COMPOSE_PROFILES`.

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

Write only:

```text
_builds/<name>/
├── overrides.env
└── compose.override.yml   # optional
```

`<name>` is a filesystem label supplied by the user or a neutral description of
the requested build. It is never a Compose profile.

`overrides.env` contains:

1. `BASE_PROFILE=<base|alerts|lvs|search>` for human/tooling traceability.
2. The full effective `COMPOSE_PROFILES` after additions and removals.
3. Only environment values that differ from the Base Profile.

Do not write secrets unless the user explicitly requests a local deploy file;
prefer exported shell variables for credentials. `_builds/` is gitignored.

Create `compose.override.yml` only when:

- a requested service does not exist in the root Compose graph; or
- an existing service definition must change in a way Compose env interpolation
  cannot express.

An override may contain only the changed or new `services:` entries. Reuse the
canonical service key. Do not copy unchanged services, volumes, networks, or
profile files.

## Validate

From the repository root:

```bash
REPO="$(git rev-parse --show-toplevel)"
BASE_PROFILE="$(sed -n 's/^BASE_PROFILE=//p' "$REPO/_builds/<name>/overrides.env")"
BASE_DIR="$REPO/deploy/docker/developer-profiles/dev-profile-$BASE_PROFILE"
BUILD_DIR="$REPO/_builds/<name>"

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
```

Then verify:

- `BASE_PROFILE` names one current developer profile.
- Every `COMPOSE_PROFILES` token exists in the current Compose graph after env
  interpolation.
- The resolved service list is non-empty.
- Added capability owners and their required peers resolve.
- Removed services do not resolve.
- No unrequested service definition is present in `compose.override.yml`.
- The resolved configuration contains no unresolved `${...}` value required by
  a selected service.
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
