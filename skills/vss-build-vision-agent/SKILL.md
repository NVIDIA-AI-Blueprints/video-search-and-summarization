---
name: vss-build-vision-agent
description: Compose VSS-based agent deployments from a natural-language capability description. Use this skill when the user asks for a new VSS profile or extension to an existing profile. Route, compose, configure, and deploy stock base, alerts, LVS, or search developer profiles and lean custom combinations expressed as delta overlays on one current developer profile.
---

# Build Vision Agent

## References

- [`references/composition.md`](references/composition.md) — delta-profile rules, base selection, artifact contract, and validation.
- [`references/deployment.md`](references/deployment.md) — shared stock and delta deployment lifecycle.
- [`references/profiles/`](references/profiles/) — current developer profile capabilities, exact service sets, owner mappings, knobs, readiness checks, and sources.
- [`references/services/`](references/services/) — capability-owner contracts for service keys, required peers, configurable environment knobs, and sources.

## Routing

| Request | Route |
|---|---|
| Deploy, start, run, verify, or stop a named `base`, `alerts`, `lvs`, or `search` profile | Stock mode for that profile. |
| Deploy capabilities that exactly match one current developer profile | Stock mode for the exact match. |
| Build, create, extend, customize, combine, add, or remove capabilities | Delta mode on the closest current developer profile. |
| Deploy capabilities with no exact match | Build the smallest delta, then deploy it. |
| Two bases have an equally small capability delta | Ask the user to choose between those bases. |
| Warehouse or another industry profile | Stop: this skill currently covers developer examples only. |

## Steps

1. Parse the request and any eval specification into required capabilities, excluded capabilities, configuration knobs, and observable success checks.
2. Read the matching file under `references/profiles/`. In delta mode, compare all four and select exactly one current Base Profile; ask only when two are equally plausible.
3. Read `references/composition.md` and only the capability-owner files under `references/services/` needed by the request.
4. If the request is an exact stock match, keep its authoritative service set unchanged and follow `references/deployment.md`.
5. Otherwise compute the smallest delta from the Base Profile’s exact `COMPOSE_PROFILES`: add or remove only canonical service profile keys and change only requested environment knobs.
6. Write `_builds/<name>/overrides.env`. Write `_builds/<name>/compose.override.yml` only for a genuinely new service or a changed service definition. Treat `<name>` only as a filesystem label; never add it to `COMPOSE_PROFILES`.
7. Validate the selected keys, env layering, resolved services, required peers, and requested success checks as specified in `references/composition.md`.
8. If deployment was requested, follow `references/deployment.md` with the same Base Profile and delta artifacts used during validation.
