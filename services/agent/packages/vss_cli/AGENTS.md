# AGENTS.md — driving `vss` against a VSS deployment

Per-command detail for the `vss` CLI: the two command shapes, how `vios`
addresses media, and what the CLI does not cover. Setup and the cross-cutting
contract live in [AGENTS.md at the repository root](../../../../AGENTS.md).

## What `vss` is

The host-side entry point to a **deployed** VSS stack. It runs beside the
deployment, not inside it — no NAT, no torch, no GPU, no agent framework.

Every invocation is one process: JSON on stdout, a diagnostic on stderr, a typed
exit code. That is the whole contract. You do not need an SDK, a server, or a
session.

## Bootstrap, configure, exit codes

All of it — `uv` setup, why the project-local form and not a global `vss`,
`vss configure`, the exit-code table, and the rules for pipes and empty
results — is in [AGENTS.md at the repository root](../../../../AGENTS.md).
It is written once there because every skill needs it and none should
restate it.

This file covers what is specific to the CLI's own surface.

## The two shapes

**Job groups** — `search`, `summarize`, `vlm`. Work that runs a model and produces
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

## `vss search`, `vss summarize`, and `vss vlm`

```bash
vss search run "forklift near the loading dock" [--limit N]
vss search get --job-id <id>

vss summarize run --video-uri <uri> --prompt "..." --timeout <seconds>
vss summarize get --job-id <id>

vss vlm run --sensor NAME --start-time <ISO-UTC> --end-time <ISO-UTC> --prompt "..."
vss vlm get --job-id <id>
```

If a preflight fails, report its error and stop. Do not fall back to calling
Elasticsearch, the embedding service, or the agent API directly — a hand-built query
that returns *something* is worse than a clean failure, because nothing
downstream can tell it was improvised.

## `vss memory` — recall

```bash
vss memory query --query "..." [--mode keyword|semantic|hybrid]
vss memory introspect --query "..." --sensor NAME
vss memory embeddings backfill [--dry-run]
```

Text queries use the configured retrieval mode — `hybrid` whenever embeddings
are enabled, fusing the keyword and semantic rankings client-side. Asking for a
semantic mode with embeddings disabled warns on stderr and answers from keyword
retrieval rather than failing. Lookups by identity embed nothing and stay
deterministic: `get`, a `query` with no `--query`, and an introspection scoped
by `--job-id` or `--record-id`.

Canonical Elasticsearch memory remains authoritative. Vectors live in a
versioned companion index, and authoritative records carry only
`output.embedding` references to it. The CLI runs no model — by default it
reuses the OpenClaw Gateway's `openclaw/default` target, while an explicitly
configured OpenAI-compatible service can be used instead. Policy, prerequisites,
and the backfill contract are in [MEMORY.md](MEMORY.md).

## Rules

The seven that govern every group — configure once, branch on the exit code, an
empty result is an answer, never fall back to raw REST, read ids from listings,
do not wrap commands in your own retries, cite the handle you were given — are
in [AGENTS.md at the repository root](../../../../AGENTS.md). They are stated
once there because they are not specific to this package, and two copies drift.

## When the CLI does not cover it

The CLI covers the operations agents actually need. VIOS's full REST surface —
WebRTC session control, the proxy, recorder configuration, network scan, device
settings — is documented in
`skills/operations/vss-manage-video-io-storage/references/api-reference.md` and is reached
with `curl`. That is also the right tool when you are debugging VIOS itself: when
the question is *why* the service is failing, a wrapper over it tells you less
than the status code does.
