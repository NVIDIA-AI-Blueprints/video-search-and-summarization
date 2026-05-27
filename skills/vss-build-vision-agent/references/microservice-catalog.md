# VSS Microservice Catalog

Index of every VSS microservice that has reference files the `build-vision-agent` skill can resolve. Each entry maps a microservice name and capability tags to the skill folder where its `integrate-<microservice>.md` and `deploy-<microservice>.md` live.

**How the skill uses this file:**

1. Parse the user's capability description.
2. Tag-match against the `Capability tags` column to identify candidate services.
3. For each candidate, follow `Skill folder` → `integrate-<microservice>.md` to read the integration contract, then `deploy-<microservice>.md` for deployment requirements.
4. Cross-reference declared peer services in each candidate's `Required Peer Services` section to compose the full service list. Each `integrate-<microservice>.md § Required Peer Services` declares a structured `component_services:` block listing the upstream compose service-keys that microservice owns; the skill unions those blocks across confirmed candidates to produce the per-generation allow-list that drives Step 6.5.

**How to register a new microservice:**

1. Create `skills/<your-skill-folder>/references/integrate-<your-microservice>.md` and `deploy-<your-microservice>.md` per the schemas in `integrate-microservice-schema.md` and `deploy-microservice-schema.md`.
2. Include a `component_services:` block in `§ Required Peer Services` declaring the upstream compose service-keys this microservice brings into a deployment (and any variant choices the skill must surface to the user).
3. Run `scripts/validate-references.py` to confirm the files pass schema validation.
4. Add a row to the table below with the microservice name, skill folder path, and capability tags.
5. Open a PR — the CI workflow re-runs validation on every reference file.

---

> **Note on reference file pair convention.** Each catalog row names the upstream skill folder AND a target `integrate-<microservice>.md` / `deploy-<microservice>.md` pair. The pair-file convention is introduced by `vss-build-vision-agent`. The current upstream state of each pair is shown inline in the table cells (current filename ⇢ target filename). Rows where the cell reads "— ⇢ `target.md` *(pending)*" indicate the target file does not yet exist upstream and must be authored.
>
> **Observed upstream conventions** (Phase 1a/1b/1c rollout work):
>
> - **Naming convention (updated 2026-05-26):** the skill **honors upstream naming** rather than enforcing a single target shape. Where upstream already ships a `deploy-<short-name>-service.md` (5 skills) or a `deploy-vss-<skill-folder>.md` (2 skills), that name is **retained as canonical** — no rename. The `integrate-<service>.md` companion is authored alongside under the same shape if the upstream skill has any pair-file scheme, or as plain `integrate-<short-name>.md` otherwise. The earlier rollout plan to "drop the `-service.md` suffix" / "rename long-form deploys to short form" is **reversed**: Phase 1a/1b/1c work is now authoring the missing companion file(s), not renaming existing ones. Phase 1a confirmed this for VIOS (`integrate-vios-service.md` + `deploy-vios-service.md` both retained with `-service.md`) and RT-VLM (`deploy-rt-vlm-service.md` retained; companion authored as plain `integrate-rt-vlm.md`).
> - **`integrate-*.md` is almost entirely absent upstream.** The single exception is `vss-deploy-video-embedding/references/integrate-vss-deploy-video-embedding.md` (long-form). Every other catalog row's `integrate-<service>.md` is net-new authoring work.
> - **Four upstream skills have no `references/` folder at all** — `vss-ask-video`, `vss-query-analytics`, `vss-generate-video-report`, `vss-generate-video-report-rag`. Rollout work: create the folder plus both pair files.
> - **Three skills have a `references/` folder but no deploy file** — `vss-manage-alerts` (has `alert-notify.md`, `alert-subscriptions.md`), `vss-search-archive` (has `discovery_modes.md`, `troubleshooting.md`), `vss-manage-video-io-storage` (has the three retained refs `api-reference.md` + `integrate-vios-service.md` + `deploy-vios-service.md` after the Phase 1a authoring). The integrate content for the remaining two largely lives in existing docs; rollout work is consolidation + authoring under the upstream-respecting name.
>
> The catalog declares the **canonical** name per row (matching upstream where it exists). The skill's Step 2/Step 5 logic resolves that filename directly; Phase 1a/1b/1c work is what makes the missing files exist.

## Catalog

### Phase 1a (IN-1 — VIOS + RT-VLM + ELK)

