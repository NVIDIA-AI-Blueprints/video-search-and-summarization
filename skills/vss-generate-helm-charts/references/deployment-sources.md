# Compose deployment knowledge sources

Use this reference to include launcher-derived variants and deployment-skill knowledge in a Docker-to-Helm synchronization without treating operational documentation as configuration authority.

## Required launcher review

For every developer-profile consumer, read `deploy/docker/scripts/dev-profile.sh` in full, then trace the selected profile's branches. Add ledger rows for behavior that changes the Kubernetes output:

- accepted profile, mode, and hardware combinations;
- `.env` → `overrides.env` → generated `generated.env` precedence, plus `containers.env`;
- computed `COMPOSE_PROFILES` and mode-specific service membership;
- local, local-shared, and remote LLM/VLM selection, model names, device/GPU assignments, and hardware-specific variants;
- internal service URLs versus browser-facing protocol, host, and port values;
- generated config or environment values consumed by workloads;
- data directories, permissions, kernel/runtime prerequisites, login/pull/build flags, teardown, and other host actions.

Translate only portable workload intent. Examples: generated service membership becomes chart enablement; an internal Compose hostname becomes Kubernetes Service DNS; GPU placement becomes resources plus scheduling values; required persistent data becomes a PVC or an explicitly documented external-storage contract. Do not translate Docker login, host `chmod`, kernel mutation, `docker compose up/down`, image pulling, or cleanup literally into a chart.

Never run `dev-profile.sh`, even with `--dry-run`, during Helm authoring. Reading it is sufficient; its dry-run path still performs validation and may depend on host state or credentials.

## Deployment-skill review

Search the current skill tree rather than relying only on this list:

```bash
rg -l -i 'docker compose|compose\.ya?ml|docker-compose\.ya?ml|deploy/docker' \
  skills/*/SKILL.md
```

Open `skills/vss-deploy-profile/SKILL.md` whenever a developer or industry profile consumes the selected service. Then open each Compose-oriented deployment skill whose description, source path, Compose service key, or profile matches the synchronization scope. Current common routes include:

| Scope | Supporting skill |
|---|---|
| Developer VSS profiles and profile env resolution | `vss-deploy-profile` |
| RT-VLM / dense captioning | `vss-deploy-dense-captioning` |
| RTVI-CV 2D detection and tracking | `vss-deploy-detection-tracking-2d` |
| Standalone RTVI-CV-3D / MV3DT | `vss-deploy-detection-tracking-3d` |
| RT-Embed / video embedding | `vss-deploy-video-embedding` |
| Generated stock or delta Compose compositions | `vss-build-vision-agent` |

Also inspect any newly added matching `vss-deploy-*` skill and directly linked deployment/configuration references. A skill may identify startup ordering, required sidecars, generated config, readiness semantics, optional peers, external broker/model modes, storage ownership, or supported hardware variants that are easy to miss in a single Compose fragment.

## Evidence and conflict rules

Apply this authority order:

1. Explicit user requirement
2. Valid `helm-sync` directive
3. Checked-in `deploy/docker` Compose, env, launcher, config, and script behavior
4. Matching deployment-skill guidance
5. Existing Helm convention
6. Kubernetes-safe default

For each deployment-skill finding, locate its Docker evidence where possible and cite both in one ledger row. If the skill is stale, contradicts Docker source, describes a standalone flow outside the selected profile, or contains operator-only steps, retain the Docker behavior and record why the skill guidance was not translated. If a skill describes required portable behavior with no checked-in source evidence, mark it unresolved and request clarification rather than silently adding it to Helm.
