# Architecture Diagram Template (Step 4)

Render the architecture proposal as an **ASCII flowchart** so the user can SEE the wiring, not just read it. The diagram is displayed inline in the terminal at Step 4 and persists losslessly in `<BUILD_DIR>/MANIFEST.md` — Step 6 must embed the same diagram there verbatim.

## Why ASCII (not Mermaid)

The skill's audience reads the diagram in two places: the Step 4 chat output (terminal) and `MANIFEST.md` (often via `cat` / `less` over SSH). A plain text-art diagram renders the same in both. We use Unicode box-drawing characters (`┌ ─ ┐ ╔ ═ ╗ │ ▼ →`) because every modern terminal and Markdown renderer on Linux/macOS/Windows handles them correctly under any UTF-8 locale, and they are dramatically more readable than 7-bit ASCII fallback (`+ - | = v >`). If the operator's environment is locked to `LANG=C` or a bare console with a non-Unicode framebuffer font, the skill can be re-invoked with `--ascii` to emit the 7-bit fallback.

## Higher-level by design

One box per **logical layer**, not per service. The Step 4 proposal text already enumerates each service; the diagram's job is to make the inter-layer flow obvious. Internal wiring between services inside the same layer is a flat list inside the box. Only edges that cross a layer boundary get an arrow.

## Required content

The diagram MUST include:

- **One box per logical layer** (ingestion / inference / storage / search / infra). Use the double-line frame (`╔═╗ ║ ╚═╝`) for layers and the single-line frame (`┌─┐ │ └─┘`) for external actors so they read as visually distinct at a glance.
- **Layer header line** carries the layer name (em-dash separated from a one-line role) and a right-aligned **network mode + GPU annotation** in square brackets. Always include both fields, even for non-GPU layers: `[network_mode: host · GPU: none]`, `[network_mode: bridge · GPU: 0]`, `[network_mode: bridge · GPU: none]`. Do not write a bare `[bridge]`, `[host]`, or a header with no GPU value.
- **Service list inside the box.** List every allow-listed service key, including one-shot init/wait services such as `kafka-topic-init-container` and `elasticsearch-init-container`; do not summarize them as `+ inits`. One service per line where possible, named by its service key and optionally followed by `container_name` in parentheses when they differ. Append `:PORT` for any host-exposed port. Use `·` (or `-` in `--ascii` mode) as the inline separator when two short services share a line.
- **External actors** (`operator`, `external RTSP source`, `agent UI`) as small single-line-frame boxes above the layered stack. Give each external actor an annotation line such as `[network_mode: external · GPU: none]` so the diagram remains auditable when copied into `MANIFEST.md`. If you group external actors into an `EXTERNAL ACTORS` logical layer instead of separate boxes, that layer header must also include both fields, for example `[network_mode: external/host · GPU: none]`. Edges enter the stack from the top. When the **NvStreamer validation harness** is included (sidecar `validation_harness:` key — see `references/validation-harness.md`), render it as a single-line-frame **validation-source** box labeled `NvStreamer (validation source)` with its ports (`:31000` HTTP, `:315xx` RTSP) in place of the real external-camera box; its edge enters the ingestion (VIOS) layer labeled with the registration call `POST /sensor/add (sensorUrl=rtsp://…315xx)`.
- **One labeled arrow per inter-layer connection** declared in the integrate refs' `§ Integration Interfaces`. Label format:
  - REST calls: `POST /vst/api/v1/sensor/add` (path only, drop the host)
  - Kafka: two-line label — `Kafka topic: mdx-vlm-captions` on line 1, `schema: nv.VisionLLM (proto)` on line 2
  - Shared bind mounts: `shared host vol: clip_storage`
  - RTSP / live media: `RTSP :30554 (live)` / `RTSP :30564 (vod)`
  - Direction: producer → consumer; the arrowhead lands ON the consumer box
- **Header comments** above the diagram, prefixed with `#`, carrying the deployment shape and the compose-profile flag:

  ```
  # deployment_shape: <chosen-variant-case-name>
  # flag: <bp_developer_…>
  ```

- **Code-fence the whole thing** with no language tag (` ``` ` … ` ``` `) so terminals and Markdown renderers preserve whitespace.

## Multi-diagram split rule

If the proposal involves more than **5 logical layers** (e.g. combined IN+AN+AT profiles that pull in agent / UI / metrics on top of the base ingestion/inference/storage/search), split into two stacked diagrams under sub-headings (`### Ingestion + storage` and `### Inference + indexing`) and reference both from the proposal text. Do not try to cram more than 5 vertically-stacked boxes into a single diagram.

## Canonical IN-1 example

Use as a template for the shape; swap layers and labels per the actual allow-list.

