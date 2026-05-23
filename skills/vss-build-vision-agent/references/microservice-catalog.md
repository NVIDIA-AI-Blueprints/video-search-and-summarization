# VSS Microservice Catalog

Index of every VSS microservice that has reference files the `build-vision-agent` skill can resolve. Each entry maps a microservice name and capability tags to the skill folder where its `integrate-<microservice>.md` and `deploy-<microservice>.md` live.

**How the skill uses this file:**

1. Parse the user's capability description.
2. Tag-match against the `Capability tags` column to identify candidate services.
3. For each candidate, follow `Skill folder` → `integrate-<microservice>.md` to read the integration contract, then `deploy-<microservice>.md` for deployment requirements.
4. Cross-reference declared peer services in each candidate's `Required Peer Services` section to compose the full service list.

**How to register a new microservice:**

1. Create `skills/<your-skill-folder>/references/integrate-<your-microservice>.md` and `deploy-<your-microservice>.md` per the schemas in `integrate-microservice-schema.md` and `deploy-microservice-schema.md`.
2. Run `scripts/validate-references.py` to confirm the files pass schema validation.
3. Add a row to the table below with the microservice name, skill folder path, and capability tags.
4. Open a PR — the CI workflow re-runs validation on every reference file.

---

> **Note on reference file pair convention.** Each catalog row names the upstream skill folder AND a target `integrate-<microservice>.md` / `deploy-<microservice>.md` pair. The pair-file convention is introduced by `vss-build-vision-agent`. The current upstream state of each pair is shown inline in the table cells (current filename ⇢ target filename). Rows where the cell reads "— ⇢ `target.md` *(pending)*" indicate the target file does not yet exist upstream and must be authored.
>
> **Observed upstream conventions** (Phase 1a/1b/1c rollout work):
>
> - **Most common upstream deploy pattern:** `deploy-<short-service-name>-service.md` — used by `vss-deploy-dense-captioning/references/deploy-rt-vlm-service.md`, `vss-summarize-video/.../deploy-lvs-service.md`, `vss-setup-behavior-analytics/.../deploy-behavior-analytics-service.md`, `vss-setup-video-analytics-api/.../deploy-video-analytics-api-service.md`, `vss-generate-video-calibration/.../deploy-auto-calibration-service.md`. Rollout work: drop the `-service.md` suffix to reach the target name.
> - **Long-form upstream deploy pattern:** `deploy-vss-<skill-folder-name>.md` — used by `vss-deploy-detection-tracking-2d/.../deploy-vss-detection-tracking-2d.md` and `vss-deploy-video-embedding/.../deploy-vss-deploy-video-embedding.md`. Rollout work: rename to short-service-name form.
> - **`integrate-*.md` is almost entirely absent upstream.** The single exception is `vss-deploy-video-embedding/references/integrate-vss-deploy-video-embedding.md` (long-form). Every other catalog row's `integrate-<service>.md` is net-new authoring work.
> - **Four upstream skills have no `references/` folder at all** — `vss-ask-video`, `vss-query-analytics`, `vss-generate-video-report`, `vss-generate-video-report-rag`. Rollout work: create the folder plus both pair files.
> - **Three skills have a `references/` folder but no deploy file** — `vss-manage-alerts` (has `alert-notify.md`, `alert-subscriptions.md`), `vss-search-archive` (has `discovery_modes.md`, `troubleshooting.md`), `vss-manage-video-io-storage` (has only `api-reference.md`). The integrate content largely lives in those existing docs; rollout work is consolidation + renaming.
>
> The catalog declares the **target** naming. The skill's Step 2/Step 5 logic resolves the target filename in each row; Phase 1a/1b/1c work is what makes the targets exist.

## Catalog

### Phase 1a (IN-1 — VIOS + RT-VLM + ELK)

| Microservice | Skill folder | Integration ref (current ⇢ target) | Deployment ref (current ⇢ target) | Capability tags |
|---|---|---|---|---|
| VIOS (Video Storage) | `skills/vss-manage-video-io-storage/` | — ⇢ `integrate-vios.md` *(pending; upstream `references/` has only `api-reference.md`)* | — ⇢ `deploy-vios.md` *(pending; no deploy doc upstream)* | `video-storage`, `rtsp-ingestion`, `video-upload`, `clip-extraction`, `snapshot`, `sensor-management` |
| RT-VLM | `skills/vss-deploy-dense-captioning/` | — ⇢ `integrate-rt-vlm.md` *(pending)* | `deploy-rt-vlm-service.md` ⇢ `deploy-rt-vlm.md` *(rename — upstream uses `-service.md` suffix; also has `kafka-workflows.md` worth folding into the integrate doc)* | `dense-captioning`, `vlm`, `vision-language`, `streaming-inference`, `on-demand-inference`, `alert-detection` |
| ELK (Elasticsearch + Logstash + Kibana) | `skills/vss-build-vision-agent/` | `integrate-elk.md` ✓ | `deploy-elk.md` ✓ | `indexing`, `search`, `caption-storage`, `dashboard`, `kafka-ingestion`, `redis-ingestion` |

