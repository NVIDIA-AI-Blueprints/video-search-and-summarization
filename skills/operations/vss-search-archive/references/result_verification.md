# Search-result verification

Read this only after the CLI returned a nonempty result set in which **every**
displayed hit has `critic_result` that is `null` or `result: "unverified"`,
those hits were displayed, and the user explicitly answered yes to the
verification question. Recheck the complete displayed set before handoff. If
any hit is `confirmed` or `rejected`, do not delegate any hit. Do not reconfirm
or rerun search.

Invoke the existing `vss-ask-video` skill once per displayed hit, with at most
three invocations in flight. Do not require or add a search-specific mode to
that skill. Instead, resolve the hit's exact bounded clip here and pass it
through ask-video's ordinary user-supplied `VIDEO_URL` interface.

## Resolve the bounded clip

For each hit, require the exact `sensor_id`, `start_time`, and `end_time`
returned by the CLI. Validate the sensor identifier before placing it in a URL,
resolve its main stream from VST, and request only the hit interval. Use
`--data-urlencode` so timestamp text is data rather than shell syntax:

```bash
: "${VST_URL:?resolved deployment origin}"
: "${HIT_SENSOR_ID:?exact CLI sensor_id}"
: "${HIT_START:?exact CLI start_time}"
: "${HIT_END:?exact CLI end_time}"
[[ "${HIT_SENSOR_ID}" =~ ^[A-Za-z0-9_-]+$ ]] || exit 1
VSS_PUBLIC_URL="${VST_URL%/}"
VSS=(uv run --project "${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}/services/agent" \
  --no-dev --extra cli vss)

# The recorded timeline. `vios timeline` resolves the sensor and its main
# stream itself, so there is no /sensor/<id>/streams call to make.
TIMELINE=$("${VSS[@]}" vios timeline --sensor "${HIT_SENSOR_ID}") || exit 1
TIMELINE_START=$(printf '%s' "${TIMELINE}" |
  jq -er '.segments[0].start_time') || exit 1
TIMELINE_END=$(printf '%s' "${TIMELINE}" |
  jq -er '.segments[0].end_time') || exit 1

# Rebase the synthetic hit interval onto the current file timeline, preserving
# its exact duration. This is the one part the CLI does not do for you.
mapfile -t MAPPED_BOUNDS < <(
  uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev python - \
    "${HIT_START}" "${HIT_END}" "${TIMELINE_START}" "${TIMELINE_END}" <<'PY'
import sys
from vss_core.vios import map_interval_to_timeline

for value in map_interval_to_timeline(*sys.argv[1:]):
    print(value)
PY
)
[ "${#MAPPED_BOUNDS[@]}" -eq 2 ] || exit 1

# One call: the window is validated against what is actually recorded, the URL
# is minted, normalised onto the configured origin, and its lazy render warmed.
# The scheme-doubling and bare-/storage repairs that used to live here are the
# CLI's job now.
CLIP=$("${VSS[@]}" vios clip --sensor "${HIT_SENSOR_ID}" \
  --start-time "${MAPPED_BOUNDS[0]}" --end-time "${MAPPED_BOUNDS[1]}") || exit 1
VIDEO_URL=$(printf '%s' "${CLIP}" |
  jq -er '.media_url | select(type == "string" and length > 0)') || exit 1
export VIDEO_URL VSS_PUBLIC_URL
```

The mapping preserves the exact search-hit duration, including intervals that
cross the synthetic midnight boundary, while rebasing it onto the current file
timeline. Failure to resolve the exact sensor, stream, interval, or reachable clip URL is
a technical failure. Never broaden the interval, choose another stream, or use
a cached/local copy.

## Invoke ordinary ask-video

Pass the complete original visual intent and the resolved `VIDEO_URL`. Ask the
VLM to analyze only that bounded clip, ignore scores, filenames, object IDs,
and other retrieval metadata, and return exactly one JSON object:

```json
{
  "result": "confirmed",
  "criteria_met": {
    "subject:person wearing a white jacket": true,
    "action:climbing a ladder": true
  },
  "evidence": "The bounded clip visibly shows the requested subject and action.",
  "media_evaluated": true
}
```

Require `result` to be `confirmed`, `rejected`, or `unverified`, every
`criteria_met` value to be boolean, nonempty `evidence`, and
`media_evaluated: true`. Malformed output is a technical failure; do not parse
JSON from hidden reasoning or surrounding prose. A valid semantic `unverified`
is a completed visual check and must not trigger fallback.

Replace only that hit's prior `null` or `unverified` state with the validated result.
Use representative-screenshot inspection only after a technical ask-video
failure. Reuse the hit's already origin-validated `screenshot_url`; never infer
missing criteria or broaden the interval. State that fallback evidence is one
representative image. If it is unavailable, retain the retrieval hit and report
verification as unavailable.

## Implementation-neutral reply

Keep progress and the final reply implementation-neutral: say that
verification is running or that a secondary method is being used, then report
the verdict and the visual evidence for it. Do not expose skill, model,
endpoint, or parser details, and name the source as the user did rather than by
its resolved UUID — those describe how the answer was produced, not what was
seen.

The user asked *what* was seen, not *how* the answer was produced, so the final
reply and any progress message **must not** expose any of these implementation
details, by name or by paraphrase:

- **Model names** — e.g. `nvidia/cosmos3-nano-reasoner` or any served-model
  string from `/v1/models`. Refer to it only as "the configured vision model."
- **Endpoint or API paths** — e.g. `/rtvi-vlm/v1`, `/v1/generate_captions`,
  `/v1/chat/completions`, `/api/v1/search`, or any VST/RTVI/Agent route. Do not
  name the service called.
- **Resolved sensor IDs, stream IDs, or UUIDs** — name the source exactly as
  the user did (`warehouse-ladder`), never as its resolved `sensor_id`,
  `default_<id>` index, or `sensorId`.
- **VST / RT-VLM / CLI internals** — timeline segments, `map_interval_to_timeline`
  rebasing, `vios timeline`/`vios clip`, the `VIDEO_URL` variable, or the
  `vss-ask-video` delegation. Do not describe the bounded-clip resolution or
  which command produced the clip.
- **Parser, schema, or skill machinery** — `SearchOutput`, `critic_result`
  field names, `criteria_met` keys, or that `vss-ask-video` was used.

State only the outcome: the source (as the user named it), the verdict
(`confirmed` / `rejected` / `unverified`), and a plain-language summary of the
visual evidence. A correct reply looks like:

> Verified `warehouse-ladder`: **confirmed**. The bounded clip shows a person
> wearing a white jacket climbing a ladder.

A wrong reply leaks the mechanism:

> I called `/rtvi-vlm/v1/chat/completions` with model
> `nvidia/cosmos3-nano-reasoner` on sensor `warehouse-ladder_3`'s VST timeline
> segment, and `vss-ask-video` returned `criteria_met: true`.

If verification is unavailable after a technical failure, say so plainly
("verification could not be completed; the retrieval hit is retained as
unverified") without naming the failed endpoint, model, or command.