| Microservice | Skill folder | Integration ref (current ⇢ target) | Deployment ref (current ⇢ target) | Capability tags |
|---|---|---|---|---|
| VIOS (Video Storage) | `skills/vss-manage-video-io-storage/` | `integrate-vios-service.md` ✓ | `deploy-vios-service.md` ✓ | `video-storage`, `rtsp-ingestion`, `video-upload`, `clip-extraction`, `snapshot`, `sensor-management` |
| RT-VLM | `skills/vss-deploy-dense-captioning/` | `integrate-rt-vlm.md` ✓ | `deploy-rt-vlm-service.md` ✓ *(canonical; sibling `kafka-workflows.md` is retained as a cited reference, not folded)* | `dense-captioning`, `vlm`, `vision-language`, `streaming-inference`, `on-demand-inference`, `alert-detection` |
| ELK (Elasticsearch + Logstash + Kibana) | `skills/vss-build-vision-agent/` | `integrate-elk.md` ✓ | `deploy-elk.md` ✓ | `indexing`, `search`, `caption-storage`, `dashboard`, `kafka-ingestion`, `redis-ingestion` |

> **Note on ELK's location:** unlike RT-VLM, RT-CV, or VIOS — which are NVIDIA-built RTVI microservices owned by per-service teams — ELK is a third-party open-source stack (Elastic) used as VSS foundational infrastructure. Its reference files therefore live **co-located with the orchestrator skill** (`skills/vss-build-vision-agent/references/`) rather than in a sibling `skills/elk/` folder. This is the convention for foundational/infra components that the skill itself effectively owns; per-service NVIDIA microservices follow the canonical `skills/<service>/references/` pattern.

### Phase 1b — Planned

| Microservice | Skill folder | Integration ref (current ⇢ target) | Deployment ref (current ⇢ target) |
|---|---|---|---|
| RT-CV (DeepStream) | `skills/vss-deploy-detection-tracking-2d/` | — ⇢ `integrate-vss-detection-tracking-2d.md` *(pending — author under the upstream long-form name to match the deploy companion; upstream has `api-reference.md`, `pipeline-config.md`, `workflow-reference.md`, etc. as additional refs)* | `deploy-vss-detection-tracking-2d.md` ✓ *(canonical; upstream long-form retained)* |

### Phase 1c — Planned

