# On-Demand Verification (Workflow F — CV mode)

Operational reference for **Workflow F**: one-shot VLM verification of a specific video/image URL through Alert Bridge. Execution requires a CV (verification) deployment; *explain-only* asks are answerable anywhere. Routing guard: continuous monitoring of a sensor/stream is **Workflow D**, never F.

## Endpoint & request contract

**`POST $AB/api/v1/verification/ondemand`** — there is **no** `/verification/verify` route; do not use `POST /generate` or any `/api/v1/realtime*` mutation for this.

```bash
# Use $AB from parent skill (K8s: ${VSS_PUBLIC_URL}/alert-bridge; Docker: :9080).
: "${AB:?Resolve AB from vss-manage-alerts Deployment prerequisite}"
curl -sf -X POST "$AB/api/v1/verification/ondemand" -H 'Content-Type: application/json' -d '{
  "category": "<alert_type>",
  "info": {
    "media_urls": ["https://…/clip.mp4"],
    "media_type": "video"
  }
}'
```

- `category` (required, case-insensitive — normalized to lowercase): must match an existing verifier-config `alert_type`. **Resolve it first** via `GET $AB/api/v1/verification/config` and pick the config matching the user's ask; never invent one.
- `info.media_urls` (required): non-empty list of URLs. `info.media_type` (required): `video` or `image`.
- Everything else is optional (`extra=allow` — the endpoint accepts a full `nv.Incident` payload, same shape DirectMedia consumes from Kafka). Useful optional fields: `id` (becomes the `correlationId`), `sensorId`, `timestamp`/`end`. Defaults when omitted: `id=ondemand-<uuid4>`, `sensorId="ondemand"`, timestamps = now.

## Responses

| Code | Body | Meaning |
|---|---|---|
| **202** | `{"status":"accepted","correlationId":"…","message":…,"timestamp":…}` | Accepted for **background** processing — not a verdict. Report the returned `correlationId` verbatim. |
| **400** | `{"status":"error","error":"unknown_category",…}` | `category` has no verifier config — list configs and pick a real one. |
| **400** | `{"status":"error","error":"invalid_request",…}` | Payload shape problem (missing `media_urls`, bad `media_type`, …). |

A 202 means **accepted, not verified** — never report a verdict, confirmation, or failure at submit time.

## Media constraints

- With the default `vlm_media_source_using_base64: false`, the URL is handed to the VLM endpoint, which **fetches it itself** — the URL must be reachable *from the VLM*, not just from the caller. A `localhost`/container-internal URL that the remote VLM cannot reach fails the fetch.
- `media_download.*` config (`max_media_count`, `timeout_seconds`, `max_size_mb`, `allow_private_urls`) applies on the base64/download path.
- An unreachable or invalid URL does **not** error the submit — the 202 stands and the **error path still publishes a result document** with a non-200 `verificationResponseCode` and `verdict: "verification-failed"`.

## Result lifecycle & validation

Background task → VLM call → result published via the same sink as the Kafka pipeline. **Which store it lands in depends on the submission's kind** (`is_alert()` checks `notification_type`):

| Submission | Kind | Store | How to query |
|---|---|---|---|
| Minimal `{category, info}` (the default) | **incident** | `mdx-vlm-incidents-*` | **`GET $AB/api/v1/realtime/incidents`** — a real REST endpoint (Workflow C's) |
| Payload carrying `notification_type: "alert"` | alert | `mdx-vlm-alerts-*` | Workflow B's interim ES probe |

```bash
# default case: poll the incident endpoint (allow ≥2 minutes for the VLM round-trip)
curl -sf "$AB/api/v1/realtime/incidents?limit=50" \
  | jq '.incidents[] | select(.id=="<correlationId>" or .sensorId=="ondemand")'
```

Validation checklist for a landed document (fields live in the `info` block):

- `verificationResponseCode` — `200` = VLM call succeeded; 4xx/5xx = error path (fetch/VLM failure). Accept camelCase or snake_case.
- `verdict` — populated (`confirmed`/`rejected`/…) only when the deploy runs verdict parsing (`use_verdict: true` or a pluggable parser). The default `use_verdict: false` is **freestyle**: the raw VLM text is stored (see `vlm_response`/`reasoning`) and `verdict` may be absent or empty — that is a valid success, not a failure.
- `reasoning` / `vlm_response` — the VLM's output; quote it rather than paraphrasing a verdict into existence.

No document after the poll window: report that the result has not landed yet (grounded), suggest re-polling; do not fabricate one. Verdict meanings: see `references/verification.md`.
