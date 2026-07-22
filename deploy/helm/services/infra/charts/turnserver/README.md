<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# turnserver (Helm chart TODO — helm-sync-bot placeholder)

This directory is a **placeholder** left by the helm-sync bot to flag
drift introduced by PR #1351 (`feat/base-profile-rtvlm-move`).

## What drifted

`deploy/docker/services/infra/compose.yml` adds two brand-new
services with no counterpart in this Helm tree:

- `turnserver-init` — an Alpine init container running
  `deploy/docker/services/infra/turnserver/scripts/init-password.sh`
  to generate a shared TURN password onto a named volume
  (`vss-turn-password`) at `/run/secrets/vss-turn/turn-password`.
- `turnserver` — `coturn/coturn:4.14.0`, mounting the same volume
  read-only, running
  `deploy/docker/services/infra/turnserver/scripts/entrypoint.sh`,
  and exposing UDP/TCP `${TURN_PORT:-3478}` plus the UDP/TCP
  relay port range `${TURN_MIN_RELAY_PORT:-49160}` –
  `${TURN_MAX_RELAY_PORT:-49200}`.

Downstream, `deploy/docker/services/vios/streamprocessing/docker-compose.yaml`
now mounts `apply_turn_config.sh`, the `vss-turn-password` volume,
and consumes the following env vars:

- `VST_STATIC_TURNURL_LIST`, `TURN_USERNAME`, `TURN_PASSWORD_FILE`,
  `TURN_PUBLIC_HOST`, `TURN_HOST_PORT`

with `depends_on: turnserver-init` so the password file exists
before VST reads it.

**None of this is currently reflected in Helm** — neither the
turnserver stack itself, nor the VIOS-side wiring.

## Why the bot did not auto-generate the chart

Porting the coturn stack to Kubernetes is not a mechanical
translation — it requires design decisions:

1. **UDP relay port range (49160–49200)**: `Service` type
   `LoadBalancer` supports UDP but many cloud providers (AKS, GKE,
   older EKS) don't handle large contiguous UDP port ranges well.
   `NodePort` is bounded to 30000–32767 by default and would need
   the range mapped or renumbered. `hostNetwork: true` avoids
   both problems but is a significant privilege escalation.
2. **Password sharing**: docker uses a named volume shared between
   `turnserver-init` and `turnserver`. In Kubernetes the idiomatic
   options are (a) an init-container in the same Pod writing to an
   `emptyDir`, or (b) a `Job` writing to a `Secret` (needs RBAC),
   or (c) an out-of-band pre-created `Secret`. Each has different
   ops implications.
3. **External IP discovery**: `TURN_EXTERNAL_IP` in docker falls
   back to `${HOST_IP}`. In Kubernetes this must come from the
   Service's external IP / a fixed value / DNS — a judgment call.
4. **Do we even want TURN in-cluster?** Many deployments front
   VSS behind an external TURN/STUN service; the docker addition
   may be a developer-profile convenience rather than a
   production-topology decision.
5. **VIOS wiring**: `apply_turn_config.sh` writes VST-config JSON
   at container start. Equivalent Helm behavior needs either an
   init-container running the same script against a
   `Secret` mount, or a `postStart` lifecycle hook, or (cleaner)
   folding the values into the existing rendered VST config via
   the chart's config template.

Because these are architectural decisions rather than mechanical
sync, the bot deliberately did not scaffold a `Chart.yaml`,
`values.yaml`, or templates here. The contributor (or a follow-up
PR) should make the call.

## What to do to unblock the helm-sync check

Either:

1. **Implement the Helm counterpart** — add a real `Chart.yaml` /
   `values.yaml` / `templates/*` under this directory (plus
   matching VIOS wiring under
   `deploy/helm/services/vios/charts/vios-streamprocessing/`), then
   delete this README. The next `pull-request/1351` mirror push
   will re-run helm-sync and, if parity holds, report
   `DONE: in sync`.

2. **Split the TURN work into a follow-up PR** — revert the
   turnserver additions from this PR's `deploy/docker/` diff so
   docker and helm remain in sync at the current scope. The
   turnserver stack lands as its own PR that updates both trees
   together.

3. **Explicitly scope TURN out of Helm** — if the intent is that
   TURN is docker-only (developer-profile / on-prem convenience,
   never in Kubernetes), document that decision in
   `deploy/helm/README.md` and add a `# TURN: docker-only, see
   deploy/docker/services/infra/turnserver/README.md` note here.
   The helm-sync bot will still flag drift until the exception is
   codified in its rules — coordinate with the harness owner.

## Files that would need to be created (for option 1)

Rough sketch, not prescriptive:

```
deploy/helm/services/infra/charts/turnserver/
├── Chart.yaml
├── values.yaml            # image, ports, relay range, resources, externalIP source
├── templates/
│   ├── _helpers.tpl
│   ├── deployment.yaml    # coturn container + init container running init-password.sh
│   ├── service.yaml       # UDP/TCP 3478 + relay range (design decision: LB vs NodePort vs hostNet)
│   ├── configmap.yaml     # entrypoint.sh + init-password.sh mounted from files/
│   └── secret.yaml        # (optional) TURN password if pre-provisioned
└── files/
    ├── entrypoint.sh
    └── init-password.sh
```

Plus, in `deploy/helm/services/vios/charts/vios-streamprocessing/`:

- `templates/deployment.yaml` + `statefulset.yaml`: add init
  container running `apply_turn_config.sh`, mount the TURN
  password Secret/volume, add `VST_STATIC_TURNURL_LIST`,
  `TURN_USERNAME`, `TURN_PASSWORD_FILE`, `TURN_PUBLIC_HOST`,
  `TURN_HOST_PORT` env from values.
- `values.yaml`: `turn.enabled`, `turn.publicHost`, `turn.hostPort`,
  `turn.username`, `turn.passwordSecret` (secretRef).

## Confidence

The bot's confidence in auto-generating this chart mechanically
is **1/5**. Do not merge a bot-generated version of this stack
without a careful human review of the networking, secret, and
privilege choices.