| Microservice | Skill folder | Integration ref (current ⇢ target) | Deployment ref (current ⇢ target) |
|---|---|---|---|
| RT-Embedding | `skills/vss-deploy-video-embedding/` | `integrate-vss-deploy-video-embedding.md` ✓ *(canonical; upstream long-form retained — the only upstream skill with an existing `integrate-*` companion)* | `deploy-vss-deploy-video-embedding.md` ✓ *(canonical; upstream long-form retained)* |
| Behavior Analytics | `skills/vss-setup-behavior-analytics/` | — ⇢ `integrate-behavior-analytics-service.md` *(pending — author under `-service.md` shape to match the deploy companion; upstream has `configuration.md`, `dynamic-config.md`, `dynamic-calibration.md` as additional refs)* | `deploy-behavior-analytics-service.md` ✓ *(canonical; `-service.md` retained)* |
| Alerts (Alert Verification) | `skills/vss-manage-alerts/` | — ⇢ `integrate-alerts.md` *(pending; upstream has `alert-notify.md`, `alert-subscriptions.md` — content lives there but no pair-file scheme exists yet, so author as plain `integrate-alerts.md`)* | — ⇢ `deploy-alerts.md` *(pending; no upstream deploy doc, so author as plain `deploy-alerts.md`)* |
| Long Video Summarization (LVS) | `skills/vss-summarize-video/` | — ⇢ `integrate-lvs-service.md` *(pending — author under `-service.md` shape to match the deploy companion; upstream has `lvs-api.md`, `lvs-environment-variables.md`, `hitl-prompts.md`, `lvs-debugging.md`, `lvs.env.example` as additional refs)* | `deploy-lvs-service.md` ✓ *(canonical; `-service.md` retained)* |
| VIOS MCP | `skills/vss-manage-video-io-storage/` | — ⇢ `integrate-vios-mcp-service.md` *(pending — author under `-service.md` shape to match the VIOS-skill convention)* | — ⇢ `deploy-vios-mcp-service.md` *(pending)* |
| Video Analytics API | `skills/vss-setup-video-analytics-api/` | — ⇢ `integrate-video-analytics-api-service.md` *(pending — author under `-service.md` shape to match the deploy companion; upstream has `configuration.md` as additional ref)* | `deploy-video-analytics-api-service.md` ✓ *(canonical; `-service.md` retained)* |
| Video Analytics MCP / Query | `skills/vss-query-analytics/` | — ⇢ `integrate-video-analytics-mcp.md` *(pending; **no `references/` folder upstream**, no pair-file scheme → author as plain `integrate-video-analytics-mcp.md`)* | — ⇢ `deploy-video-analytics-mcp.md` *(pending; no `references/` folder upstream)* |
| LLM NIM | `skills/vss-deploy-profile/` | — ⇢ `integrate-llm-nim.md` *(pending; NIM bring-up content is spread across `base.md`, `alerts.md`, `search.md`, `lvs.md`, `warehouse.md` per profile — no pair-file scheme → author as plain)* | — ⇢ `deploy-llm-nim.md` *(pending)* |
| VLM NIM | `skills/vss-deploy-profile/` | — ⇢ `integrate-vlm-nim.md` *(pending; same — content in per-profile docs)* | — ⇢ `deploy-vlm-nim.md` *(pending)* |
| Agent (Ask Video) | `skills/vss-ask-video/` | — ⇢ `integrate-ask-video.md` *(pending; **no `references/` folder upstream** → author as plain)* | — ⇢ `deploy-ask-video.md` *(pending; no `references/` folder upstream)* |
| Archive Search | `skills/vss-search-archive/` | — ⇢ `integrate-search-archive.md` *(pending; upstream has `discovery_modes.md`, `troubleshooting.md` but no pair-file scheme → author as plain)* | — ⇢ `deploy-search-archive.md` *(pending)* |
| Video Calibration | `skills/vss-generate-video-calibration/` | — ⇢ `integrate-auto-calibration-service.md` *(pending — author under `-service.md` shape to match the deploy companion; upstream has `rtsp.md`, `sample-dataset.md`, `videos.md` as additional refs)* | `deploy-auto-calibration-service.md` ✓ *(canonical; `-service.md` retained)* |
| Video Report | `skills/vss-generate-video-report/` | — ⇢ `integrate-video-report.md` *(pending; **no `references/` folder upstream** → author as plain)* | — ⇢ `deploy-video-report.md` *(pending; no `references/` folder upstream)* |
| Video Report (RAG) | `skills/vss-generate-video-report-rag/` | — ⇢ `integrate-video-report-rag.md` *(pending; **no `references/` folder upstream** → author as plain)* | — ⇢ `deploy-video-report-rag.md` *(pending; no `references/` folder upstream)* |

---

## Capability Tag Glossary

Tags used to match user prompts to microservices. Keep tags consistent across catalog entries — if a user prompt asks for "dense captioning", every microservice that satisfies that capability must carry the `dense-captioning` tag.

| Tag | Meaning | Services that carry it |
|---|---|---|
| `video-storage` | Persistent storage and retrieval of video clips | VIOS |
| `rtsp-ingestion` | Accepts RTSP streams as input | VIOS, RT-VLM |
| `video-upload` | Accepts video file uploads via REST | VIOS, RT-VLM |
| `clip-extraction` | Extracts time-bounded clips from recorded video | VIOS |
| `snapshot` | Returns single-frame snapshots from live or recorded streams | VIOS |
| `sensor-management` | Adds, lists, removes camera sensors / streams | VIOS |
| `dense-captioning` | Generates per-chunk natural-language descriptions of video | RT-VLM |
| `vlm` | Runs a vision-language model | RT-VLM |
| `vision-language` | Synonym for `vlm` | RT-VLM |
| `streaming-inference` | Processes live RTSP streams continuously | RT-VLM |
| `on-demand-inference` | Processes uploaded video files on request | RT-VLM |
| `alert-detection` | Emits structured alerts/incidents alongside captions | RT-VLM |
| `indexing` | Indexes structured records for query | ELK (Elasticsearch) |
| `search` | Full-text and structured search over indexed records | ELK (Elasticsearch) |
| `caption-storage` | Persistent storage of caption / metadata records | ELK (Elasticsearch) |
| `dashboard` | Visual dashboards over indexed data | ELK (Kibana) |
| `kafka-ingestion` | Consumes Kafka topics and writes to a sink | ELK (Logstash) |
| `redis-ingestion` | Consumes Redis streams and writes to a sink | ELK (Logstash) |

When you add a new tag, list it here with the services that carry it.
