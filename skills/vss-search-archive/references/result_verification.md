# Search-result verification

Read this only after the CLI returned a nonempty result set in which **every**
displayed hit is `unverified`, those hits were displayed, and the user
explicitly answered yes to the verification question. Recheck the complete
displayed set before handoff. If any hit is `confirmed` or `rejected`, do not
delegate any hit. Do not reconfirm or rerun search.

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
VST_API_BASE="${VSS_PUBLIC_URL}/vst/api/v1"
STREAMS=$(curl -fsS --connect-timeout 5 --max-time 15 \
  "${VST_API_BASE}/sensor/${HIT_SENSOR_ID}/streams") || exit 1
STREAM_ID=$(printf '%s' "${STREAMS}" | jq -er '
  if type == "array" then
    ((map(select(.isMain == true)) + .)[0].streamId)
  else
    .streamId
  end | select(type == "string" and length > 0)') || exit 1
[[ "${STREAM_ID}" =~ ^[A-Za-z0-9_-]+$ ]] || exit 1

TIMELINE=$(curl -fsS --connect-timeout 5 --max-time 15 \
  "${VST_API_BASE}/storage/${STREAM_ID}/timelines") || exit 1
TIMELINE_START=$(printf '%s' "${TIMELINE}" |
  jq -er 'sort_by(.startTime) | first | .startTime') || exit 1
TIMELINE_END=$(printf '%s' "${TIMELINE}" |
  jq -er 'sort_by(.startTime) | first | .endTime') || exit 1
mapfile -t MAPPED_BOUNDS < <(
  uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev python - \
    "${HIT_START}" "${HIT_END}" "${TIMELINE_START}" "${TIMELINE_END}" <<'PY'
import sys
from vss_core.vst import map_interval_to_timeline

for value in map_interval_to_timeline(*sys.argv[1:]):
    print(value)
PY
)
[ "${#MAPPED_BOUNDS[@]}" -eq 2 ] || exit 1
CLIP_START=${MAPPED_BOUNDS[0]}
CLIP_END=${MAPPED_BOUNDS[1]}

CLIP_RESPONSE=$(curl -fsS --connect-timeout 5 --max-time 120 --get \
  "${VST_API_BASE}/storage/file/${STREAM_ID}/url" \
  --data-urlencode "startTime=${CLIP_START}" \
  --data-urlencode "endTime=${CLIP_END}" \
  --data-urlencode 'container=mp4' \
  --data-urlencode 'disableAudio=true') || exit 1
VIDEO_URL=$(printf '%s' "${CLIP_RESPONSE}" |
  jq -er '.videoUrl | select(type == "string" and length > 0)') || exit 1

# VIOS 3.2.0 may prepend a scheme to an endpoint that already has one. Reduce
# the response to its path, then attach the already validated deployment origin.
while [[ "${VIDEO_URL}" =~ ^https?://https?:// ]]; do
  VIDEO_URL="${VIDEO_URL#*://}"
done
VIDEO_HOST_AND_PATH="${VIDEO_URL#*://}"
case "${VIDEO_HOST_AND_PATH}" in
  */*) VIDEO_PATH_QUERY="/${VIDEO_HOST_AND_PATH#*/}" ;;
  *) exit 1 ;;
esac
case "${VIDEO_PATH_QUERY}" in
  /vst/*) ;;
  /storage/*) VIDEO_PATH_QUERY="/vst${VIDEO_PATH_QUERY}" ;;
  *) exit 1 ;;
esac
VIDEO_URL="${VSS_PUBLIC_URL}${VIDEO_PATH_QUERY}"
curl -fS --connect-timeout 5 --max-time 120 \
  "${VIDEO_URL}" -o /dev/null || exit 1
export VIDEO_URL VSS_PUBLIC_URL
```

The mapping preserves the exact search-hit duration, including intervals that
cross the synthetic midnight boundary, while rebasing it onto the current file
timeline. Failure to resolve the exact sensor, stream, interval, or reachable clip URL is
a technical failure. Never broaden the interval, choose another stream, or use
a cached/local copy.

## Invoke ordinary ask-video

Pass exactly two inputs to `vss-ask-video`: the resolved `VIDEO_URL` and a
`QUESTION`. Do not pass the sensor or stream ID, timestamps, source name, model,
endpoint, retrieval metadata, or request/retry instructions as skill arguments.
The caller retains that operational context and validates the returned verdict.
Invoke the skill exactly once. Its confirmed-search handoff must finish all
read-only endpoint selection first, then make one inference request. Permit one
additional request only when a 2xx answer is malformed JSON, and only as a
same-endpoint, same-model, same-clip repair. An HTTP/auth/media/model failure is
technical and must not trigger another inference request; a valid semantic
verdict must never be retried.

Build `QUESTION` from the complete original visual intent. Require analysis of
only the bounded clip, ignore scores, filenames, object IDs, and other retrieval
metadata, and request exactly one JSON object with exactly these four keys:

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

Require no Markdown fence, preamble, summary, or fields beyond `result`,
`criteria_met`, `evidence`, and `media_evaluated`. A suitable handoff is:

```text
VIDEO_URL=<resolved bounded clip URL>
QUESTION=Evaluate only this bounded clip for: <complete original visual intent>.
Ignore retrieval scores, filenames, object IDs, and metadata as evidence. Return
only one JSON object with exactly these keys: result, criteria_met, evidence,
media_evaluated. result must be confirmed, rejected, or unverified;
criteria_met must be an object whose values are booleans; evidence must be a
nonempty string; media_evaluated must be true. Use confirmed only when every
criterion is true, rejected when any criterion is visibly false, and unverified
only when the visual evidence is genuinely inconclusive. Evidence must describe
only visible content and must not mention a VLM, model, endpoint, API, request,
retry, or backend. Add no prose or fields.
```

Require `result` to be `confirmed`, `rejected`, or `unverified`, every
`criteria_met` value to be boolean, nonempty `evidence`, and
`media_evaluated: true`. Malformed output is a technical failure; do not parse
JSON from hidden reasoning or surrounding prose. A valid semantic `unverified`
is a completed visual check and must not trigger fallback.

Replace only that hit's prior `unverified` state with the validated result.
Use representative-screenshot inspection only after a technical ask-video
failure. Reuse the hit's already origin-validated `screenshot_url`; never infer
missing criteria or broaden the interval. State that fallback evidence is one
representative image. If it is unavailable, retain the retrieval hit and report
verification as unavailable.

Keep progress implementation-neutral: say only that verification is running or
that a secondary method is being used. The final reply must contain only the
verdict, boolean criteria, and visual evidence useful to the user. Do not expose
skill names, model or vendor names, VLM terminology, endpoints or URLs, parser
details, source/sensor/stream IDs or UUIDs, timeline-rebasing mechanics, or
request/retry counts. Do not append an operational summary to the validated
result. In particular, the final reply must not contain the term `VLM`, even
when reporting a technical failure.
