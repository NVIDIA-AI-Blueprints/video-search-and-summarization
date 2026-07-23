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
2. Read the matching file under `references/profiles/`. In delta mode, compare all four and select exactly one current Foundation; ask only when two are equally plausible.
3. Read `references/composition.md` and only the capability-owner files under `references/services/` needed by the request.
4. Determine the effective service set. For an exact stock match, keep its authoritative set unchanged. Otherwise compute the smallest delta from the Foundation’s exact `COMPOSE_PROFILES`: add or remove only canonical service profile keys and change only requested environment knobs.
5. Before writing delta artifacts or starting a stock or delta deployment, present a compact architecture diagram in the conversation. Show the Foundation, added and removed capability owners and service keys, principal data flows and topics, external endpoints, and GPU/model placement. Do not save the diagram as a build artifact.
6. For every stock or delta build, write `_builds/<name>/override.env`, `_builds/<name>/compose.yml`, and `_builds/<name>/resolved.yml`. Put the Foundation, the full effective `COMPOSE_PROFILES`, and every customized environment value in `override.env`. Make `compose.yml` include the root `deploy/docker/compose.yml` plus only changed or new service Compose files, if any. Treat `<name>` only as a filesystem label; never add it to `COMPOSE_PROFILES`.
7. Generate `resolved.yml` with `docker compose config` using the ordered env layers in `references/composition.md`, normalize dangling optional dependencies with `scripts/normalize_resolved_yml.py`, then validate the selected keys, fully resolved environment, services, images, required peers, and requested success checks against that exact file.
8. If deployment was requested, deploy the exact `_builds/<name>/resolved.yml` validated in the previous step and follow `references/deployment.md` for readiness and teardown.
