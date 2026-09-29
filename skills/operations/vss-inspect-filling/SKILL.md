---
name: vss-inspect-filling
description: Use for explicit measured bottle fill levels, Filling Analysis inspection records, bottle cycle-N/cyce-N identities, and questions scoped by the Filling Analysis UI. Do not use for searching a named recording for leaking, overflowing, or filling events; those requests use vss-search-archive. Never semantic-search a cycle identifier or select an RTSP stream as a recording.
metadata:
  vss-requires: filling
---

# Recorded and live filling inspection

Use this skill for the custom Filling Analysis tab and measured bottle questions.
It operates an already deployed extension through `vss filling`; it does not
deploy a stack, run a model locally, or create realtime alert incidents.
Follow the repository AGENTS.md for CLI setup, configuration and exit codes.

## Route archive searches away from this skill

A request to find/search clips of filling, leaks or overflow in a named video
is an archive search, not a request for calibrated measurements. Use
`vss-search-archive`; keep the video name as source scope and the visual event
as the query. Do not run any `filling` command for that request, even if this
extension is available or the words “filling”/“overflow” appear in the prompt.
The recipes below apply only to explicit measurement/inspection questions,
exact bottle cycle IDs, or context from the Filling Analysis UI. A named
recording is never a live session identifier. Explicit archive-search intent
takes precedence over ambient filling UI context.

## Exact live chat call and concise answer

Read the selection in the user's `[Context: ...]` before constructing the tool call.
For “this bottle” or “these two bottles”, the selected full track IDs MUST be
included as `--selected-cycle`. Session and stream IDs alone do not select a bottle.
Use the supplied `session_id` and `stream_id`; do not run status again when both exist.

For one selected bottle, make exactly this `vss_cli` call using the actual values:

```json
{"args":["filling","live","query","--session-id","<context session_id>","--stream-id","<context stream_id>","--question","<user question unchanged>","--selected-cycle","<selected_bottles[0].track_id>","--limit","10","--operator-view"]}
```

For two selected bottles, repeat the selection option with both actual full IDs:

```json
{"args":["filling","live","query","--session-id","<context session_id>","--stream-id","<context stream_id>","--question","<user question unchanged>","--selected-cycle","<selected_bottles[0].track_id>","--selected-cycle","<selected_bottles[1].track_id>","--limit","10","--operator-view"]}
```

These placeholders mean values from this request's context, never literal text.
Retain each track's embedded session and epoch. Do not combine `--selected-cycle`
with `--cycle-id` or `--epoch`. Require one selection for a single bottle or two
for comparison. With no selection, ask the user to select the bottle(s); never
substitute the moving current bottle or the first history records.

Explicitly typed cycle labels override ambient selections. Only in that case,
omit `--selected-cycle`, preserve the complete question and call:

```json
{"args":["filling","live","query","--session-id","<context session_id>","--stream-id","<context stream_id>","--question","<user question unchanged>","--limit","10","--operator-view"]}
```

Use `--operator-view` for ALL operator inspection questions, including clicked
Ask actions, comparisons and the latest overflow. For a clicked event without selected_bottles, add its
exact full returned `--cycle-id` and target `--epoch` if supplied. Never pass a
bare number as a cycle ID, omit the session, or use ambient `view_epoch` as a
query filter for an older short cycle label. With no context, discover the live
session once through `filling live status`, then use its returned IDs.

The compact response is already fully validated and scoped to the requested
cycles. For an inspection answer, return its `display_markdown` VERBATIM as the
final response, without a code fence, backticks, preface, follow-up analysis or
extra prose. Do not recalculate heights/durations, convert source time to UTC,
speculate about lighting, or add current-bottle diagnostics to an older cycle.
Do not omit or reconstruct any image URL in `display_markdown`.

The `ui_artifacts` field transports actual verified decoded-frame images to the
native VSS UI automatically. Do not print its raw envelopes. It contains only
the representative returned images, never a video claim. Empty video evidence
remains pending. Preserve not_found/ambiguous/in_progress/unsupported answers;
do not replace them with unrelated Search results or counts. Only an explicit
engineering/debugging request should omit `--operator-view` for full diagnostics.
## Route the measurement question first

