# AGENTS.md

## Scope

Applies to `vss-deploy-profile`, the profile deploy, verify, debug, and teardown
skill.

## First Reads

- Read `SKILL.md`.
- Open exactly one profile reference first: `base.md`, `alerts.md`,
  `lvs-profile.md`, `search.md`, `warehouse.md`, or `edge.md`.
- Read `prerequisites.md`, `credentials.md`, `ngc.md`, `data-directory.md`,
  and `readiness.md` only as the selected profile requires.

## Rules

- Route standalone microservice requests to the matching `vss-deploy-*` or
  `vss-setup-*` skill instead of this profile skill.
- For `alerts`, always distinguish `verification` mode from `real-time` mode.
- Copy profile `overrides.env` to `generated.env` before applying host-specific
  overrides. Do not mutate checked-in `.env` or `overrides.env`.
- Render `resolved.yml` with the same env layers used for deployment; reason
  from that rendered file.
- Report browser-reachable public URLs from the resolved deployment env, never
  raw container or private host ports.
- Teardown is destructive; require explicit user confirmation unless the eval
  prompt pre-authorizes it.

## Eval Behavior

- In CI, deploy autonomously when the trial prompt says prerequisites are
  pre-authorized.
- Verify the service needed by the next step, not only container uptime.
- Record profile, mode, hardware, env placement, readiness outcome, and endpoint
  mapping in the final answer.
