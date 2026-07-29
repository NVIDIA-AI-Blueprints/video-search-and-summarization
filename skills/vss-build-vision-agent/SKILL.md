---
name: vss-build-vision-agent
description: Compose VSS-based agent deployments from a natural-language capability description. Use this skill when the user asks for a new VSS profile or extension to an existing profile. Route, compose, configure, and deploy stock base, alerts, LVS, or search developer profiles and lean custom combinations expressed as delta overlays using one current developer profile as the Foundation.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint orchestration deployment compose code-generation"
---

# Build Vision Agent

## References

- [`references/composition.md`](references/composition.md) — delta-profile rules, Foundation selection, build artifact contract, resolution, and validation.
- [`references/deployment.md`](references/deployment.md) — resolved Compose deployment lifecycle.
- [`references/deployment_resolution.md`](references/deployment_resolution.md) — deployment publication of `VSS_PUBLIC_URL`, public-route mappings, and the endpoint contract consumed by operate skills.
- [`references/teardown.md`](references/teardown.md) — default project-volume cleanup, explicit cache-preserving teardown, stale-volume removal, and bind-mounted data cleanup.
- [`references/prerequisites.md`](references/prerequisites.md), [`references/credentials.md`](references/credentials.md), and [`references/ngc.md`](references/ngc.md) — host, GPU runtime, firewall, credential, entitlement, and NGC checks.
- [`references/sizing.md`](references/sizing.md) — consolidated developer-profile sizing, model placement, shared-GPU budgets, stream capacity, utilization tuning, and validation.
- [`references/edge.md`](references/edge.md) — DGX Spark and Thor routing, unified-memory budgeting, cache management, and edge model recipes.
- [`references/env-overrides.md`](references/env-overrides.md), [`references/data-directory.md`](references/data-directory.md), [`references/readiness.md`](references/readiness.md), [`references/troubleshooting.md`](references/troubleshooting.md), and [`references/brev.md`](references/brev.md) — deployment checks, mandatory data-directory preparation, and environment-specific runtime guidance.
- [`references/profiles/`](references/profiles/) — current developer profile capabilities, exact service sets, owner mappings, knobs, readiness checks, and sources.
- [`references/services/`](references/services/) — capability-owner contracts for service keys, required peers, configurable environment knobs, and sources.

## Routing

| Request | Route |
|---|---|
| Deploy, start, run, verify, or stop a named `base`, `alerts`, `lvs`, or `search` profile | Stock mode for that profile. |
| Deploy capabilities that exactly match one current developer profile | Stock mode for the exact match. |
| Build, create, extend, customize, combine, add, or remove capabilities | Delta mode using the closest current developer profile as the Foundation. |
| Deploy capabilities with no exact match | Build the smallest delta, then deploy it. |
| Two Foundations have an equally small capability delta | Ask the user to choose between those Foundations. |
| Warehouse or another industry profile | Stop: this skill currently covers developer examples only. |

## Steps

1. Parse the request and any eval specification into required capabilities, excluded capabilities, configuration knobs, and observable success checks.
2. Read the matching file under `references/profiles/` and `references/sizing.md`. In delta mode, compare all four current profiles and select exactly one Foundation; ask only when two are equally plausible. Read `references/edge.md` for DGX Spark or Thor.
3. Before resolution or deployment, run the applicable checks from `references/prerequisites.md`, `references/credentials.md`, and `references/ngc.md`. Read the environment and Brev references when applicable.
4. Read `references/composition.md` and only the capability-owner files under `references/services/` needed by the request.
5. Determine the effective service set. For an exact stock match, keep its authoritative set unchanged. Otherwise compute the smallest delta from the Foundation’s exact `COMPOSE_PROFILES`: add or remove only canonical service profile keys and change only requested environment knobs.
6. Before writing delta artifacts or starting a stock or delta deployment, present a compact architecture diagram in the conversation. Show the Foundation, added and removed capability owners and service keys, principal data flows and topics, external endpoints, and GPU/model placement. Do not save the diagram as a build artifact.
7. For every stock or delta build, write `_builds/<name>/override.env`, `_builds/<name>/compose.yml`, and `_builds/<name>/resolved.yml`. Put the Foundation, the full effective `COMPOSE_PROFILES`, every customized environment value, and its transitively derived values in `override.env`. Make `compose.yml` include the root `deploy/docker/compose.yml` plus only changed or new service Compose files, if any. Treat `<name>` only as a filesystem label; never add it to `COMPOSE_PROFILES`.
8. Generate `resolved.yml` with `docker compose config` using the ordered env layers in `references/composition.md`, normalize dangling optional dependencies with `scripts/normalize_resolved_yml.py`, then run the mandatory check/create gate in `references/data-directory.md`. Reject stale placeholders and invalid checked-in bind sources with `scripts/validate_resolved_yml.py`, then validate the selected keys, services, images, required peers, GPU placement, utilization, and requested success checks against that exact file.
9. If deployment was requested, deploy the exact `_builds/<name>/resolved.yml` validated in the previous step, refresh its registry images even when their tags already exist locally, use `references/readiness.md` with the matching profile checks, and follow `references/deployment.md` for the resolved-Compose lifecycle. For stop or cleanup, follow `references/teardown.md`: remove project volumes by default and preserve model caches only when the user explicitly requests it.
