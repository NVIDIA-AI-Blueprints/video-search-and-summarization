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

**Read-only analytics** — `analytics`. Reads incidents, calibration-backed
sensor/place inventories, and metrics from the configured Video Analytics API.
It likewise mints no `job_id`, writes no record, and has no job verbs.

## `vss analytics` — incidents and metrics

```bash
vss analytics incidents [--source TEXT --source-type sensor|place] [--limit N]
vss analytics incident --incident-id ID
vss analytics sensors [--place TEXT]
vss analytics places
vss analytics fov-histogram --source TEXT --source-type sensor|place --start-time T --end-time T
vss analytics average-speed --source TEXT --source-type sensor|place --start-time T --end-time T
vss analytics analyze --source TEXT --source-type sensor|place --start-time T --end-time T --analysis-type TYPE
```

Every command requires the `video_analytics` service discovered by
`vss configure`. Empty arrays/counts are successful answers. A missing incident
exits 5. `vss analytics incidents` also returns `has_more`: when it is true,
`count` is at least that many matching incidents, not an exact total.

`vss analytics sensors` lists sensor IDs represented in analytics calibration
data. It is not VIOS registration: use `vss vios list` for the sensors currently
registered in the media plane.

`vss analytics places` returns hierarchy tokens such as
`building=Warehouse/room=Room-1`. Use those exact tokens with
`--source-type place` or `analytics sensors --place`.

## `vss filling` — recorded and live filling extension

The optional `filling` route is discovered at `/filling/api/health` by
`vss configure`. It serves the separately deployed custom Filling Analysis
extension, not a stock VSS capability. The server owns analysis and its cache;
these commands mint no additional CLI job.

```bash
vss filling sources
vss filling source
vss filling select --stream-id UUID
vss filling analyze --stream-id UUID
vss filling status --stream-id UUID
vss filling results --stream-id UUID
vss filling query --stream-id UUID --question "Which bottles overflowed?"
vss filling evidence --stream-id UUID --start SECONDS --end SECONDS
```

Read the UUID from the source listing. Selection verifies the recording against
an approved calibration. Every measurement command checks stream identity,
source SHA and recording clock; a changed source is an error. `analyze` waits
within `--timeout`; on timeout read status rather than starting it again.
`results` omits sample arrays unless `--include-samples` is requested.
`query` includes each measured observation's own video evidence by default.
Use `--reference-percent 75` for a 75% visible-height reference. Preserve
unsupported/uncertain results and each clip's availability; never reuse another
bottle's link. This is recorded visible-height analysis, not calibrated volume
or native realtime alerts.

The source advertises `expected_measurement`. Results, query answers and evidence
preserve the actual `measurement` engine, algorithm and model hashes. The CLI
rejects a completed cache with a different algorithm/checkpoint/pipeline, even
when the video UUID and bytes match. For the supported RF cycle profile, heights
derive from bottle/liquid masks; completion is inferred from sampled rise,
stability and departure. Its overflow signal is separately calibrated exterior
color detection. Other profiles can remain explicitly legacy pixel analysis.
Read each result's actual reference/tolerance; values from earlier algorithms
are not interchangeable.

`filling segmentation run/status/get` exposes the raw GPU masks.
`get --at SECONDS` returns one actual recorded sample; full polygon arrays
require `--include-samples`. Use `filling results/query` for derived cycle
metrics. Both analysis commands wait at most 1200 seconds by default; they can
time out while a valid full-frame job continues, so read status rather than
starting another job.

### Exact live filling cycles

Use `--operator-view` for operator chat. It validates the full upstream result,
then returns only the selected cycle facts, authoritative `display_markdown`
and native representative snapshot artifacts. Return `display_markdown`
verbatim without a code fence or added inference. Omit the flag only for explicit
engineering diagnostics; full results contain unrelated current state.

`cycle-N` is a live track label, not an archive search query or the global event
sequence. With no explicit UI/event session, discover active/latest using
`vss filling live status`; retain its returned session and stream IDs.
`vss filling live query --session-id UUID --stream-id UUID --question "What happened with bottle cycle-12?" --operator-view`
returns the exact lookup outcome. `--cycle-id` accepts the returned short label
or full track ID, `--epoch` scopes an explicit event epoch, and `--limit` bounds
results 1–50 (default 10). Do not apply the current epoch to a known older event.
Not-found/ambiguous/in-progress outcomes are valid typed answers; do not replace
them with generic counts or different records. No session is started or source
selected by a read. Never select a live RTSP source through recorded commands.

Live status/events/query preserve source/session and per-epoch measurement
history. Historical events must match the exact declared epoch model/pipeline/
calibration/clock identity. Null heights remain unknown and provisional bottles
have no final verdict. Visible height is not volume; overflow uses a separately
calibrated exterior signal. Unverified receiver UTC cannot mint evidence URLs.

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
