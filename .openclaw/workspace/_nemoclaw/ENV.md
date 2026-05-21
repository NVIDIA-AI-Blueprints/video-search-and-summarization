# ENV.md — Sandbox Environment

Environment variables that must be set every session. Single source of
truth — `AGENTS.md`, `BOOTSTRAP.md`, and `TOOLS.md` all reference this
file rather than duplicating the values.

`/sandbox/.bashrc` is root-owned (mode `444`) in the nemoclaw sandbox,
so these cannot be persisted to a shell init file. `AGENTS.md` "Every
Session" Step 1 runs the block below at session start; if `$HOST_IP`
is ever empty (new shell, fresh connect, gateway restart), run it
again.

## Exports

```bash
# Sandbox host alias — the only hostname the nemoclaw egress policy
# whitelists for VSS backend ports. Skills curl ${HOST_IP} for every
# runtime call (never localhost, never a literal IP) so the same skill
# works in-sandbox and on bare metal.
export HOST_IP=host.openshell.internal
```
