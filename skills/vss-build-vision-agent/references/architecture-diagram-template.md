# Architecture Diagram Template (Step 4)

Render the architecture proposal as an **ASCII flowchart** so the user can SEE the wiring, not just read it. The diagram is displayed inline in the terminal at Step 4 and persists losslessly in `<BUILD_DIR>/MANIFEST.md` — Step 6 must embed the same diagram there verbatim.

## Why ASCII (not Mermaid)

The skill's audience reads the diagram in two places: the Step 4 chat output (terminal) and `MANIFEST.md` (often via `cat` / `less` over SSH). A plain text-art diagram renders the same in both. We use Unicode box-drawing characters (`┌ ─ ┐ ╔ ═ ╗ │ ▼ →`) because every modern terminal and Markdown renderer on Linux/macOS/Windows handles them correctly under any UTF-8 locale, and they are dramatically more readable than 7-bit ASCII fallback (`+ - | = v >`). If the operator's environment is locked to `LANG=C` or a bare console with a non-Unicode framebuffer font, the skill can be re-invoked with `--ascii` to emit the 7-bit fallback.

## Higher-level by design

One box per **logical layer**, not per service. The Step 4 proposal text already enumerates each service; the diagram's job is to make the inter-layer flow obvious. Internal wiring between services inside the same layer is a flat list inside the box. Only edges that cross a layer boundary get an arrow.

## Required content

The diagram MUST include:

- **One box per logical layer** (ingestion / inference / storage / search / infra). Use the double-line frame (`╔═╗ ║ ╚═╝`) for layers and the single-line frame (`┌─┐ │ └─┘`) for external actors so they read as visually distinct at a glance.
- **Layer header line** carries the layer name (em-dash separated from a one-line role) and a right-aligned **network mode + GPU annotation** in square brackets: `[network_mode: host]`, `[bridge · GPU 0]`, `[bridge]`.
- **Service list inside the box.** One service per line where possible, named by its `container_name` (or service-key when no container_name is set). Append `:PORT` for any host-exposed port. Use `·` (or `-` in `--ascii` mode) as the inline separator when two short services share a line.
- **External actors** (`operator`, `external RTSP source`, `agent UI`, `sample mediamtx`) as small single-line-frame boxes above the layered stack. Edges enter the stack from the top.
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

  ┌──────────────┐                ┌──────────────────────────┐
  │   operator   │                │   external RTSP source   │
  └──────┬───────┘                │   (camera │ mediamtx)    │
         │                        └────────────┬─────────────┘
         │ PUT /storage/file?ts                │ RTSP push
         │ POST /sensor/add                    │
         ▼                                     ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  VIOS — ingestion + storage         [network_mode: host]  ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  vst-ingress :30888 · sensor-ms :30000                    ║
  ║  stream-processing :30001 :30554 :30564                   ║
  ║  sdr-controller + 5 inits :10000 :5003                    ║
  ║  centralizedb (postgres)                                  ║
  ╚════════════════════════════╤══════════════════════════════╝
                               │ RTSP :30554 (live)
                               │ shared host vol: clip_storage
                               ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  RT-VLM — inference                  [bridge · GPU 0]     ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  rtvi-vlm :8018   (cosmos-reason2, in-process)            ║
  ╚════════════════════════════╤══════════════════════════════╝
                               │ Kafka topic: mdx-vlm-captions
                               │ schema: nv.VisionLLM (proto)
                               ▼
  ╔═══════════════════════════════════════════════════════════╗
  ║  ELK + Kafka — caption pipeline           [bridge]        ║
  ║  ───────────────────────────────────────────────────────  ║
  ║  kafka :9092 → logstash → elasticsearch :9200 →           ║
  ║                                              kibana :5601 ║
  ║  redis :6379 · phoenix · broker-health-check              ║
  ║  ES index: default_<collection_id>                        ║
  ╚═══════════════════════════════════════════════════════════╝
```

## What Step 6 must do

Step 6 MUST embed this same diagram verbatim (code fence and all) in `<BUILD_DIR>/MANIFEST.md` under `## Architecture` so the operator (and any future regeneration / re-deploy) has a permanent record. Do NOT regenerate or restyle the diagram in Step 6 — copy the Step 4 output exactly.
