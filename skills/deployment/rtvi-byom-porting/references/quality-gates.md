# BYOM Quality Gates

Treat the running service OpenAPI document as authoritative. If a field or
endpoint here disagrees with `GET $RTVI_BASE/openapi.json`, follow OpenAPI.

## Required Smoke Coverage

Test each modality the model claims to support:

- a text-only sanity prompt;
- an image prompt with explicit image routing;
- a video prompt with declared sampling parameters;
- a negative/control prompt for grounding.

Fail the port when output is empty, truncated, unrelated, repetitive, invalid
Unicode, token soup, or transport framing rather than model content.

## OpenAPI Request Contract

Resolve `$RTVI_BASE` (standalone Compose host port, often `http://127.0.0.1:8000`)
and `$MODEL` from `GET $RTVI_BASE/v1/models` before any generate call.

```bash
curl -fsS "$RTVI_BASE/v1/health/ready"
curl -fsS "$RTVI_BASE/v1/models"
```

`POST /v1/files` (`purpose=vision`):

- `media_type` must be `image` or `video`.
- Send exactly one of `file`, `filename`, or `url`. Sending `file` and
  `filename` together is rejected.

```bash
curl -fsS -F purpose=vision -F media_type=image \
  -F "file=@$IMAGE" "$RTVI_BASE/v1/files"
curl -fsS -F purpose=vision -F media_type=video \
  -F "file=@$VIDEO" "$RTVI_BASE/v1/files"
```

`POST /v1/generate_captions` requires `id`, `prompt`, and `model`. Set
`media_type` here (`image` or `video`). Do not omit `model`.

```bash
curl -fsS "$RTVI_BASE/v1/generate_captions" \
  -H 'Content-Type: application/json' \
  -d "{\"id\":\"$FILE_ID\",\"model\":\"$MODEL\",\"prompt\":\"Describe this.\",\"media_type\":\"image\",\"max_tokens\":64,\"stream\":false}"
```

`POST /v1/chat/completions` is OpenAI-compatible and rejects unknown top-level
fields. Do not send top-level `media_type`. Use it for text-only smoke, or for
chat bodies that OpenAPI actually documents.

```bash
curl -fsS "$RTVI_BASE/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the word ping.\"}],\"max_tokens\":32}"
```

`file://` URLs require `FILE_URL_ALLOWED_DIRS` to include the parent directory.
An empty allowlist returns HTTP 403; that is not a model failure.

## Media Files

Before upload, confirm the bytes are real media, not a Git LFS pointer. Pointer
files start with `version https://git-lfs.github.com/spec/v1` and will fail
vision ingest even though the path looks like `.mp4` or `.png`. Use a fetched
LFS object or another real clip (for example a short warehouse warmup video)
instead of committing or uploading the pointer.

## Video Sampling

Start with the service default sampler and record the response. If you set
`use_fps_for_chunking=true` with `num_frames_per_second_or_fixed_frames_chunk`,
record the resulting frame count. Reject the run when output is ungrounded or
when the frame count is implausibly large for the clip duration.

## Grounding

Compare media responses with visible evidence. The answer should name present
objects and actions, respect timing and sampling assumptions, and avoid confident
claims about unseen details or events.

## Evidence

Capture:

- redacted request payload or command;
- model source, immutable revision and backend;
- container tag and digest when available;
- eager-mode state and reason;
- relevant context, tensor-parallel, quantization, memory and media settings;
- raw response samples for every tested modality;
- latency, throughput, GPU utilization, memory and error counts.

Pass `byom_port_report.py` the raw observed strings, lists, or objects. Empty
evidence (`null`, `[]`, `{}`, or blank) must not be recorded as PASS.
