# AGENTS.md — driving `vss` against a VSS deployment

**This file is the single source for how an agent uses the CLI.** A skill that
needs VSS should link here rather than restate the bootstrap, the exit codes, or
the resolution rules. Instructions that live in one skill (or worse, in an eval
adapter) are invisible to every other caller and drift the moment the CLI moves.

## What `vss` is

The host-side entry point to a **deployed** VSS stack. It runs beside the
deployment, not inside it — no NAT, no torch, no GPU, no agent framework.

Every invocation is one process: JSON on stdout, a diagnostic on stderr, a typed
exit code. That is the whole contract. You do not need an SDK, a server, or a
session.

## Bootstrap

Run it from the checkout. `--extra cli` is required — the base meta-package does
not install the distribution that provides `vss`.

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || {
  echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT" >&2; exit 1; }
# A function, not an alias: bash does not expand aliases in non-interactive
# shells, and neither survives into a separate command invocation.
vss() { uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss "$@"; }
```

Or install it once into a venv — see [README.md](README.md#install).

Do **not** run it through `docker exec`, `kubectl exec`, or a pod shell. It is a
client; it talks to the deployment over the ingress like any other client.

## Configure once, then never pass an endpoint

```bash
vss configure --base-url "${VSS_PUBLIC_URL}"
```

This probes the origin's routes and records them in `~/.vss/config.json`. **Every
other command reads that file.** No command takes a host, a port, or a service
URL, and you should never construct one:

- Do not use `kubectl port-forward`, a Service DNS name, a NodePort, or a guessed
  Helm release name.
- Do not read `HOST_IP`, `VST_INTERNAL_URL`, or a container's env to find a
  backend. The CLI reads no process env for endpoints, by design — same input,
  same behaviour, on any host.
- If a command exits 4 saying a service is missing, the fix is `vss configure`,
  not a flag.

## Exit codes — branch on these, not on stdout

| Code | Meaning | What to do |
|------|---------|-----------|
| 0 | Success | Parse stdout |
| 1 | Unexpected error | Report it; do not retry blindly |
| 2 | Invalid input | You asked for something impossible — fix the arguments |
| 3 | Backend unreachable | VSS or one of its services is down |
| 4 | Configuration | Run `vss configure --base-url <origin>` |
| 5 | Not found | The handle does not exist |
| 6 | Partial | Some results are missing; the payload says which |
| 7 | Timeout | Bounded wait expired; a `job_id` may be resumable |

**Pipe carefully.** `vss … | jq` hides the CLI's exit code behind `jq`'s, so a
failed command with empty stdout can read as an empty answer. Use `set -o
pipefail`, or capture first and check, before piping into anything.

**A non-zero exit always writes a diagnostic to stderr.** If you get a non-zero
exit and no message, that is a bug worth reporting — not a reason to improvise a
substitute query. Improvising around a silent failure is how agents end up
answering from data they invented.

**An empty result is not a failure.** `{"count": 0}` at exit 0 means the
deployment genuinely has nothing matching. A backend problem exits 3. Never
treat the two as the same.

## The two shapes

**Job groups** — `search`, `summarize`. Work that runs a model and produces
evidence. Every `run` mints a `job_id` and persists a record, so the result is
retrievable afterwards by that id:

```
vss <group> run     ...      execute end to end; the only execution verb
vss <group> status  --job-id
vss <group> get     --job-id
vss <group> list    [--since ...]
```

`run` is synchronous in every group. For a long job, background the process and
read the completion marker it prints as its final stdout line — do not poll.

**The media plane** — `vios`. Resolves handles and mints URLs. It runs no model
and produces no evidence, so it mints **no `job_id`**, writes no record, and has
no `run`/`status`/`get` verbs. Its `list` lists *sensors*, not jobs.

## `vss vios` — media

```bash
vss vios list     [--type video|stream] [--sensor NAME]
vss vios timeline --sensor NAME
vss vios clip     --sensor NAME [--start-time T --end-time T]   # -> media_url
vss vios snapshot --sensor NAME [--at T]                        # -> media_url
vss vios add      --type video|stream SOURCE [--name NAME]
vss vios delete   --type video|stream --sensor NAME
```

**Address media by sensor name.** The name is the stable handle — for an
uploaded file it is the filename stem (`warehouse_safety_0001`). Ids are
internal; the CLI resolves them.

**Never build a sensorId from a name.** VIOS assigns ids three different ways: an
auto-discovered file's id can carry a `_N` suffix its name does not have, a
PUT-uploaded file gets a fresh UUID, and a POST-uploaded one sometimes reports an
empty string. `/sensor/<name>/streams` answers `CameraNotFoundError` for two of
the three. If you need an id, read it from `vss vios list`.

**`--type` is provenance:** `video` is a file-backed sensor, `stream` is an RTSP
one. It is optional on `list` (omit it to see everything with its type resolved)
and required on `add`/`delete`, where the two genuinely differ.

**Do not hand-build a clip window.** `vss vios clip --sensor NAME` reads the
recorded range itself and returns the window it resolved alongside the
`media_url`. Reading a timeline and passing bounds back is where invented
timestamps come from — and a window spanning a recording gap is rejected.

**Before asking about a named sensor, check it exists.** Even when the user named
it explicitly, even when a previous turn used it:

```bash
SENSORS=$(vss vios list --type video) || exit 1     # check before piping
printf '%s' "${SENSORS}" | jq -r '.sensors[].name'
vss vios add --type video /path/to/clip.mp4         # if absent; the filename becomes the name
```

Uploaded filenames must have no whitespace — the filename *is* the sensor name.
The CLI rejects a bad one locally rather than spending the upload first.

## `vss search` and `vss summarize`

```bash
vss search run "forklift near the loading dock" [--limit N]
vss search get --job-id <id>

vss summarize run --video-uri <uri> --prompt "..." --timeout <seconds>
vss summarize get --job-id <id>
```

If a preflight fails, report its error and stop. Do not fall back to calling
Elasticsearch, the embedding NIM, or the agent API directly — a hand-built query
that returns *something* is worse than a clean failure, because nothing
downstream can tell it was improvised.

## Rules

1. **Configure once; never pass or construct an endpoint.**
2. **Branch on the exit code**, not on parsing stdout for words like "error".
3. **An empty result is an answer.** Do not retry it as though it were a failure.
4. **Never fall back to raw REST** when a command fails. Report the failure.
5. **Read ids from listings**; never assemble one from a name.
6. **Do not wrap commands in your own retry or timeout loops.** Bounded waits and
   retries are the CLI's job; a second layer just hides which one gave up.
7. **Cite the handle you were given** — `media_url`, `job_id` — not one you
   reconstructed.

## When the CLI does not cover it

The CLI covers the operations agents actually need. VIOS's full REST surface —
WebRTC session control, the proxy, recorder configuration, network scan, device
settings — is documented in
`skills/vss-manage-video-io-storage/references/api-reference.md` and is reached
with `curl`. That is also the right tool when you are debugging VIOS itself: when
the question is *why* the service is failing, a wrapper over it tells you less
than the status code does.
