# AGENTS.md

## Start Here

For any VSS agent task, read these first:

1. `skills/README.md` - catalog of available VSS skills.
2. `skills/vss-deploy-profile/SKILL.md` - profile routing for base, lvs, search, alerts, and warehouse deployments.
3. `skills/vss-deploy-profile/references/agent-facing-failure-modes.md` - first stop for deploy/runtime failure triage.
4. `services/agent/AGENTS.md` - only when changing or debugging the Python agent service.

## Routing Rule

If the request mentions deploy, profile, Docker Compose, broken stack, GPU sizing, NGC keys, remote endpoints, or VSS not starting, begin with:

`skills/vss-deploy-profile/SKILL.md`

That skill owns profile selection, backend placement, hardware checks, generated env files, compose dry-runs, deploy, teardown, and cross-profile troubleshooting.

## Common Paths

| Task | Start with |
|---|---|
| Deploy or redeploy VSS | `skills/vss-deploy-profile/SKILL.md` |
| Pick base/lvs/search/alerts/warehouse | `skills/vss-deploy-profile/SKILL.md#profile-routing` |
| Debug NIM, OOM, remote endpoint, or key failures | `skills/vss-deploy-profile/references/agent-facing-failure-modes.md` |
| Use search, summarization, alerts, VIOS, analytics, or reports | `skills/README.md` |
| Change agent API/tool code | `services/agent/AGENTS.md` |

## Guardrails

- Do not guess a deployment profile from memory. Use the profile routing table.
- Do not silently switch local/remote model placement. Use the profile reference sizing rules and report blockers clearly.
- Do not mutate source `.env` files in developer profiles. Work from `generated.env` as described in `vss-deploy-profile`.
- Before `docker compose up`, verify `resolved.yml` has no unexpanded `${...}` tokens.
