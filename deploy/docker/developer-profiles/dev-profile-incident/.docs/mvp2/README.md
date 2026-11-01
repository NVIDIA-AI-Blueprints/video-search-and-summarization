# MVP2: Natural-Language Evidence Search with Object Crops

**Profile:** `dev-profile-incident`
**Target:** Zero-GPU local workstation and `kwanz-ws` (2x NVIDIA RTX A6000 48 GB, of which VSS uses GPU 0 only: [GPU rule](../incident-profile-operations.md#5-vm-deployment-configuration-principles-generatedenvremote))
**Status:** search, chat and crops delivered; the retrieval eval is in progress ([status](../status.md#2-mvp2-natural-language-evidence-search))

---

## 1. Executive Summary

MVP2 adds **natural-language evidence search with object crops** to the incident search and reporting capstone. A reviewer uploads a video clip, a VLM extracts a structured incident report (incident-contract-v4), and the system enables searching across all stored incidents using natural language queries. Each search result returns the matching evidence item (entity/instrument/asset) with its **timestamp** and a **cropped object image** (bounding box from a separate open-vocabulary detector stage).

The MVP2 end-goal user-facing outcome is: **A reviewer types a natural-language query (e.g. "crowbar used in ATM burglary") and receives ranked evidence items, each showing the incident timestamp, description, and a tightly cropped JPEG of the object from the exact video frame.**

---

## 2. End-to-End Architecture & Data Flow

```
Surveillance Video
       |
       V (VLM P1 Extraction, incident-contract-v4)
       |
   Structured Report
       |  - incident type/title/description + severity, start_timestamp, end_timestamp
       |  - entities [{entity_id, type, description, timestamp_seconds, label}]
       |  - instruments [{instrument_id, name, threat_level, holder_entity_id, timestamp_seconds, label}]
       |  - assets [{asset_id, name, description, timestamp_seconds, label}]
       |  - timestamp_seconds + label per item (not bounding boxes)
       |
       V (Crop Pipeline Service)
       |
Keyframe @ timestamp_seconds  ──────────────────▶  Detector port (today: remote VLM DefaultDetector)
       |                                                   |
       |   Bounding box [ymin,xmin,ymax,xmax] normalised 0-1000
       V                                                   V
 Cropped JPEG (evidence image)                        High-precision box
       |                                                   |
       V                                                   V
  R2: evidence/<video key>/run-<N>/<itemId>.jpg  Supabase evidence_crops table
                                                 (embedding vector(1024), timestamp_seconds, bounding_box)
       |
       | (Text Embedding Model generates vector(1024) from item description)
       V
Search & Retrieval
       |
 POST /api/v1/incidents/search (vss-agent Search Route, search-contract-v1)
       |   - Input: natural-language query text + filters
       |   - Backend generates query text embedding vector(1024)
       V
 search_evidence_items RPC (Supabase Postgres, service_role only)
       |   - pgvector cosine distance over evidence_crops.embedding <=> query_embedding
       |   - Filters: item_type, incident_type, severity, min_similarity
       |   - Returns candidates with similarity_score, image URL, timestamp_seconds, bounding_box
       |
       V (Critic, off by default: INCIDENT_SEARCH_CRITIC)
       |
  VLM Critic evaluates each candidate hit against the user query using the cropped screenshot
       and item description. Outputs confirmed/rejected with objective rationale.
       |
       V (Result Persistence)
       |
  Both retrieval ranking and critic verdicts persisted into query_results table
       |
       V (Console Display)
       |
  Ranked results returned to incident-console-v2: each result shows
  - Incident type and description
  - Timestamp (hh:mm:ss)
  - Cropped object image with bounding box label
  - Critic verdict (confirmed/rejected), only when the critic is on
  - Rank and similarity score

```

### Key Data Flow Details

1. **VLM P1 Extraction** (incident-contract-v4): The VLM returns `timestamp_seconds` and a short searchable **label** for each entity/instrument/asset. **Bounding boxes are NOT requested** from the VLM (0% well-framed in pilot). Each entity also carries `first_seen_seconds` and `last_seen_seconds` (INT, `incident-contract-v4`) for the presence bar and the Edit-mode scrubber. The incident report schema uses `timestamp_seconds` (INT) and descriptive text per item, whilst `start_timestamp` and `end_timestamp` define incident-level temporal boundaries.

2. **Crop Pipeline**: When an incident report is generated, a lightweight CPU utility extracts the video frame at `timestamp_seconds`, then the detector port runs on the single frame with the item name as prompt and returns a bounding box (with 5% padding). Today the port is backed by the remote VLM (`DefaultDetector`, whole-frame fallback when no box is found); local open-vocabulary detectors (Grounding DINO, OWLv2) are evaluated offline in [`eval.md`](eval.md) §6 ([decisions](../decisions.md), 2026-10-18). The cropped JPEG is uploaded to Cloudflare R2 and the metadata (`timestamp_seconds`, `bounding_box`, `image` R2 key, `item_name`, `item_description`) is stored in the `evidence_crops` table. A text embedding model generates the pgvector embedding from the item description/text.

3. **evidence_crops table:** one table for entities, instruments and assets, keyed by `revision_id` and holding each video's searchable revision only (its official revision, else its newest). Columns, index, writers and RLS: [`data.md`](../data.md#evidence_crops).

4. **Search Route & Database RPC**:
   - Frontend calls REST route `POST /api/v1/incidents/search` (`search-contract-v1`) with natural-language query text and optional filters, including `filters.video_id` to search one incident.
   - The backend service generates `query_embedding` (through `semantic_search`, the one embed-then-retrieve path that `POST /api/v1/incidents/revisions/search` shares) and executes the `search_evidence_items` RPC (Supabase Postgres, restricted to `service_role`).
   - The RPC takes `p_query_embedding`, `top_k`, `min_similarity`, and applies filters (`item_type`, `incident_type`, `severity_level`, `min_similarity`, `p_video_id`). An empty query with a video id lists that video's crops in time order.
   - Queries pgvector HNSW over `evidence_crops.embedding <=> p_query_embedding`.
   - Returns only crops of each video's searchable revision, with the revision's facets as candidate metadata.
   - **Critic off by default** (changed 2026-10-28): the VLM critic runs only when the agent has `INCIDENT_SEARCH_CRITIC` set truthy; otherwise every hit is `unverified` (rationale `Critic not run`) and no VLM call or delay occurs. With the critic on, it evaluates each candidate hit against the original user query using the crop image (attached when its URL is http(s)) and a prompt that includes the item description (the same text that was embedded); it does not reorder hits. Both ranking and critic verdicts are persisted into `query_results` before returning results.
   - In `incident-console-v2`, search goes through the `EvidenceSearchRepository` port, whose agent adapter (`src/lib/adapters/agent/search.ts`) calls this route ([console layout](console-hexagonal-architecture.md#2-ports-and-adapters)).

5. **Query Results** (new table):
   - Records: `query_id`, `rank`, `similarity_score`, `critic_verdict` (confirmed/rejected/unverified), `critic_rationale`
   - Enables audit trails and critic feedback loops

6. **Chat Endpoint**: Follow-up questions with evidence citations. The chat route makes no model call and no embedding: it keyword-matches a report version's saved evidence, or asks its search client, which is still in-memory canned data ([`vm-api-contracts.md`](../vm-api-contracts.md)).

---

## 3. Shared Contracts & Workstream Consumption

| Contract / Schema | Description | Consumed By |
|---|---|---|
| **incident-contract-v4** (`contracts/incident_report.schema.json`) | Report schema: `incident{type, title, start_timestamp, end_timestamp, description, severity_level, severity_reason, confidence_score, location}`, `entities[]`, `instruments[]`, `assets[]`, `timeline[]`, `uncertainties[]`. Key fields: `timestamp_seconds` (INT, per item: entities, instruments, assets; entities also `first_seen_seconds` and `last_seen_seconds`, INT; `start_timestamp` and `end_timestamp` per incident), `entity type {human, animal, unknown}`, `instrument threat_level 1-5`, `asset name/description`. **Does NOT include bounding boxes.** | VM backend (vss-agent P1 extraction), eval PR 119, console gateway mode |
| **evidence_crops** (new Supabase table) | Unified table: `(revision_id, item_type, item_id, item_name, description, timestamp_seconds, bounding_box INTEGER[4], image R2 key, embedding vector(1024))`. HNSW index on embedding. RLS service_role-only. | search route; rows written by the console from `POST /api/v1/incidents/evidence-embeddings` for each video's searchable revision |
| **query_results** (new Supabase table) | Records: `id, query_id, revision_id, item_type, item_id, rank, similarity_score, critic_verdict, critic_rationale, retrieved_at`. | PR 119 (eval), search route, critic audit |
| **search-contract-v1** (REST endpoint) | `POST /api/v1/incidents/search`. Parameters: `query` (natural-language string), `top_k`, `min_similarity` (default 0.0), `hide_rejected` (default true; meaningful only with the critic on), `item_types[]`, `incident_types[]`, `min_severity`, `max_severity`, `filters.video_id`. Returns ranked results with incident metadata, item fields, similarity score, image URL, timestamp, and critic verdict. | PR 118 (frontend search), PR 119 (eval) |
| **search_evidence_items** (Database RPC) | Supabase Postgres function. Parameters: `p_query_embedding vector(1024)`, `top_k`, `min_similarity`, `item_types[]`, `incident_types[]`, `min_severity`, `max_severity`, `p_video_id` (optional; NULL embedding allowed only with it). Returns matching candidate table. RLS/Permissions: `service_role` only. | VM backend search route, PR 119 (retrieval benchmarks) |
| **chat-contract-v1** (REST endpoint) | `POST /api/v1/incidents/chat`. Parameters: `session_id` (optional), `messages[]` (`{role: "user" | "assistant" | "system", content}`), `context` (`{scope: "global" | "incident", incident_id, evidence_crop_ids}`). Returns assistant response with grounded evidence citations (`incident_id`, `item_id`, `item_type`, `timestamp_seconds`, `crop_image_url`). | PR 118 (frontend chat) |
| **incident-contract-v4 taxonomy** | Retained from contract-v2: `road accident`, `burglary`, `explosion`, `assault`, `animal attack`. `entity type {human, animal, unknown}`. `instrument threat_level 1-5`. Item IDs: sequential `E1..`, `I1..`, `A1..`. | All workstreams |

---

## 4. Delivered, dropped and open

**Delivered** (state: [`status.md`](../status.md#2-mvp2-natural-language-evidence-search)):

- `incident-contract-v4` extraction and the native `vss-agent` P1 route.
- Crop service (detector port, JPEG quality 85, readable R2 keys, `EvidenceStore` port) and `evidence_crops` / `query_results` with the `search_evidence_items` RPC.
- `POST /api/v1/incidents/search` (critic dormant by default; `INCIDENT_SEARCH_CRITIC` turns it on) and `POST /api/v1/incidents/chat` (`chat-contract-v1`).
- Both mocks (ports 7777 and 7778), `start.sh` bringing search up unconditionally, and the console's hexagonal middle layer ([layout](console-hexagonal-architecture.md)).
- `/library` Find incidents and Find evidence, and the report chat.

**Dropped:** multi-subagent routing and full RT-CV / RT-Embed indexing ([decision](../decisions.md#2026-10-24-drop-multi-subagent-routing-and-full-rt-cv--rt-embed-indexing-from-mvp2)); the timestamp backfill script and production evidence loader (the analyze route now writes crops and the console writes `evidence_crops`).

**Open** (all owned by the evaluation work in [`eval.md`](eval.md)):

- Detector framing: the remote-VLM `DefaultDetector` against offline candidates (§6.2). Stand-in: `[ymin, xmin, ymax, xmax]` normalised 0-1000.
- Critic model and prompt (§3.6). Stand-in: `{verdict: confirmed|rejected|unverified, confidence, rationale}`.
- Embedding model comparison (§6.3). Stand-in: `vector(1024)` on `evidence_crops.embedding`; the agent embeds, the console never does.
- Search RPC thresholds: the route sends `top_k` 10 and `min_similarity` 0.0 (the RPC's own 0.2 default is unused by the route).

## 5. Shared Conventions, Environments & Definitions

The PR task docs assume the following conventions and environment setup. This section makes them explicit.

### Environment

- **Zero-GPU local development:** `mock-backend/base_profile_mock/` (port 7777) provides a full mock of vss-agent API + VST + inference behind one FastAPI process. `mock-backend/search_profile_mock/` (port 7778) adds search-profile mock for MVP2 prep. `vlm-gateway/` (port 8600) holds the Brev key server-side.
- **GPU-backed VM:** `kwanz-ws`, GPU 0 only ([GPU rule](../incident-profile-operations.md#5-vm-deployment-configuration-principles-generatedenvremote)). `start.sh --mode vm` launches native `vss-agent` on port 8000. Docker appliances (Phoenix and the person-tracking stack; Elasticsearch also with `ENABLE_ANALYTICS=true`) run in compose. HAProxy, VIOS and Redis are removed.
- **Secrets:** Server-side only. `VLM_GATEWAY_API_KEY` owned by gateway, not browser (`NEXT_PUBLIC_` never used for credentials). `/api/health` returns booleans only.

### Conventions

- **Item ID formats:** Sequential `E1..`, `I1..`, `A1..` (contract-v2). Enforced by `id-normalization-v1` repair.
- **Timestamp format:** Integer seconds (`timestamp_seconds INT`) for evidence items (entities, instruments, assets); incident temporal boundaries use `start_timestamp` and `end_timestamp`. Storage: [`data.md`](../data.md#report_revisions).
- **Taxonomy:** `incident-contract-v2` taxonomy retained: `road accident`, `burglary`, `explosion`, `assault`, `animal attack`. Entity type: `human`, `animal`, `unknown`. Instrument `threat_level` 1-5.
- **RLS, RPC lockdown and pgvector:** [`data.md`](../data.md#row-level-security-rls--access-control).
- **Critic off by default (changed 2026-10-28; it was always on):** with `INCIDENT_SEARCH_CRITIC` set, every search candidate is evaluated by the VLM critic and both ranking and verdict are persisted; otherwise hits are `unverified`. No opt-in `--search` flag; search starts automatically with `start.sh`.
- **R2 key layout:** [`data.md`](../data.md#2-cloudflare-r2-storage-architecture).
- **Search parameters:** the REST route in [`vm-api-contracts.md`](../vm-api-contracts.md#post-apiv1incidentssearch), the RPC in [`data.md`](../data.md#search_evidence_items).

### Definitions (for PR task doc consistency)

- **Evidence crop:** A targeted object image (JPEG) extracted from a video frame at `timestamp_seconds`, cropped by an open-vocabulary detector's bounding box, stored in R2 with metadata in `evidence_crops`.
- **Critic verdict:** `confirmed` (VLM confirms the crop matches the user query), `rejected` (VLM says it does not), or `unverified` (critic evaluation failure or fallback), with an objective rationale string.
- **Query result:** A ranked search hit from `search_evidence_items`, including similarity_score, critic_verdict, and image URL.
- **One-shot baseline:** A retrieval run that scores results using only the initial query embedding, without critic re-evaluation. System baseline uses the full pipeline (critic on; it is off by default in production since 2026-10-28). [`eval.md`](eval.md) §2 proposes (P-1, pending approval) treating extraction parity (gateway vs agent P1) and full-system extraction as separate tracks.
- **Natural-language evidence search:** The user types a query (e.g. "crowbar"), the system retrieves matching evidence items across all stored incidents, each with timestamp and cropped object image.

---

## Appendix: MVP2 Plan Genealogy

- **Original MVP2 plan** (`vss-mvp2-plan-v2/report.md`): Proposed VLM bounding boxes via `incident-contract-v3`. Boxes were 0% well-framed in the box-accuracy pilot.
- **Amended plan** (after the box-accuracy pilot): VLM bounding boxes rejected (0% well-framed). Timestamps over 95% accurate. Architecture shifted to **Hybrid: VLM timestamp + open-vocabulary detector for boxes** (Option c). `incident-contract-v3` adds only `timestamp_seconds` + short searchable label per item; boxes come from a separate detector stage stored with the crops.
- **This doc** (MVP2 overview): Reflects the amended plan's end goal and architecture.