```
# deployment_shape: streaming-and-uploaded-dense-captioning
# flag: bp_developer_in_1

  ┌────────────────────────────────────────────┐      ┌────────────────────────────────────────────┐
  │ operator                                   │      │ NvStreamer (validation src)                │
  │ [network_mode: external · GPU: none]       │      │ [network_mode: host · GPU: none]           │
  └─────────────────┬──────────────────────────┘      │ nvstreamer-validation                      │
                    │                                 │ vss-vios-nvstreamer                        │
                    │                                 │ :31000 HTTP · :315xx RTSP                  │
                    │                                 └─────────────────┬──────────────────────────┘
                 │ PUT /storage/file?ts                  │ POST /sensor/add
                 │ POST /sensor/add                      │ (sensorUrl=rtsp://…315xx)
                 ▼                                       ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  VIOS [network_mode: host · GPU: none]                    ║
  ║  ingestion + storage                                      ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  vst-ingress :30888 · sensor-ms :30000                    ║
  ║  stream-processing :30001 :30554 :30564                   ║
  ║  sdr-controller :10000 :5003                               ║
  ║  sdrc-init-dirs · sdrc-render-config                       ║
  ║  sdrc-wdm-env-from-config                                  ║
  ║  sdrc-wait-for-redis · wait-for-docker-workloads           ║
  ║  centralizedb (postgres)                                  ║
  ╚════════════════════════════╤══════════════════════════════╝
                               │ RTSP :30554 (live)
                               │ shared host vol: clip_storage
                               ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  RT-VLM [network_mode: bridge · GPU: 0]                   ║
  ║  inference                                                ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  rtvi-vlm :8018   (cosmos-reason2, in-process)            ║
  ╚════════════════════════════╤══════════════════════════════╝
                               │ Kafka topic: mdx-vlm-captions
                               │ schema: nv.VisionLLM (proto)
                               ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  ELK + Kafka [network_mode: bridge · GPU: none]           ║
  ║  caption pipeline                                         ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  kafka :9092 · kafka-topic-init-container                 ║
  ║  elasticsearch :9200 · elasticsearch-init-container       ║
  ║  logstash → kibana :5601                                  ║
  ║  redis :6379 · broker-health-check                        ║
  ║  ES index: default_<collection_id>                        ║
  ╚═══════════════════════════════════════════════════════════╝
```

In this IN-1 example the streaming input is the **NvStreamer validation harness** (no real camera was supplied), so the top-right external actor is the NvStreamer validation source rather than a physical camera. It registers its auto-discovered sample with VIOS via `POST /sensor/add` (field `sensorUrl`); VIOS then re-publishes the stream on `rtsp://<host>:30554/live/<id>`, which RT-VLM consumes (the VIOS → RT-VLM edge already shown). If the operator HAD supplied a real RTSP camera, swap the validation-source box for an `external RTSP source` box and drop the `validation_harness:` key. The `operator` box's `POST /sensor/add` edge is the manual/VOD path and is independent of the harness.

## Canonical AT-1 example (realtime alerts, `alert_source=vlm-realtime`)

Use as the template when the allow-list includes Alert Bridge + VIOS + RT-VLM with `alert_source=vlm-realtime`. The **media path** is NvStreamer → VIOS → Alert Bridge; Alert Bridge drives RT-VLM internally (do not draw a direct NvStreamer → Alert Bridge or NvStreamer → RT-VLM edge).

```
# deployment_shape: realtime-vlm-alerts
# flag: bp_developer_at_1
# alert_source: vlm-realtime

  ┌──────────────────────────────┐
  │  NvStreamer (validation src) │
  │  vss-vios-nvstreamer          │
  │  :31000 HTTP · :315xx RTSP    │
  └──────────────┬───────────────┘
                 │ POST /sensor/add
                 │ (sensorUrl=rtsp://…315xx)
                 ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  VIOS — ingestion + storage         [network_mode: host]  ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  vst-ingress :30888 · sensor-ms · stream-processing       ║
  ║  sdr-controller + inits · live RTSP proxy (dynamic port)  ║
  ╚════════════════════════════╤══════════════════════════════╝
                               │ GET /sensor/{id}/streams
                               │ → live_stream_url (VIOS proxy)
                               ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  Alert MS — realtime rules          [network_mode: host]  ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  vss-alert-bridge :9080                                   ║
  ║  POST /api/v1/realtime (sensor_id + live_stream_url)      ║
  ╚════════════════════════════╤══════════════════════════════╝
                               │ /v1/streams/add + generate_captions
                               ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  RT-VLM — inference                  [bridge · GPU 0]     ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  rtvi-vlm :8018                                           ║
  ╚════════════════════════════╤══════════════════════════════╝
                               │ Kafka topic: mdx-vlm-incidents
                               ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  ELK + Kafka — alert pipeline             [bridge]        ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  kafka :9092 → logstash → elasticsearch :9200             ║
  ║  ES index: mdx-vlm-incidents-*                            ║
  ╚═══════════════════════════════════════════════════════════╝
```

For `cv-verification` (`alert_source=cv-verification`), insert RT-CV + Behavior Analytics between VIOS and Kafka (`mdx-raw` → `mdx-incidents`), then Alert Bridge consumes from Kafka — see `patch-alerts.md` and the AT-1 eval's cv-verification architecture requirement.

## What Step 6 must do

Step 6 MUST embed this same diagram verbatim (code fence and all) in `<BUILD_DIR>/MANIFEST.md` under `## Architecture` so the operator (and any future regeneration / re-deploy) has a permanent record. Do NOT regenerate or restyle the diagram in Step 6 — copy the Step 4 output exactly.
