# AGENTS.md - Your Workspace

> **This sandbox operates one VSS deployment; it never deploys or tears one
> down. There is no checkout, no GPU and no Docker here, and the policy allows
> the deployment, the model endpoint and the package hosts, nothing else.**

## VSS operations

- Every VSS skill reaches the deployment through the `vss_cli` tool,
  `/usr/local/bin/vss`, or the REST routes documented in that skill's `SKILL.md`
  — never ad-hoc HTTP in place of a CLI command that failed.
- The CLI knows the deployment from its own recording: `vss configure show`
  inspects it and `vss configure check` reports which command groups it can
  serve. Record `VSS_PUBLIC_URL` with `vss configure --base-url` only when
  nothing is recorded or the recording names another origin.
- Missing configuration is not authorization to deploy a stack. When a skill
  routes to `/vss-build-vision-ai`, which is not installed here, report what
  the request needs — the profile, the hardware, the origin — and stop.
- Report a policy denial or an unavailable service as itself. An empty result
  at exit 0 is an answer, not a failure.

## Skills

Skills provide your tools. Read the one you select from its own `SKILL.md`
before following it; never work from the name. `vss-hermes-sync` activates the
skills the deployment can serve and names the service each inactive one
wanted, so an absent skill is a missing service, not a missing file.

## Memory

You wake up fresh each session, and nothing outside `/sandbox` survives one.
Keep what is specific to this deployment — sensor names, prompts that worked,
corrections — in `TOOLS.md`.
