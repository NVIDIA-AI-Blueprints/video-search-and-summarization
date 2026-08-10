<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# VSS Share

Publishes an agent-computed result set at a shareable, expiring URL.

## Why this exists

An agent driving VSS from a terminal-like surface can answer in prose, but some
results do not fit a terminal — search hits with thumbnails, for one. This
service takes the rows the agent already computed and puts them behind a link
that the existing VSS search grid can render.

**Results are transported by value, not by query.** Search is approximate
(`search_mode` is `embed` / `attribute` / `fusion` / `object`, with ANN
retrieval and fusion) and the archive keeps ingesting, so re-running a query in
the UI would produce a different answer than the one the agent reported. The
rows have to travel.

Real-time alerts do **not** need this — the alerts tab already polls Alert
Bridge on its own. Link to `#vss-mt-alerts` instead.

## Flow

```
 terminal                   vss-share                 browser
 ────────                   ─────────                 ───────
 skill calls search
 backend directly
    → {data:[...]}
 POST /api/view ──────────►  Redis + TTL
                ◄──────────  {id, url, expires_at}
 prints link
                                              opens /view/<id>
                            GET /api/view/<id> ◄──
                            rows, thumbs rewritten ──►  VideoSearchList
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/view` | Publish `{data:[...]}`; returns an unguessable id |
| `GET` | `/api/view/<id>` | Payload, with `screenshot_url` rewritten to proxy paths |
| `GET` | `/api/view/session/<sid>` | Current view for a slot, with an `ETag` for polling |
| `GET` | `/api/view/<id>/thumb/<n>` | Proxied VST thumbnail, scoped to the view |
| `GET` | `/api/view/<id>/preview.png` | Open Graph card for link unfurling |
| `GET` | `/health` | Liveness, including Redis reachability |

### Publishing

```bash
curl -s -X POST http://localhost:9095/api/view \
  -H 'content-type: application/json' \
  -d '{
        "query": "forklifts near the loading dock",
        "session": "operator-1",
        "data": [
          {"video_name": "clip1.mp4", "similarity": 0.94,
           "screenshot_url": "http://vst-ingress:30888/vst/api/v1/replay/stream/abc/picture",
           "description": "Forklift reversing", "start_time": "2026-01-15T09:00:00",
           "end_time": "2026-01-15T09:00:20", "sensor_id": "camera-3", "object_ids": []}
        ]
      }'
```

`session` is optional. When set, the slot is repointed at the new view, so a
page already open on that slot picks it up on its next poll. The superseded
view stays resolvable at its own id.

## Design notes

**Originals never reach the client.** `screenshot_url` values point at VST on a
private address; a recipient off the LAN cannot load them. They are stored
server-side and rewritten to `/api/view/<id>/thumb/<n>` on read.

**The proxy is index-addressed.** A caller names a position, never a URL, so it
cannot be steered at a host of their choosing. The stored target still has to
clear `SHARE_THUMB_ALLOWED_PREFIXES` — without that allowlist this would be an
open proxy.

**The id is the version.** Publishing mints a new id, so a session slot's
current id doubles as its `ETag` and an unchanged poll costs a 304.

**Missing and expired are the same 404.** Probing for which ids were once valid
should not be possible.

**The preview card is PIL, not a headless browser.** It is a title, a count and
four thumbnails at 1200×630; a faithful page render would roughly double the
image and add a browser to the attack surface.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `SHARE_REDIS_URL` | `redis://redis:6379/0` | Storage |
| `SHARE_TTL_SECONDS` | `604800` (7d) | Retention, not just cache |
| `SHARE_ID_BYTES` | `16` | 128 bits of entropy |
| `SHARE_MAX_RESULTS` | `500` | Oversized posts are rejected, never truncated |
| `SHARE_MAX_PAYLOAD_BYTES` | `4194304` | As above |
| `SHARE_THUMB_ALLOWED_PREFIXES` | VST ingress | Comma separated; empty disables proxying |
| `SHARE_THUMB_MAX_BYTES` | `8388608` | Per-thumbnail ceiling |
| `SHARE_PUBLIC_BASE_URL` | *(unset)* | Public https origin; blank returns a relative path |
| `SHARE_CORS_ALLOW_ORIGINS` | `*` | A view is already gated by its id |

## Security posture

A published view contains surveillance imagery. What protects it today is the
unguessable id plus the TTL — there is **no authentication**. Before pointing a
real public origin at this, decide whether that is sufficient for your
deployment.

The read-only UI (`vss-ui-view`) exists for the same reason: the full VSS UI
carries destructive controls (video delete, RTSP management) and must stay
internal. Enforcement is `middleware.ts` in the UI app, gated on the
server-side `VSS_VIEW_ONLY` variable.

## Known gaps

- **Clip playback is disabled in shared views.** `useVideoModal` resolves clip
  URLs against VST directly, which an off-LAN recipient cannot reach.
  Thumbnails render; cards are not clickable. Proxying clips through this
  service is the follow-up.
- **No authentication**, per above.
- Alerts views are not covered — link to the live alerts tab instead.

## Development

```bash
uv venv --python 3.13 .venv
uv pip install -r requirements.txt pytest pytest-asyncio fakeredis ruff
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check .
```

The suite fakes Redis and stubs every upstream fetch, so it needs no
containers.