> **Note on ELK's location:** unlike RT-VLM, RT-CV, or VIOS — which are NVIDIA-built RTVI microservices owned by per-service teams — ELK is a third-party open-source stack (Elastic) used as VSS foundational infrastructure. Its reference files therefore live **co-located with the orchestrator skill** (`skills/vss-build-vision-agent/references/`) rather than in a sibling `skills/elk/` folder. This is the convention for foundational/infra components that the skill itself effectively owns; per-service NVIDIA microservices follow the canonical `skills/<service>/references/` pattern.

### Phase 1b — Planned

| Microservice | Skill folder | Integration ref (current ⇢ target) | Deployment ref (current ⇢ target) |
|---|---|---|---|
| RT-CV (DeepStream) | `skills/vss-deploy-detection-tracking-2d/` | — ⇢ `integrate-rt-cv.md` *(pending; upstream has many docs — `api-reference.md`, `pipeline-config.md`, `workflow-reference.md`, etc. — none in the integrate naming scheme)* | `deploy-vss-detection-tracking-2d.md` ⇢ `deploy-rt-cv.md` *(rename — upstream uses long `deploy-vss-<skill-folder>.md` form)* |

### Phase 1c — Planned

| Microservice | Skill folder | Integration ref (current ⇢ target) | Deployment ref (current ⇢ target) |
|---|---|---|---|
| RT-Embedding | `skills/vss-deploy-video-embedding/` | `integrate-vss-deploy-video-embedding.md` ⇢ `integrate-rt-embedding.md` *(the only upstream skill with an `integrate-*` file; rename to short form)* | `deploy-vss-deploy-video-embedding.md` ⇢ `deploy-rt-embedding.md` *(rename)* |
| Behavior Analytics | `skills/vss-setup-behavior-analytics/` | — ⇢ `integrate-behavior-analytics.md` *(pending; upstream has `configuration.md`, `dynamic-config.md`, `dynamic-calibration.md`)* | `deploy-behavior-analytics-service.md` ⇢ `deploy-behavior-analytics.md` *(rename — drop `-service.md` suffix)* |
| Alerts (Alert Verification) | `skills/vss-manage-alerts/` | — ⇢ `integrate-alerts.md` *(pending; upstream has `alert-notify.md`, `alert-subscriptions.md` — content lives there but not in the integrate naming)* | — ⇢ `deploy-alerts.md` *(pending; no deploy doc upstream)* |
| Long Video Summarization (LVS) | `skills/vss-summarize-video/` | — ⇢ `integrate-lvs.md` *(pending; upstream has `lvs-api.md`, `lvs-environment-variables.md`, `hitl-prompts.md`, `lvs-debugging.md`, `lvs.env.example`)* | `deploy-lvs-service.md` ⇢ `deploy-lvs.md` *(rename — drop `-service.md` suffix)* |
| VIOS MCP | `skills/vss-manage-video-io-storage/` | — ⇢ `integrate-vios-mcp.md` *(pending; upstream has only `api-reference.md` for the whole VIOS skill)* | — ⇢ `deploy-vios-mcp.md` *(pending)* |
| Video Analytics API | `skills/vss-setup-video-analytics-api/` | — ⇢ `integrate-video-analytics-api.md` *(pending; upstream has `configuration.md`)* | `deploy-video-analytics-api-service.md` ⇢ `deploy-video-analytics-api.md` *(rename)* |
| Video Analytics MCP / Query | `skills/vss-query-analytics/` | — ⇢ `integrate-video-analytics-mcp.md` *(pending; **no `references/` folder upstream**)* | — ⇢ `deploy-video-analytics-mcp.md` *(pending; no `references/` folder upstream)* |
| LLM NIM | `skills/vss-deploy-profile/` | — ⇢ `integrate-llm-nim.md` *(pending; NIM bring-up content is spread across `base.md`, `alerts.md`, `search.md`, `lvs.md`, `warehouse.md` per profile)* | — ⇢ `deploy-llm-nim.md` *(pending)* |
| VLM NIM | `skills/vss-deploy-profile/` | — ⇢ `integrate-vlm-nim.md` *(pending; same — content in per-profile docs)* | — ⇢ `deploy-vlm-nim.md` *(pending)* |
| Agent (Ask Video) | `skills/vss-ask-video/` | — ⇢ `integrate-ask-video.md` *(pending; **no `references/` folder upstream**)* | — ⇢ `deploy-ask-video.md` *(pending; no `references/` folder upstream)* |
| Archive Search | `skills/vss-search-archive/` | — ⇢ `integrate-search-archive.md` *(pending; upstream has `discovery_modes.md`, `troubleshooting.md`)* | — ⇢ `deploy-search-archive.md` *(pending)* |
| Video Calibration | `skills/vss-generate-video-calibration/` | — ⇢ `integrate-auto-calibration.md` *(pending; upstream has `rtsp.md`, `sample-dataset.md`, `videos.md`)* | `deploy-auto-calibration-service.md` ⇢ `deploy-auto-calibration.md` *(rename — drop `-service.md` suffix)* |
| Video Report | `skills/vss-generate-video-report/` | — ⇢ `integrate-video-report.md` *(pending; **no `references/` folder upstream**)* | — ⇢ `deploy-video-report.md` *(pending; no `references/` folder upstream)* |
| Video Report (RAG) | `skills/vss-generate-video-report-rag/` | — ⇢ `integrate-video-report-rag.md` *(pending; **no `references/` folder upstream**)* | — ⇢ `deploy-video-report-rag.md` *(pending; no `references/` folder upstream)* |

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
