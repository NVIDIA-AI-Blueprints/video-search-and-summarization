# ENV.md — Sandbox Environment

Environment variables that must be set every session. Single source of
truth — `AGENTS.md`, `BOOTSTRAP.md`, and `TOOLS.md` all reference this
file rather than duplicating the values.

`/sandbox/.bashrc` is root-owned (mode `444`) in the nemoclaw sandbox,
so these cannot be persisted to a shell init file. `AGENTS.md` "Every
Session" Step 1 runs the block below at session start; if any of them is
ever empty (new shell, fresh connect, gateway restart), run it again.

## Exports

```bash
# Sandbox host alias, for Docker Compose deployments only — those
# publish their services on host ports. Skills curl ${HOST_IP} for those
# runtime calls (never localhost, never a literal IP) so the same skill
# works in-sandbox and on bare metal.
export HOST_IP=host.openshell.internal

# Kubernetes deployments publish nothing on host ports: every HTTP
# surface sits behind one path-based Ingress, so operate skills take the
# Ingress origin as their single public endpoint. It differs per
# deployment, so `deploy_nemoclaw.ipynb` fills this line in at upload time
# from its own VSS_PUBLIC_URL setting. Empty is a valid state -- it means
# Compose, or a Kubernetes deployment the notebook did not know about; see
# "Empty VSS_PUBLIC_URL" below before running anything that needs it.
export VSS_PUBLIC_URL=""

# Exact, setup-managed source checkout used by the operational VSS skills.
# `deploy_nemoclaw.ipynb` provisions and warms this checkout before chat is
# exposed; skills must not silently replace it with a mutable branch.
export VSS_REPO_ROOT="${HOME}/video-search-and-summarization"

# Skills that drive the VSS CLI (vss-search-archive, vss-summarize-video)
# invoke it as `uv run --project <checkout>/services/agent`, so uv has to
# resolve. `pip install --user` puts it here, and this directory is not on
# the default PATH. Harmless when uv is not installed; TOOLS.md "VSS CLI
# checkout" sets it up.
export PATH="/tmp/.local/bin:${HOME}/.local/bin:${PATH}"
```

## Empty VSS_PUBLIC_URL

A Kubernetes skill with no origin has nothing to talk to. Ask the user
before running it — do not guess the hostname, and do not go looking for
it in the cluster:

> I need the Ingress origin of the VSS deployment you want me to operate.

Then `export VSS_PUBLIC_URL=<answer>` for the session and write it to
`memory/YYYY-MM-DD.md`, so the next session can offer it back instead of
asking again. Keep the port in it: `vss configure` records the origin
verbatim, and the Elasticsearch client rejects a URL without one.

Expect the first call to fail anyway. The Ingress host must also be named
in the `vss-k8s-ingress` egress policy, and that entry is filled from the
same notebook setting that fills this file — so an empty export here means
the policy is almost certainly carrying its unreachable placeholder host
too. The policy lives on the host, out of your reach. On `CONNECT tunnel
failed, response 403`, report this and stop:

> Egress to that host is not allowed yet. Set `VSS_PUBLIC_URL` in section
> 1.3 of `deploy_nemoclaw.ipynb` and re-run 3.2 to re-apply the policy.
