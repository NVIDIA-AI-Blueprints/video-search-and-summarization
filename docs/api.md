# API Reference

Base URL (local): `http://localhost:8000`

Authentication: Cognito JWT bearer token (`Authorization: Bearer <id_token>`).
With `ENVIRONMENT=local` the API accepts unauthenticated requests as a
synthetic dev user.

## API service (`services/api`)

### `GET /health`

Liveness probe. Returns `{"status": "ok"}`.

### Videos

#### `POST /videos?filename=<name>&content_type=<type>&title=<optional>`

Creates video metadata and a presigned S3 upload URL.

```
201 Created
{
  "video": {
    "entity_type": "video",
    "video_id": "0fbb582f-...",
    "owner_id": "local-dev-user",
    "filename": "demo.mp4",
    "status": "UPLOADED",
    "created_at": 1756056000000
  },
  "upload_url": "https://s3.../videos/<owner>/<video_id>/demo.mp4?X-Amz-..."
}
```

The client uploads bytes directly to `upload_url` via HTTP PUT.

#### `GET /videos`

Lists the caller's videos, newest first.

```
200 OK  {"videos": [VideoMetadata, ...]}
```

#### `GET /videos/{video_id}`

Returns one `VideoMetadata`. `404` if not owned by caller.

Status lifecycle: `UPLOADED → TRANSCRIBING → VISION_PROCESSING → CHUNKING →
EMBEDDING → READY | FAILED`.

#### `DELETE /videos/{video_id}`

Removes metadata plus S3 media/artifact prefixes. `204` on success.

#### `GET /videos/{video_id}/stream-url`

Returns `{"url": "<presigned GET>"}` for playback in the player.

#### `POST /videos/{video_id}/chat`

Ask a question about one video. Requires status `READY` (`409` otherwise).

```json
// request
{"question": "What is discussed in the first minute?"}

// 200 OK
{
  "answer": "The speaker introduces ... ",
  "citations": [
    {"video_id": "...", "start_ms": 4200, "end_ms": 9800,
     "quote": "welcome to the tour", "timestamp": "00:00:04.200"}
  ]
}
```

Errors: `404` unknown video · `409` not READY · `502` agent failure.

### Cross-video agent

#### `POST /agent/chat`

Same as above but accepts `"video_ids": [...]` to search across videos;
validates ownership of every id.

#### `GET /agent/whoami`

Echoes resolved identity (useful for auth debugging).

## Agent service (`services/agent`, internal)

Called by the API, not directly by browsers.

### `GET /health`
### `POST /invocations`

```json
{"question": "...", "video_ids": ["..."]}
→ {"answer": "...", "citations": [...]}
```

Tools available to the model, each returning `{results, citations}` and
isolating its own errors:

| Tool | Purpose |
|---|---|
| `search_transcript` | keyword search over transcript segments |
| `search_visual_events` | search detected visual events |
| `retrieve_context` | embedding similarity over chunks |
| `get_timestamp_reference` | normalize/validate a citation range |
