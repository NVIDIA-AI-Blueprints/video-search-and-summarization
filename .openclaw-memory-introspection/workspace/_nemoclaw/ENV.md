# ENV.md — Sandbox Environment

The operator or evaluation harness supplies the deployment environment. Keep
those values, including explicit empty values. This file supplies defaults for
unset variables; `AGENTS.md`, `BOOTSTRAP.md`, and `TOOLS.md` reference it.

`/sandbox/.bashrc` is root-owned (mode `444`) in the nemoclaw sandbox,
so do not write a shell init file. The VSS CLI is already installed at
`/usr/local/bin/vss`; no PATH repair, checkout, or installation is needed.

## Exports

```bash
# Sandbox host alias, for Docker Compose deployments only — those
# publish their services on host ports. Skills curl ${HOST_IP} for those
# runtime calls (never localhost, never a literal IP) so the same skill
# works in-sandbox and on bare metal.
export HOST_IP="${HOST_IP-host.openshell.internal}"

# Origin of the VSS deployment you operate. Compose and Kubernetes follow
# the same contract: `vss configure --base-url $VSS_PUBLIC_URL` probes the
# path routes behind this one origin and records what answered, and every
# VSS skill then uses the recording. They differ only in the value: the
# haproxy origin http://host.openshell.internal:7777 for Compose, the
# cluster's Ingress origin for Kubernetes. `deploy_nemoclaw.ipynb` 3.2
# fills this line in at upload time. Preserve VSS_PUBLIC_URL, including an
# explicit empty value; only when unset use the harness's VSS_GATEWAY_ORIGIN.
export VSS_PUBLIC_URL="${VSS_PUBLIC_URL-${VSS_GATEWAY_ORIGIN-}}"

# Whether this harness may pause a running turn for structured human input.
# The launch configuration keeps this false: ask follow-up questions in the
# ordinary chat response and let the user's next message start the next turn.
export HITL_ENABLED="${HITL_ENABLED-false}"
```

## Empty VSS_PUBLIC_URL

First check `vss configure show`: an existing CLI configuration may already
name the deployment. If neither the environment nor the CLI configuration names
one, ask the user before a call that requires it. In a non-interactive evaluation,
report the missing configuration and stop. Do not discover or deploy a stack:

> I need the origin of the VSS deployment you want me to operate
> (for Compose on this host, `http://host.openshell.internal:7777`;
> for Kubernetes, the Ingress origin).

Then `export VSS_PUBLIC_URL=<answer>` for the session and write it to
`memory/YYYY-MM-DD.md`, so the next session can offer it back instead of
asking again. Keep the port in it: `vss configure` records the origin
verbatim, and the Elasticsearch client rejects a URL without one.

The selected origin must be allowed by the sandbox's policy. A policy denial is
a configuration problem to report; do not change the policy or route around it.