A bottle `cycle-N` label (including a user's `cyce-N` typo), a complete live
track ID, the current filling bottle, or a Live-tab question is a **live filling
record lookup**. Read this skill before choosing a tool. A cycle identifier is
not a visual description, timestamp, event sequence or semantic Search query.
Never use archive Search, VLM, recorded `filling select/analyze/query`, or native
alert analytics to look up a live cycle.

Use the session/stream supplied by the UI or an explicitly selected event. A
full event track ID and its epoch take precedence over the ambient current-view
epoch. With no session context, `vss filling live status` discovers the active or
latest session; retain its returned session/stream and state which session you
looked in. Do not silently look across sessions or invent a session ID. A plain
short cycle label does not justify forcing the newest epoch onto an older event.

```text
vss filling live query --session-id <returned session UUID> --stream-id <returned stream UUID> --question "What happened with bottle cycle-12?" --operator-view
vss filling live query --session-id <returned session UUID> --stream-id <returned stream UUID> --cycle-id <exact returned track ID> --epoch <that event's epoch> --operator-view
```

Use the `vss_cli` argument array directly, for example
`["filling","live","query","--session-id","<context session>","--stream-id","<context stream>","--question","<user's actual cycle question>","--operator-view"]`.
`--cycle-id` optionally accepts a short `cycle-N` label or a complete returned
track ID; it is never the event's global `seq`. `--limit` is 1–50 (default 10).
An exact cycle query is a read; do not start/stop/reselect/reanalyze anything.

Follow the returned `query_status`:
- `ok`: explain the returned cycle, actual measured height/reference and reason.
- `in_progress`: describe the returned `current` observation as provisional;
  it has no final verdict yet.
- `not_found`: say the cycle was not found in this exact session; do not replace
  it with another bottle or generic totals.
- `ambiguous`: show the returned candidate session/epoch/track identities and
  ask which one; never pick an epoch silently.
- `unsupported`: explain the supported measurement question types.

Keep the exact source/session, event epoch, track ID and available evidence.
An older cycle retains its original measurement/clock from `measurement_history`;
resuming the session does not relabel it with a newer pipeline. Null means
unknown. Evidence without a verified returned URL remains pending. If a tool
returns a typed error, explain it; do not retry through recorded selection or
archive Search.

Only use the recorded workflow below when the user is asking about an explicitly
recorded source/profile. Resolve source type from actual listings; never pass an
RTSP/live source to recorded `filling select`.

## Recorded source and profile first

1. Run `vss filling sources`. Resolve the user's exact recording or supplied
   stream UUID from this listing. Never infer a UUID from a filename.
2. Run `vss filling source`. If its stream differs, use
   `vss filling select --stream-id <returned UUID>`.
3. Inspect `analysis_kind`, `profile_id`, `sha256`, `actual_start_time`
   and `expected_measurement`. A source without an approved profile needs calibration;
   do not borrow another recording's measurements.
4. Run `vss filling status --stream-id <UUID>`. If idle, invoke
   `vss filling analyze --stream-id <UUID>` once. This bounded command waits for
   server-side completion. On timeout, read status; do not restart the analysis.
5. Use the same UUID for every query, result and evidence request. A source
   conflict is a real failure to resolve, never a reason to drop the UUID.

## Measured questions

The `bottle-cycles` profile supports final underfills, visible exterior
overflows, the largest final-height shortfall and cycle counts. Judge low fill
after filling finishes. A low height during normal filling is not an underfill.
Overflow means observed escaping liquid; a high interior level alone is not proof.

```text
vss filling query --stream-id <UUID> --scene-id single-station --question "Which bottles finished below the reference level? Show their evidence."
vss filling query --stream-id <UUID> --scene-id single-station --question "Which bottles overflowed? Show their evidence."
vss filling query --stream-id <UUID> --scene-id single-station --question "Which bottle had the largest final-height shortfall?"
vss filling results --stream-id <UUID>
```

The original `within-shot-heights` profile supports within-shot visible-height
comparisons, sampled threshold observations and sampled height-change rates.
Read chapters to choose a shot; do not compare identities across different shots.
A user-specified 75% reference is `--reference-percent 75`, not 0.75.
The current cycle profile's reference is already applied when no override is
given. Read its actual returned reference; never reuse an older algorithm's
percentage. For RF measurements, final_level is a fraction of detected visible
bottle height; display percent as 100 * final_level. It is not a percentage of
the reference and must never be divided by reference_level. The reference uses
the same bottle-height denominator. A null level means unreadable/unknown,
never normal, zero or full. RF mask height uses the visible predicted bottle height and differs
from the legacy calibrated-strip height. Do not invent factory tolerances or
convert either measurement to volume.

## Evidence and presentation

`query` retrieves separate real clips for its measured observations by default.
Cite each `video_evidences[]` entry's exact returned `public_url` when available.
If that entry has no public URL, use its returned `local_clip_url` only as a
same-origin link in the VSS UI. Never substitute another bottle's clip. An
unavailable clip remains explicitly unavailable; measurements can still be shown.

In the VSS web chat, render each evidence URL as a standard clickable Markdown
link, for example `[Bottle 2 evidence](<exact returned public_url>)`, beside that
bottle's measured result. Insert the actual URL unchanged, without angle brackets.
Do not output `MEDIA:` wrappers, inline-code URLs, or a fenced block of links:
those are not clickable evidence links in this UI. Keep separate evidence links
for separate bottles; never construct a URL from a guessed source or timestamp.

The response preserves source SHA, stream UUID, UTC recording origin, requested
offsets and VIOS's actual clip start. Display source-second event times from the
measurement, allowing for the returned clip's preroll. Never equate clip-local
seconds with recording seconds.

For a specific measured interval:
`vss filling evidence --stream-id <UUID> --start <returned evidence_start> --end <returned evidence_end>`.

Summarize the actual counts and bottle IDs returned now. Never use demonstration
expectations or previous-run counts as results. Preserve uncertain or unreadable
cycles, sampling qualifications and unsupported-answer reasons. Describe the
recorded commands as recorded visible-height inspection, not calibrated volume,
continuous live measurement or native VSS incident detection.

In OpenClaw call the installed `vss_cli` tool with an argument array such as
`["filling","query","--stream-id","<UUID>","--question","Which bottles overflowed?"]`.
Return a concise answer plus each relevant returned evidence link.


## Learned GPU bottle and liquid masks

The optional GPU worker runs real RF-DETR bottle instance segmentation and a
separate source-adapted RF-DETR liquid model over recorded frames. Its cache is
bound to source, recording clock, checkpoints and pipeline code.

The current Sammy worker processes every source frame (24 fps); other sources
retain their reported frame clock. For the supported bottle-cycle profile,
current filling results/query derives visible heights and cycle measurements from those learned masks when
measurement.engine is rfdetr. Check measurement.algorithm/model_hashes against
source.expected_measurement; the CLI rejects stale caches. Preserve the
segmentation_pipeline_sha256 and source clock. The original multi-shot profile
may still report an explicitly legacy pixel engine; never describe that output
as RF-DETR measurement.

Use these commands through vss_cli, preserving the selected stream UUID:

- vss filling segmentation status --stream-id <UUID>
- vss filling segmentation run --stream-id <UUID> --timeout 1200
- vss filling segmentation get --stream-id <UUID>
- vss filling segmentation get --stream-id <UUID> --at <source seconds>

Check status first. Run starts or reuses the server job and waits within the
deadline; on timeout use status, never start a duplicate analysis. Do not use
--force unless reanalysis is requested. An unavailable worker is a real
capability limitation, not evidence that masks are empty.

Get omits all frame arrays by default and returns actual source/model provenance
and the sampled-frame count. For a specific moment, --at returns one actual
sample and reports both requested and sampled offsets plus its recorded UTC
time. Do not claim the nearest sampled frame occurred at the requested offset.
--include-samples returns the entire recording's polygons; avoid it in chat
unless full machine-readable data is explicitly requested.

Raw segmentation output identifies visible bottle/liquid pixels; use filling
results/query for derived heights, completed cycles and current provenance.
Never reuse earlier demonstration counts or percentages. Completion in the RF
cycle pipeline is inferred from sampled height rise, stability and departure;
it does not prove nozzle flow. Underfill uses the final readable height and
the returned reference/tolerance. Overflow uses the separately named calibrated
exterior-color signal; do not call it a learned liquid-mask spill detector.
State this hybrid method when reporting cycle outcomes.

Per-frame mask IDs are not persistent bottle tracks, and projected liquid area
or visible height is not volume. Preserve weak-supervision/domain limitations,
uncertainty and missing detections. No synthetic outline or colour-rule fallback
is evidence of a learned mask. Full-frame analysis can take several minutes;
the analyze/run default waits at most 1200 seconds and then directs you to status.


## Live RTSP filling sessions

Use the separate `filling live` commands when the user asks for continuous
measurement from a currently registered live stream. Recorded `results`, cached
samples and analyzed replay are never substitutes for a live session.

1. Run `vss filling live sources`; use the exact registered stream UUID returned
   for the requested source. Do not construct an RTSP URL or infer its UUID.
2. Discover active/latest state with `vss filling live status`, or inspect a
   supplied ID with `vss filling live status --session-id <returned session ID>`.
   Start a new session only within
   the user's requested scope: `vss filling live start --stream-id <UUID>`.
3. Retain the actual returned session ID and stream UUID together. Use that
   same session for status, events, queries and stop. A different session or
   stream is a provenance conflict, not a reason to omit the identifier.
4. Use `vss filling live events --session-id <ID> --after-seq 0 --limit 100`
   for a bounded event window; use its returned `next_seq` for the next window
   (maximum limit 200). Use
   `vss filling live query --session-id <ID> --question "Which finalized bottles
   were underfilled? Show any available evidence."` for measured answers.
   An optional `--stream-id <UUID>` additionally asserts the known source for
   status, events, query and stop. Do not reuse or invent a session UUID.
5. Stop only the requested session with `vss filling live stop --session-id <ID>`.
   A stopped session does not establish that the stream itself stopped.

In OpenClaw use its installed `vss_cli` tool with an argument array, for example
`["filling","live","query","--session-id","<ID>","--question","Which finalized bottles were underfilled?","--operator-view"]`.
Do not invent `list`, `run`, `--sensor`, or flags from the recorded command path.
A typed capability, source or session error stays an error; never fall back to
raw endpoints or cached recorded answers.

Keep provisional observations separate from finalized bottle cycles. A bottle
whose level is still rising has no final underfill decision. Do not count every
frame or provisional update as a separate completed bottle. Preserve missing
masks, unreadable heights, uncertainty, incomplete cycles and stream/reconnect
status. Starting a session proves only that it started, not that measurements
or anomalies were observed. Quote actual sampling rate, processing delay and
dropped-frame information when supplied; never promise source-frame throughput.

Live visible height comes from the reported RF bottle/liquid masks. Display
`100 * final_level` as percent of detected visible bottle height. The reference
has that same denominator; do not divide by the reference or call it percent
of the reference. A null level is unreadable/unknown, never zero, full or normal.
This is visible height, not volume. Overflow is a separately calibrated exterior
signal, not a learned spill mask or a conclusion from a high interior level.
Preserve actual engine, model, pipeline, source, session and clock provenance.

Live evidence is pending until the service returns a verified clip for that
session, stream and event. Use only that event's exact returned public URL, as a
clickable Markdown link. No URL means **evidence pending/unavailable**; do not
construct a VIOS URL, use another bottle's clip, or substitute a recorded clip.
Separate source timestamps, receive/processing times and clip-local time. These
extension events are not automatically native VSS alert incidents.


### Exact live snapshots and pending video clips

A `snapshot_evidences` entry marked `verified:true` with
`verification:exact-decoded-frame` is the actual decoded JPEG tied to its returned
session, stream, event, frame ID and source PTS. Cite its exact returned public
URL as a clickable **snapshot** link. This does not verify camera UTC or a VIOS
video clip. Keep snapshot availability separate from `video_evidences`: a real
snapshot may be available while video evidence is still pending. Never substitute
an arbitrary preview/current frame for an older event's snapshot or construct a
link from identifiers yourself. Older preserved cycles may have no stored image;
report their measurements honestly without inventing evidence.
