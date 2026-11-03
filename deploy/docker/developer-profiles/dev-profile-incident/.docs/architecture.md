# Incident Search and Reporting Profile: Architecture

The architecture of `dev-profile-incident`, the incident search and reporting profile the team built on NVIDIA's Video Search and Summarization (VSS) blueprint. The page follows the arc42 section order and the C4 model's levels (context, containers, components, deployment), drawn in Mermaid so GitHub renders them, and NVIDIA's blueprint conventions for naming components and describing data flow ([conventions](#conventions)). It describes roles and interactions, not deployment values: ports, hosts, addresses, paths and settings live in the runbook, the skills and the agent contract page.

| If you need | Read |
|---|---|
| What is true today, known issues, live flags | [`status.md`](status.md) |
| What a console page shows or does, a console route, the console's configuration | [`console-reference.md`](console-reference.md) |
| An agent HTTP contract | [`vm-api-contracts.md`](vm-api-contracts.md) |
| Tables, database functions, object keys | [`data.md`](data.md) |
| The report contract, validation and storage of model output | [`analysis-schema.md`](analysis-schema.md) |
| Why something is the way it is | [`decisions.md`](decisions.md) |
| Natural-language search in depth | [`mvp2/README.md`](mvp2/README.md) |
| How to launch, operate, analyse, migrate or evaluate | [`skills/README.md`](../skills/README.md), [`incident-profile-operations.md`](incident-profile-operations.md) |

## Introduction and Goals

### Purpose

A reviewer uploads a surveillance or anomaly clip. A vision-language model (VLM) extracts a structured incident report: type, severity, confidence, a summary, and the entities, instruments and assets involved, each with the second it is seen. The report is stored as an append-only revision with the clip in object storage. The reviewer edits it into new revisions, marks one revision per video official, searches the whole archive in natural language over evidence items, asks questions about a report, and reads a dashboard that counts each video once. Model quality is measured separately by an offline evaluation pipeline on the same contract. Goals and scope in full: [`action-plan.md`](action-plan.md).

### Quality Goals

The five qualities that shaped the design most, in priority order. Scenarios for each are in [Quality Requirements](#quality-requirements).

| Priority | Quality | What it means here |
|---|---|---|
| 1 | Traceability | Every model attempt is recorded immutably with its request, raw response, validation outcome and per-stage provenance; every report revision is kept; nothing is inferred from "the latest run" |
| 2 | Contract correctness | One prompt, schema and validation policy is shared by the console, the agent and the evaluation pipeline; model output is never repaired semantically |
| 3 | Graceful degradation | Optional stages (captions, locating, zoom, tracking, embeddings) never fail an analysis; a failed attempt is a run record, never a report |
| 4 | Confidentiality | Credentials exist only in server processes; the database answers only the service role; the agent is reachable only on its host's loopback and through an SSH tunnel |
| 5 | Changeability | The console keeps a ports-and-adapters layout with import boundaries enforced by a test; the agent's detector, embedder, stores and perception client sit behind interfaces; a zero-GPU mock serves the mock's own tests |

### Stakeholders

| Role | Concern |
|---|---|
| Reviewer | Fast, accurate reports; edits that are never lost; search that finds the right evidence |
| Developer | Where code lives, which component writes which row, how to change one part without breaking the others |
| Operator of the shared GPU host | What runs natively, what runs in containers, what the launcher starts and stops, and what is shared with other users of the host |
| Evaluation owner | A contract and request that are identical across the console, the agent and the benchmark, so scores measure models, not plumbing |
| Sponsor and upstream maintainers | A build that stays recognisably the VSS blueprint, with the team's additions separable from stock code |

## Constraints

| Constraint | Background |
|---|---|
| Built on the stock VSS blueprint: the agent is NVIDIA's VSS Agent, run with the base developer profile's configuration plus the incident tool; the perception stack is the stock search profile's Compose services, started as shipped | Keeps the fork mergeable with upstream and the sponsor's blueprint recognisable; see [Stock VSS and What the Team Added](#stock-vss-and-what-the-team-added) |
| Model inference is remote: VLM, LLM and embedding calls go to hosted OpenAI-compatible endpoints; no NVIDIA Inference Microservice (NIM) container runs locally | The profile is allotted one GPU on a shared host, and that GPU is reserved for the perception stack ([decision](decisions.md#2026-09-25-remote-brev-inference-no-local-gpu-nims)) |
| One GPU on a shared host, other users on the same machine | Every container and native process that needs a GPU is pinned to that one device; shared services are started and restarted with care and on request ([decision](decisions.md#2026-10-19-pin-all-vss-docker-appliances-and-native-services-to-gpu-0-only), [decision](decisions.md#2026-10-22-concurrency-safe-native-service-startup-across-shared-vm-users)) |
| The GPU host cannot open the Postgres wire protocol to the managed database | Services on it use PostgREST over HTTPS; PostgREST holds no client transaction, so operations that must be atomic are database functions, and revision inserts retry on a unique-key clash ([decision](decisions.md#2026-09-17-agent-database-access-through-postgrest)) |
| The agent has no authentication and holds the database service key | It binds its host's loopback interface; the console reaches it through an SSH tunnel that forwards only that endpoint ([decision](decisions.md#2026-10-18-console-and-agent-bind-loopback)) |
| Hosted video models sample a bounded number of frames per request and rate-limit callers | Long clips get a caption pass before extraction; each console analyses one job at a time; object tracking runs under a detector-call budget and a time cap; the search critic is off by default |
| Secrets never reach the browser; database and storage keys live only in server processes | The console exposes no credential or private URL to the client and its health route returns booleans only |
| Two database projects, one per project phase: the finals project is live, the midterm project is a frozen read-only record | The migration folder is the schema authority; the record is never written to ([decision](decisions.md#2026-10-20-one-supabase-project-per-phase-finals-built-from-one-baseline-midterm-frozen)) |
| Evaluation is a separate hermetic pipeline owned by another team member; the console has no ground-truth screen | Model quality is measured outside the product on the same contract ([decision](decisions.md#2026-09-24-separate-eval-pipeline)) |
| Conventions: Apache-2.0 SPDX headers on every file; each component's checks run after every change; the docs and skills change in the same pull request as the behaviour they describe | The profile [`AGENTS.md`](../AGENTS.md) and the root `AGENTS.md` |

## Context and Scope

### Business Context

**Figure 1. System context diagram for the incident profile (C4 level 1).** People are rounded boxes, the system in scope is the square box, external services are cylinders or plain boxes. Solid arrows are requests the profile makes or receives; dashed arrows are reads through signed URLs that bypass the profile.

```mermaid
flowchart LR
    Reviewer(["Reviewer<br/>[Person]<br/>Uploads clips, reviews and edits reports, searches evidence, asks questions"])
    Operator(["Developer or operator<br/>[Person]<br/>Launches the profile, runs the GPU host, evaluates models"])
    Profile["Incident search and reporting profile<br/>[Software system]<br/>Console, VSS Agent with incident tools, stock perception stack"]
    R2[("Cloudflare R2<br/>[External: object storage]<br/>Videos, thumbnails, evidence images, track files")]
    DB[("Supabase<br/>[External: managed Postgres with PostgREST]<br/>Videos, runs, report revisions, crops, queries, jobs")]
    Models["Hosted model endpoints<br/>[External: OpenAI-compatible APIs]<br/>VLM, LLM and text-embedding models"]
    Reviewer -->|"Uses in a browser"| Profile
    Operator -->|"Launches and administers over SSH"| Profile
    Profile -->|"Stores media and evidence objects, signs read URLs"| R2
    Profile -->|"Reads and writes rows, calls database functions"| DB
    Profile -->|"Sends prompts with video and image URLs, receives JSON and vectors"| Models
    Reviewer -.->|"Plays video and loads images through signed URLs"| R2
    Models -.->|"Fetch the video and images through signed URLs"| R2
```

| Communication partner | Input to the profile | Output from the profile |
|---|---|---|
| Reviewer | Clips, a model choice, an optional instruction for a re-run, edits, the official choice, search queries, questions, chart requests | Reports and their versions, evidence frames with boxes and tracks, search hits, answers with citations, the dashboard and its charts |
| Developer or operator | The launch mode, feature flags, migrations, evaluation runs | Health, logs, traces, evaluation scores |
| Cloudflare R2 (S3-compatible object storage) | Existence and size checks, signed read URLs | Uploaded videos, thumbnails, evidence crops and frames, track files, short-lived scratch windows |
| Supabase (managed Postgres with PostgREST and pgvector) | Rows, function results, vector-search hits | Video rows, model runs, AI and human report revisions, evidence crops with embeddings, queries and their results, analysis jobs |
| Hosted VLM and LLM endpoints | Schema-constrained reports, captions, bounding boxes, item names and descriptions, chat answers, chart specifications | Chat-completion requests carrying signed video or image URLs and the registered prompts |
| Hosted text-embedding endpoint | Vectors for evidence passages, revision summaries and queries | Passage and query texts |

### Technical Context

| Channel | Between | Protocol and role |
|---|---|---|
| Browser to console | Reviewer's browser and `incident-console-v2` | HTTP: pages and JSON routes. Media never streams through the console: the browser plays video and loads images from object storage through signed URLs the console issues |
| Console to agent | `incident-console-v2` and the VSS Agent | JSON over HTTP. In vm mode through the SSH tunnel to the agent's loopback endpoint; in mock mode the mock backend answers the same routes |
| Console to gateway | `incident-console-v2` and `vlm-gateway` | OpenAI chat completions over HTTP, gateway mode only |
| Services to database | Console, agent, mocks, evaluation pipeline and Supabase | PostgREST REST and RPC over HTTPS with the service role. Laptop tooling and the SQL harness use direct Postgres; the evaluation pipeline can use either |
| Services to object storage | Console, agent and Cloudflare R2 | S3 API: conditional puts that never overwrite, head checks, presigned reads |
| Gateway and agent to models | `vlm-gateway`, the VSS Agent and the hosted endpoints | OpenAI-compatible chat completions and embeddings over HTTPS with a server-side credential |
| Agent to perception stack | The VSS Agent and the stock perception containers on the same host | Host-local HTTP: file upload to NvStreamer, stream provisioning on DeepStream, frame pages from the video-analytics API |
| Agent to Phoenix | The VSS Agent and the trace viewer | Traces of model calls through the NeMo Agent Toolkit (NAT) telemetry |
| Laptop to GPU host | The launcher and the host | SSH: preflight, bounded deploy and start commands, the tunnel |

## Solution Strategy

1. **Reuse the stock VSS Agent and extend it in place.** The incident routes and the one incident tool are registered through the stock worker and tool registry, so the agent is still NVIDIA's agent. It runs natively for second-level restarts; stock media and perception appliances stay in containers ([decision](decisions.md#2026-09-21-native-services-on-kwanz-ws)).
2. **Infer remotely.** VLM, LLM and embedding calls go to hosted OpenAI-compatible endpoints; the one local GPU is left to the perception stack ([decision](decisions.md#2026-09-25-remote-brev-inference-no-local-gpu-nims)).
3. **One incident contract, three consumers.** The extraction prompt, JSON Schema, request settings, prompt registry and request goldens in `contracts/` are used by the console, vendored into the agent and read by the evaluation pipeline, with a strict validation policy and ID-only repair ([decision](decisions.md#2026-09-27-unified-contract-decisions-eval-console-agent), [policy](decisions.md#2026-09-27-validation-policy-core-scored-v1), [single source](decisions.md#2026-10-21-single-source-incident-contract-with-sync-script-and-txt-prompts), [goldens](decisions.md#2026-11-01-request-goldens-pin-every-model-request-checked-by-each-producer)).
4. **Reports are append-only revisions with an explicit official pointer.** An analysis appends an AI revision, an edit appends a human revision, a re-run starts a new AI branch, and failed attempts are run records only ([decision](decisions.md#2026-10-23-versioned-report-revisions-as-jsonb-snapshots)).
5. **Two analysis modes behind one console code path.** In agent mode the console calls the native agent, which extracts, locates, tracks and persists; in gateway mode the console calls the credential-holding gateway and persists itself. Gateway mode exists for the mock backend's own tests ([decision](decisions.md#2026-10-23-the-agent-writes-jsonb-report-revisions)).
6. **Object storage for media, the database for rows, local disks as caches.** Uploads go straight to object storage under readable keys that are never overwritten; the stock video storage service is not in the path ([decision](decisions.md#2026-10-31-vst-is-dropped-from-the-consoles-upload-path-uploads-go-to-r2-only), [naming](decisions.md#2026-10-28-r2-object-names-are-readable-and-deterministic-the-original-file-name-run-n-and-edit-n-never-a-hash-or-uuid)).
7. **Search is embed-then-retrieve over per-item evidence crops**, with pgvector in the database, one search function for evidence and incident scoping, and a critic that is dormant by default ([decision](decisions.md#2026-10-18-remote-embeddings-and-remote-vlm-cropping-for-evidence-search), [decision](decisions.md#2026-10-28-incident-search-and-evidence-search-share-one-search-function-evidence-search-can-be-scoped-to-one-video-the-critic-is-off-by-default)).
8. **Tracking reuses the stock perception stack per clip** for people, and a seeded tracker corrected by the detector for instruments and assets; both are soft stages ([decision](decisions.md#2026-10-31-person-tracking-runs-the-stock-perception-stack-from-its-own-script-behind-enable_tracking), [decision](decisions.md#2026-11-01-instruments-and-assets-are-followed-by-a-seeded-opencv-tracker-corrected-by-the-detector-flag-off-by-default)).
9. **The console is hexagonal.** Routes get ports from one composition root; only adapters know tables, columns and function arguments; a test enforces the import boundaries ([decision](decisions.md#2026-10-23-incident-console-v2-uses-a-feature-module-layout-under-src), [decision](decisions.md#2026-10-24-npm-test-enforces-the-consoles-hexagonal-import-boundaries)).
10. **Long-running work is a database queue** with one in-process worker per console, so an analysis survives a closed tab ([decision](decisions.md#2026-10-31-background-analysis-jobs-a-supabase-table-and-one-in-process-loop-per-console)).

## Building Block View

### Level 1: Containers

**Figure 2. Container diagram for the incident profile (C4 level 2).** Square boxes are processes the profile runs, grouped by where they run; cylinders are managed services. Arrow labels name the interaction in the arrow's direction. The mock backend is drawn dashed because it stands in for the agent only in mock mode.

```mermaid
flowchart TB
    subgraph Workstation["Reviewer workstation"]
        Console["incident-console-v2<br/>[Container: Next.js, TypeScript]<br/>Reviewer UI, server routes, analysis orchestration, background job worker"]
        Gateway["vlm-gateway<br/>[Container: FastAPI]<br/>Holds the inference credential, forwards chat completions unchanged (gateway mode)"]
        Mock["mock-backend<br/>[Container: FastAPI, tests only]<br/>Mocks the agent, video storage and inference"]
    end
    subgraph Host["GPU host"]
        Agent["VSS Agent<br/>[Container: NAT nat serve, FastAPI worker]<br/>Stock agent plus the incident routes and tool"]
        Perception["Perception stack<br/>[Containers: NvStreamer, DeepStream, Kafka, Logstash, Elasticsearch, video-analytics API]<br/>Per-frame person boxes with track ids"]
        Phoenix["Phoenix<br/>[Container]<br/>Trace viewer for model calls"]
    end
    subgraph Cloud["Cloud services"]
        R2[("Cloudflare R2<br/>[Object storage]")]
        DB[("Supabase<br/>[Postgres, PostgREST, pgvector]")]
        Models["Hosted model endpoints<br/>[VLM, LLM, embeddings]"]
    end
    Console -->|"Analyse, search, chat, embeddings, chart requests, box tools (JSON over HTTP)"| Agent
    Console -->|"P1 chat completion, gateway mode"| Gateway
    Gateway -->|"Forwards with the server-side credential"| Models
    Console -->|"Uploads, thumbnails, reviewer evidence, signed read URLs"| R2
    Console -->|"Rows and functions: videos, runs, revisions, crops, jobs"| DB
    Agent -->|"Extraction, captions, grounding, zoom, chat, chart specs, embeddings"| Models
    Agent -->|"Evidence crops, frames, track files, scratch windows"| R2
    Agent -->|"AI revisions, placeholder runs, search, queries and results"| DB
    Agent -->|"Upload the clip, provision the stream, read frame pages"| Perception
    Agent -->|"Traces"| Phoenix
    Console -.->|"Same routes as the agent, mock mode"| Mock
    Mock -.->|"Mock analysis writes a revision marked mock"| DB
```

| Container | Technology | Responsibility | Origin |
|---|---|---|---|
| `incident-console-v2` | Next.js App Router, React, TypeScript, Node runtime | The reviewer console and its server routes: upload with H.264 conversion, analysis orchestration in both modes, the background job worker, report versions (view, edit, history, official), the library's incident and evidence search, the dashboard and chart requests, the chat panels; signs object-storage URLs; validates model output under the shared policy | Team-built |
| `vlm-gateway` | FastAPI | Holds the remote inference credential server-side and forwards chat-completion requests unchanged to the configured endpoint, with a timeout longer than the slowest model's; a health probe. Gateway mode only | Team-built |
| VSS Agent | NeMo Agent Toolkit (NAT) `nat serve` with the stock custom FastAPI worker, LangChain, Pydantic | The stock agent (tools, agents, the video-analytics MCP server) plus the incident extension: analyse, evidence search, incident search, chat, evidence embeddings, chart requests and drawing, and the reviewer box tools. Runs natively | Stock VSS, extended |
| Perception stack | Docker: NvStreamer, DeepStream perception (an RT-DETR detector with the NvDCF tracker), Kafka, Logstash, Elasticsearch, the video-analytics API | Turns an uploaded clip into per-frame person boxes with track ids, read back as frame pages. Started by the launcher when tracking is enabled and used per clip by the agent's tracking stage | Stock VSS (search profile), unchanged; pinned to the one GPU by the profile's Compose override |
| Phoenix | Docker | Trace viewer for the agent's model calls, kept for evaluation. Always started in vm mode | Stock VSS infrastructure |
| `mock-backend` | FastAPI, two projects | The base mock stands in for the agent, the stock video storage and inference in one process: its analysis writes a real AI revision marked as mock, its embeddings are deterministic, its search and chat answer from a snapshot. The search mock serves the stock search profile's routes plus incident search from the same snapshot. Used only for the mocks' own tests | Team-built |
| `contracts/` | JSON Schema, prompt files, JSON settings, fixtures, goldens | The incident contract: extraction prompt, report schema, the model allowlist and request settings, the registry of every other prompt, request goldens and validator fixtures; vendored into the agent by a sync script | Team-built |
| `supabase/` | SQL migrations, Python copy scripts, a SQL harness | The schema authority for the database: tables, functions, row-level security, tested on a throwaway local Postgres | Team-built |
| `eval/` | Python | The standalone P1/RP1 benchmark and the Evaluation V2 package: scores model output against ground truth offline on the same contract, hermetic by default | Team-built, owned by the evaluation workstream |
| Launcher and lifecycle scripts | Bash | One command from the laptop: preflight the host, heal missing services, start the native services and the tracking stack, open the tunnel, run the console; host-side start, stop, status and logs | Team-built |
| Supabase Postgres | Managed Postgres, PostgREST, pgvector | Rows for videos, model runs, report revisions, evidence crops, queries and their results, and analysis jobs; database functions for human revisions, the official pointer, crops, search, the dashboard and job claims; row-level security that admits only the service role | External |
| Cloudflare R2 | S3-compatible object storage | Uploaded videos, thumbnails, evidence crops, frames and track files, scratch windows; private, read through signed URLs | External |
| Hosted model endpoints | OpenAI-compatible HTTPS APIs | A VLM for extraction, captions, grounding, zoomed looks, box descriptions and the optional critic; an LLM for chart requests and grounded chat; a text-embedding model for evidence, revision and query vectors | External |

Optional, outside the incident flow: the stock `video-analytics-api` and `behavior-analytics` services run natively, and Elasticsearch in a container, when the launcher is asked for analytics. Not started by the profile: Video IO & Storage (VIOS, also called VST), the HAProxy ingress, Redis, the SDR controller, the stock web UI and the NIM containers ([decision](decisions.md#2026-10-31-vst-is-dropped-from-the-consoles-upload-path-uploads-go-to-r2-only)).

### Level 2: The Console

`incident-console-v2` is one Next.js process: pages and API routes on top of feature modules, each split into domain (types and port interfaces), application (use cases) and infrastructure (adapters). The composition root is the only file that chooses adapters, and it builds them per call so configuration is read at request time. Layout and import rules: [`mvp2/console-hexagonal-architecture.md`](mvp2/console-hexagonal-architecture.md). What each page does: [`console-reference.md`](console-reference.md).

**Figure 3. Component diagram for `incident-console-v2` (C4 level 3), by layer.**

```mermaid
flowchart LR
    Routes["Pages and API routes<br/>[app/]<br/>HTTP parsing, validation, status codes"] --> UseCases["Use cases<br/>[modules/*/application]"]
    UseCases --> Ports["Ports<br/>[modules/*/domain interfaces]"]
    Root["Composition root<br/>[lib/container.ts]"] -.->|"chooses adapters per call"| Ports
    Ports --> Supa["Supabase adapters<br/>[PostgREST tables and functions]"]
    Ports --> R2A["R2 adapters<br/>[S3 API]"]
    Ports --> AgentA["Agent adapters<br/>[JSON over HTTP]"]
    Ports --> GatewayA["Gateway adapter<br/>[chat completions]"]
    Ports --> Fakes["In-memory fakes<br/>[tests]"]
```

| Module | Responsibility | Ports it drives |
|---|---|---|
| `analysis` | The one analysis path for uploads and re-runs: verify the stored object, sign a read URL, build the P1 request that equals the evaluation pipeline's, call the gateway or the agent, validate under the shared policy, record the attempt, store or verify the AI revision | `AnalysisRunRepository`, `VideoObjectStore`, `ModelGateway`, `IncidentAgent`, `ContractCatalog` |
| `jobs` and the job loop | The background analysis queue: queue, claim, heartbeat, finish, retry and cancel; one worker started once per process | `AnalysisJobRepository` |
| `reports` | Read one revision and its run into a report view; validate and save human edits; cut reviewer items (crop, frame, track) on the server; mark or clear the official revision; store searchable crops; fetch evidence embeddings; proxy the box tools | `IncidentRepository`, `EvidenceObjectStore`, `ReviewerEvidenceStore`, the agent's embeddings and box-tool adapters |
| `videos` | Video rows, run history, re-generation, delete | `VideoRepository` |
| `search` | Evidence search and incident (revision) search | `EvidenceSearchRepository`, `RevisionSearchRepository` (agent adapters) |
| `chat` | The report and library chat | The agent chat adapter |
| `dashboard` | The dashboard query, the chart specification allowlist, the chart interpreter and renderer | `DashboardRepository`, `ChartInterpreter`, `ChartRenderer` |
| `contract` | A strict port of the evaluation pipeline's validator, and the catalogue that loads prompts, schema and allowlist from `contracts/` | `ContractCatalog` |
| `health` | Readiness as booleans only | `HealthRepository` |
| `shared` | The kernel: run keys, pages, the store-rejection type that routes turn into a client error, the adapter bundle type | |

### Level 2: The Agent's Incident Extension

The extension is a set of routes registered by the stock worker, one NAT tool, a vendored copy of the contract, and utilities behind small interfaces. Request and response shapes: [`vm-api-contracts.md`](vm-api-contracts.md).

| Component | Kind | Responsibility |
|---|---|---|
| `analyze` route | Route | The console's trigger in agent mode: runs the incident tool on a signed video URL and returns the validated report, the raw output, the revision id, the run provenance and the tracking summaries; refuses experimental settings unless the agent is started for experiments |
| `incident_report_gen` | NAT tool | The analysis pipeline below, registered in the base profile's agent configuration |
| `incident_contract/` | Vendored package | The contract files and prompt registry the agent reads, kept equal to `contracts/` by the sync script |
| `search` and `revisions/search` routes | Routes | Embed the query and retrieve by cosine distance through a database function: evidence crops (optionally judged by the critic, with the query and its ranked results recorded and the media URLs signed), or AI revisions by their summary embedding |
| `evidence-embeddings` route | Route | Embeds a revision's evidence items as passage text with the same model search uses; returns vectors only, the console writes the rows |
| `chat` route | Route | Template mode composes an answer from one report version without a model call; grounded mode (behind a flag) answers from one version with a chat model and checked citations, and answers library questions by a planned, counted search |
| `chart-requests` and `charts` routes | Routes | The agent's own LLM turns a dashboard request into a chart specification; the stock chart generator draws the figures the console sends |
| `box/describe` and `box/track` routes | Routes | Name and describe a reviewer-drawn box with the VLM; follow the box through the clip with the object tracker. Nothing is stored |
| Detector, zoom namer, evidence store, scratch store, perception client, embedder | Interfaces with one production implementation each | The remote VLM as the open-vocabulary detector and namer, object storage as the evidence and scratch store, HTTP clients for the perception stack and the embedding endpoint; in-memory and fake variants for tests |

The analysis pipeline, in order. "Soft" means a failure is recorded on the item or the run and the analysis continues.

| Stage | What it does | Soft | Code |
|---|---|---|---|
| Caption pass (flag) | Cuts a long clip into overlapping windows, captions each with the VLM, offsets the stamps and hands the captions to P1 as context; scratch objects are deleted whether or not the pass succeeds | Yes | `utils/chunk_captions.py` |
| Set-of-marks (flag, needs tracking) | Runs tracking before P1, draws a numbered box on each person track on a scratch copy of the clip, asks P1 for the number in each person's label, then strips the numbers | Yes | `utils/set_of_marks.py` |
| P1 extraction | Sends the signed video URL, the contract prompt and any reviewer prompt with the schema enforced through structured output and the contract's fixed settings; validates under `core-scored-v1` with `id-normalization-v1`; retries a runaway output once; applies the video-bounds rules with the caller's duration or the length read from the MP4 header | No: a violation is a client error and nothing is persisted | `tools/incident_report_gen.py`, `incident_contract/` |
| RP1 prose report | A text report from the validated P1 JSON; the console asks for P1 only | Yes | `tools/incident_report_gen.py` |
| Locate and crop | Finds each entity, instrument and asset at its second with the detector, retrying the neighbouring seconds (each try is one detector call); stores the whole frame and the crop; an unfound item keeps a recorded gap | Yes | `utils/crop_evidence.py` |
| Zoomed second look | Two blind looks at a located item (the marked frame, then a native-resolution close-up) and a merge against the report's text; renames the item when the looks agree with enough confidence, keeping the previous name | Yes | `utils/crop_evidence.py` |
| Person tracking (flag) | Pushes the clip through the perception stack, builds person tracks from the frame pages, matches each located person to a track by box overlap, re-cuts the person's evidence at the track's first sample, stores the track file | Yes | `utils/track_evidence.py`, `utils/perception_client.py` |
| Object tracking (flag) | Follows each located instrument and asset with a seeded single-object tracker forwards and backwards, re-anchored by the detector at intervals under its own budget and time cap; stores a track in the same shape | Yes | `utils/object_tracking.py` |
| Revision | Appends the AI revision (payload, evidence, facet columns, no parent) with a summary embedding of its title and description; retries on a revision-number clash; inserts a placeholder run row only when the caller did not create one | Best effort | `utils/incident_db.py`, `utils/embeddings.py` |
| Response and provenance | Returns the report, raw output, every P1 attempt, the tracking summaries and the per-stage provenance (model, prompt hashes, calls, tokens, time) | | `utils/run_provenance.py` |

### Shared Contract

`contracts/` is the single source of the model interface ([`analysis-schema.md`](analysis-schema.md), [`contracts/README.md`](../contracts/README.md)): the extraction prompt, the JSON Schema (strict-mode compatible), the model allowlist and request settings, the registry of every other prompt (captions, grounding, zoom, chat, charts), request goldens that pin each producer's request bytes, and validator fixtures that both validators must agree on. The console reads it at request time through its contract catalogue; the agent reads its vendored copy; the evaluation pipeline reads it directly. A contract change bumps the version and updates all three together.

## Stock VSS and What the Team Added

Derived from the git history since the profile's first commit. The rule: team code lives in the profile directory or in files named `incident_*`; stock files are touched only to register or configure.

| Area | Stock VSS, unchanged | Stock VSS, modified by the team | Added by the team |
|---|---|---|---|
| Agent process (`services/agent`) | The NAT agents and tools (top agent, report agents, video understanding, the video-storage tools, search, the chart generator's core), embeddings, evaluators, the video-analytics MCP server, the stock ingest routes | The custom FastAPI worker and the route and tool registries (register the incident routes and tool); `chart_generator` (serves the dashboard chart routes); `video_report_gen` (headless analysis and hosted-endpoint fixes); the package metadata, README and `AGENTS.md` | Eight `api/incident_*.py` route modules; `tools/incident_report_gen.py` and `tools/incident_experiments.py`; `incident_contract/`; the incident utilities under `utils/` (database client, crop evidence, track evidence, object tracking, set-of-marks, chunk captions, perception client, embeddings, object storage, grounded chat, report answers, run provenance, box track, video duration) and their unit tests |
| Agent configuration | The base developer profile's workflow, LLM and telemetry blocks | The same configuration file registers `incident_report_gen` and names the LLM the chart routes use | The environment flags the incident code reads (tracking, object tracking, set-of-marks, caption pass, zoom names, chat mode, critic, experiments) |
| Perception and infrastructure | NvStreamer, DeepStream perception, Kafka, Logstash, Elasticsearch, the video-analytics API, Phoenix, the stock Compose service definitions | A shared-GPU hardware variant of a NIM environment file and the HAProxy template (both historical: neither NIMs nor the ingress are started) | The profile's Compose override (GPU pinning) and tracking Compose file, the NvStreamer configuration, the tracking-stack script |
| Deployment packaging | The `dev-profile.sh` helper and the stock profiles, never used for this profile | The developer-profiles Compose index includes the profile's Compose file; the agent's Compose service passes the incident database settings (the container itself is not used) | `start.sh`, `.scripts/`, `.dotfiles/`, the profile's `compose.yml` |
| User interface | The stock web UI, not started | | `incident-console-v2`, `vlm-gateway`, `mock-backend`; the archived v1 console is read-only history |
| Storage and data | VIOS/VST and the stock object store, not started | | `supabase/` (migrations, harness, copy scripts), `contracts/`, the object-storage key layout |
| Evaluation | NAT evaluation and the stock evaluators, not used | | `eval/` |
| Operations and documentation | The stock `skills/` catalogue | The repository `AGENTS.md` and the Docker README point at the profile | The profile's `skills/`, `.docs/`, `AGENTS.md` and `README.md` |

## Runtime View

A representative selection; each step names the building block that performs it. Contracts for the agent calls: [`vm-api-contracts.md`](vm-api-contracts.md); who writes which row: [`analysis-schema.md`](analysis-schema.md#6-agent-mode) and [`data.md`](data.md).

### Analysis Run in Agent Mode

**Figure 4. Analysis of an uploaded clip in agent mode (the vm mode default).**

```mermaid
sequenceDiagram
    autonumber
    actor Reviewer
    participant Console as incident-console-v2
    participant R2 as Cloudflare R2
    participant DB as Supabase
    participant Agent as VSS Agent
    participant Perception as Perception stack
    participant Models as Hosted model endpoints

    Reviewer->>Console: Choose a model, drop one or more clips
    Console->>Console: Probe the file, convert to an H.264 MP4 unless it is one
    Console->>R2: Conditional put of the video, head check of the stored size
    Console->>DB: Queue an analysis job for the stored object
    Console->>DB: The job loop claims the oldest queued job and heartbeats while it runs
    Console->>R2: Verify the object, sign a read URL
    Console->>DB: Create the video row once, with the job's video id
    Console->>Agent: analyze (signed URL, object key, run id, model, reviewer prompt, duration)
    opt Caption pass enabled and the clip is long
        Agent->>R2: Cut and upload caption windows to scratch
        Agent->>Models: One caption call per window
        Agent->>R2: Delete the scratch windows
    end
    Agent->>Models: P1 extraction: video URL, contract prompt, schema-constrained output
    Models->>R2: Fetch the video through the signed URL
    Models-->>Agent: Report JSON
    Agent->>Agent: Strict validation (core-scored-v1, id-normalization-v1) and video bounds
    par Evidence for every entity, instrument and asset
        Agent->>Models: Locate the item at its second (detector)
        Agent->>R2: Store the whole frame and the crop
        Agent->>Models: Zoomed second look, rename when confident
    and Person tracking enabled
        Agent->>Perception: Upload the clip, provision the stream, read frame pages
        Agent->>Agent: Build tracks, match people by box overlap, re-cut at first seen
        Agent->>R2: Store track files
    and Object tracking enabled
        Agent->>Models: Detector re-anchors a seeded tracker at intervals
        Agent->>R2: Store object track files
    end
    Agent->>Models: Embed the report's title and description
    Agent->>DB: Append the AI revision (payload, evidence, facets, summary embedding)
    Agent-->>Console: Report, raw output, revision id, provenance, tracking summaries
    Console->>Console: Validate the raw output again under the same policy
    Console->>DB: Write the immutable attempt record on the run, verify the revision
    Console->>Agent: Evidence embeddings for the new revision (video has no official revision)
    Console->>DB: Store the searchable crops, finish the job
    Console-->>Reviewer: The job's row links to the report
```

1. **Upload** (console): the browser sends the file to a console route, which probes it and converts it to an H.264 MP4 at the original size unless it already is one, puts it in object storage under a readable key that is never overwritten, and verifies the stored size. The browser also uploads a thumbnail frame.
2. **Queue** (console, database): the page queues a job with the chosen model; the console's job loop claims the oldest queued job, heartbeats while it runs and takes the next one when it finishes. A reopened tab shows the same jobs ([Background Analysis Jobs](#background-analysis-jobs)).
3. **Prepare** (console): the analysis use case verifies the object, signs a read URL, creates the video row once, and calls the agent with the signed URL, the object key, its own run id, the model and any reviewer prompt. The console owns the video row and the run row; the agent never writes the video row.
4. **Extract** (agent): optional captions, then P1 with the shared prompt and schema; the agent validates under the same policy as the console and the evaluation pipeline. A contract violation is answered as a client error with the raw output, and nothing is persisted.
5. **Evidence** (agent): locate, crop and store each item; the zoomed second look; person tracking through the perception stack; object tracking for instruments and assets. Every stage is soft.
6. **Persist** (agent): the AI revision with its evidence and summary embedding. The agent inserts a placeholder run row only when the console's does not exist yet.
7. **Record** (console): the console validates the raw output again, writes the immutable attempt record on its run row, verifies the agent's revision (and inserts one only when the agent returned none), embeds the new revision's evidence through the agent and stores the searchable crops when the video has no official revision yet, then finishes the job with the video and revision ids. A failed attempt finishes the job as failed with the reason and leaves a run record, never a report.

### Analysis Run in Gateway Mode

Gateway mode keeps the same console code path and swaps the model port: the console builds the identical P1 request, the gateway forwards it with the server-side credential, and the console persists the result itself. No detector runs, so the evidence entries have no images. It is started by the launcher's mock mode, where the mock backend answers the agent's routes, and is used only for the mocks' own tests.

**Figure 5. Analysis in gateway mode.**

```mermaid
sequenceDiagram
    autonumber
    participant Console as incident-console-v2
    participant Gateway as vlm-gateway
    participant Models as Hosted model endpoints
    participant R2 as Cloudflare R2
    participant DB as Supabase
    participant Agent as Agent or mock backend

    Console->>R2: Verify the object, sign a read URL
    Console->>Gateway: P1 chat completion, identical to the agent's and the evaluation pipeline's
    Gateway->>Models: Forward unchanged, with the server-side credential
    Models->>R2: Fetch the video through the signed URL
    Models-->>Gateway: Report JSON
    Gateway-->>Console: Report JSON
    Console->>Console: Strict validation, ID-only repair when every violation is an id
    alt Valid
        Console->>DB: Attempt record on the run, then the AI revision
        Console->>Agent: Evidence embeddings
        Console->>DB: Store the searchable crops, finish the job
    else Contract or request failure
        Console->>DB: Attempt record only, job failed with the reason
    end
```

### Background Analysis Jobs

1. A queued job is a row with the stored object, the file name and the model ([`data.md`](data.md#analysis_jobs)).
2. One loop per console process claims the oldest queued job through a database function that locks the row and skips locked ones, so several consoles never claim the same job; it heartbeats while the job runs and finishes it as done or failed through functions that check the claim.
3. A running job whose heartbeat stops is treated as lost: the next claim by any console sends it back to the queue once and fails it after a second loss, so nothing hangs.
4. Every attempt of a job analyses into the same video id, so a retry adds revisions to one video instead of a second video row on the same object.
5. One job at a time per console keeps within the hosted endpoint's rate limits; the loop is not an HTTP request, so each model's own timeout from the contract settings bounds an analysis. Every console pointed at the same database shares the queue ([status](status.md#4-known-issues), [console reference](console-reference.md#background-analysis-jobs)).

### Evidence Search and Incident Search

**Figure 6. Natural-language evidence search.**

```mermaid
sequenceDiagram
    autonumber
    actor Reviewer
    participant Console as incident-console-v2
    participant Agent as VSS Agent
    participant Models as Embedding endpoint
    participant DB as Supabase
    participant R2 as Cloudflare R2

    Reviewer->>Console: Type a query in Find evidence, optionally scoped to one incident
    Console->>Agent: search (query, filters, optional video id)
    Agent->>Models: Embed the query
    Agent->>DB: search_evidence_items: cosine distance over the searchable revisions' crops, filters applied before ranking
    opt Critic enabled (off by default)
        Agent->>Models: Judge each hit against the query with its crop
    end
    Agent->>DB: Record the query and its ranked results
    Agent->>R2: Sign crop and playback URLs
    Agent-->>Console: Ranked hits with item, incident and verdict
    Console-->>Reviewer: Tiles, filters, grouping by incident, the frame viewer
```

1. **What is searchable** (database): the evidence crops of each video's searchable revision, its official revision or else its newest, embedded as passage text; rows are written by the console when a revision is saved or marked official, with vectors from the agent's embeddings route ([`data.md`](data.md#evidence_crops)).
2. **Evidence search** (agent): one embed-then-retrieve function serves both the library-wide search and a search scoped to one incident; an empty scoped query lists that incident's evidence in time order. The critic is dormant by default, so every hit is unverified and a search costs one embedding call and one vector query.
3. **Incident search** (agent, console): the Find incidents text search embeds the query and retrieves the nearest AI revisions by their summary embedding through a second database function; the console filters the hits and falls back to a plain text match on the library when the agent fails or finds nothing.
4. **Specification** in depth: [`mvp2/README.md`](mvp2/README.md).

### Reviewer Edit, the Official Version, Chat and Charts

1. **Save as new version** (console, database): the edit route accepts only the structured fields and each item's image, validates the edit against the contract's sub-schemas and cross-field rules, cuts a reviewer-drawn item's crop, frame and track on the server from the stored video, asks the agent for embeddings when the video has no official revision, and calls one database function that appends a human revision whose parent is the opened revision and stores its crops in the same call. Earlier revisions are never changed.
2. **Mark official** (console, agent, database): the console fetches the revision's evidence embeddings from the agent and calls a database function that sets the video's official pointer and rebuilds that incident's searchable crops; clearing removes them. An unreachable agent fails the mark and changes nothing.
3. **Re-generation** (console, agent): the same analysis path on the stored object with a chosen model and an optional reviewer prompt; always a new run and a new AI branch that copies no earlier edit, never a change to the video row or the official pointer.
4. **Chat** (console, agent): the panel sends the version on screen, or the draft being edited; template mode composes the answer from that version's report with no model call; grounded mode, behind an agent flag, answers from the version with a chat model and checked citations and answers library questions by a planned, counted search over report summaries.
5. **Dashboard charts** (console, agent): the dashboard reads one database function that counts each video once (official revision, else newest). For a chart request the console sends its prompt and the request to the agent, whose own LLM picks a chart specification; the console validates it against its allowlist, computes the figures from the database itself, and asks the agent to draw them with the stock chart generator. The model never sees a figure.
6. **Reviewer box tools** (console, agent): in edit mode the console proxies two agent routes: describe names a drawn box with the VLM, track follows it with the object tracker; nothing is stored until Save.

## Deployment View

### Deployment Nodes

**Figure 7. Deployment diagram for the vm mode of the profile.** Mock mode runs the console, the gateway and the mock backend on the workstation alone, with the same cloud services.

```mermaid
flowchart LR
    subgraph Laptop["Reviewer workstation (laptop)"]
        ConsoleI["incident-console-v2<br/>[Node process]"]
        GatewayI["vlm-gateway<br/>[Python process, mock mode only]"]
        MockI["mock-backend<br/>[Python process, mock mode only]"]
    end
    subgraph GPUHost["Shared GPU host"]
        AgentI["VSS Agent<br/>[native process, bound to loopback]"]
        subgraph Containers["Docker containers"]
            PhoenixI["Phoenix"]
            PerceptionI["Perception stack<br/>[pinned to the one GPU, started when tracking is enabled]"]
        end
    end
    subgraph CloudI["Managed cloud services"]
        R2I[("Cloudflare R2")]
        DBI[("Supabase")]
        ModelsI["Hosted model endpoints"]
    end
    ConsoleI -->|"SSH tunnel forwarding only the agent's endpoint"| AgentI
    ConsoleI -->|"HTTPS"| R2I
    ConsoleI -->|"HTTPS"| DBI
    ConsoleI -.->|"mock mode"| GatewayI
    ConsoleI -.->|"mock mode"| MockI
    GatewayI -->|"HTTPS"| ModelsI
    AgentI -->|"HTTPS"| R2I
    AgentI -->|"HTTPS"| DBI
    AgentI -->|"HTTPS"| ModelsI
    AgentI -->|"host-local HTTP"| PerceptionI
    AgentI -->|"traces"| PhoenixI
```

| Node | Runs | Notes |
|---|---|---|
| Reviewer workstation | `incident-console-v2`; in mock mode also `vlm-gateway` and `mock-backend`; the laptop side of the launcher | The console's own `ffmpeg` and `ffprobe` come with its dependencies; the browser reaches media in object storage directly through signed URLs |
| Shared GPU host | The VSS Agent as a native process bound to loopback; Phoenix in a container; the perception stack in containers pinned to the one GPU; optionally the stock analytics services | The agent is installed editable from the repository checkout, so a code change needs a restart, not a rebuild; its start installs a static `ffmpeg` build and the tracker model files when the host lacks them |
| Managed cloud services | Cloudflare R2, Supabase, the hosted model endpoints | Reached over HTTPS from both the workstation and the host; the database admits only the service role |

### Native Processes and Containers

The policy is to run a service natively whenever that is feasible, and in a container when it is not ([decision](decisions.md#2026-09-21-native-services-on-kwanz-ws)):

- **Native:** the VSS Agent, and the optional analytics services. A native process restarts in seconds after a code change, against a multi-minute image build, and the agent's virtual environment is installed editable from the checkout.
- **Containers:** the media and perception appliances (NvStreamer, DeepStream perception) depend on CUDA and GStreamer toolchains that are not installable on the host, and the infrastructure appliances (Kafka, Logstash, Elasticsearch, Phoenix) gain nothing from running natively. The profile's Compose override pins every GPU-bound container to the one allotted device. The stock Compose files use fixed container names, so one perception stack runs per host.
- **Not started:** the stock video storage and ingress services, Redis and the SDR controller were removed from the deployment when uploads moved straight to object storage; the stock web UI and the NIM containers are never started.

### Launch Modes and Connectivity

The launcher is the one daily entry point ([`incident-start`](../skills/incident-start/SKILL.md)); the procedures and troubleshooting are in the [runbook](incident-profile-operations.md#3-laptop-to-vm-connectivity--ssh-troubleshooting).

- **vm mode** (the default, used for all UI and backend work): the launcher probes SSH to the host and resolves its address, inspects which containers and native services are up, deploys or starts only what is missing within bounded timeouts, starts the perception stack unless tracking is disabled, opens a backgrounded SSH tunnel that forwards only the agent's loopback endpoint, waits for the agent's health through it, and starts the console in agent mode. Leaving the launcher stops only the laptop-side processes and the tunnel; the host keeps running ([decision](decisions.md#2026-11-02-closing-startsh-leaves-the-vm-backend-running)).
- **mock mode** (the mock backend's tests only, never a fallback when the host is unreachable): the launcher starts the mock backend and the gateway on the workstation and the console in gateway mode; no SSH is used.
- **Connectivity:** the console talks to the agent through the tunnel, and to object storage and the database directly; the agent talks to the perception stack on its own host, and to object storage, the database and the model endpoints directly. The agent itself listens only on its host's loopback interface, so the tunnel is the one way in.

### Two Supabase Projects (One per Phase)

There is one database project per project phase ([decision](decisions.md#2026-10-20-one-supabase-project-per-phase-finals-built-from-one-baseline-midterm-frozen)). **Finals** is the live database: the console, the agent, the mocks and the evaluation pipeline all point at it. **Midterm** is the frozen record of the earlier phase: read-only, never pushed to, kept in the repository as an archive. Finals was built from one baseline migration and filled once from midterm by a one-way copy; the versioned-report schema and its functions were applied on top through the migration folder, which remains the schema authority ([`supabase/README.md`](../supabase/README.md)). Object storage is shared by both projects: the same keys, no copy. Which settings point each component at the live project, and the cutover checklist, are in the [`incident-manage-database`](../skills/incident-manage-database/SKILL.md) skill; the state of each migration is in [`status.md`](status.md#3-database-status).

## Crosscutting Concepts

### Incident Contract and Validation

The model's output is defined once, in `contracts/`, as a strict JSON Schema plus the prompt and the request settings. Every consumer sends the same request (pinned by goldens) and validates the response the same way: a strict parse with no fence stripping or defaults, the schema, the cross-field rules, and the video-bounds rules when the length is known. Only id renumbering may be repaired, and only when every violation is an id problem; the repaired output is re-validated. Unscored enrichment fields (the timeline) may fail without failing the report. Every violation carries a machine-readable code, and the shared fixtures are checked by both the Python and the TypeScript validator. Details: [`analysis-schema.md`](analysis-schema.md).

### Versioned Reports and the Official Pointer

A report is a JSONB snapshot: the whole contract report as the payload, the evidence list beside it, and only the facets statistics filter on copied into columns. An analysis appends an AI revision with no parent; a human edit appends a human revision whose parent is the revision the reviewer opened and which inherits its run; a re-run is a new AI branch. A trigger rejects updates and deletes. The official revision is an explicit pointer on the video, set and cleared only by a database function; "latest run wins" is never inferred. The searchable revision is the official one, or else the newest. Model: [`data.md`](data.md#the-report-model).

### Storage and Naming

Object storage holds the media; the database holds rows; local disks on the host are caches. Keys are readable and deterministic: the upload keeps its file name, each analysis gets the next run number, each reviewer save the next edit number; no key holds a hash and no object is ever overwritten, because every put is conditional. Each evidence item has a crop (what search embeds), the whole frame it was found in (what the console shows, with the box drawn), and, when tracked, a track file in one shape for people and objects. Scratch objects are deleted by the stage that wrote them. Layout and rules: [`data.md`](data.md#2-cloudflare-r2-storage-architecture).

### Model Inference

All model calls are remote and OpenAI-compatible ([decision](decisions.md#2026-09-25-remote-brev-inference-no-local-gpu-nims)). The rules, and why they hold:

1. **Client type.** The agent's LLM and VLM clients are configured as OpenAI-type clients, never as NIM-type clients: the NIM LangChain client leaks client-only parameters (such as the SSL-verification flag) into the HTTP request body, which standard OpenAI-compatible endpoints reject with a bad-request error.
2. **Base URLs carry no version path.** The agent and the LangChain clients append the API version segment themselves, so a configured base URL that already ends with it produces a wrong path.
3. **One credential, many roles.** One server-side key serves the agent's LLM, VLM and evaluation judge; the gateway holds its own copy of the hosted key for the console's gateway mode, so the browser never sees it.
4. **Capabilities the models must have.** The LLM must support tool calling for the stock top agent and schema-constrained output for incident reporting and chart specifications; the VLM must accept a video by URL with schema-constrained output (inline base64 video gave the same results in the contract pilot) and images for the grounding, zoom and box-description calls. The allowlisted VLMs, their request settings and per-model timeouts are in the contract settings; the console, the agent and the job loop take each model's own timeout.
5. **Sparse frames.** Hosted video models sample a bounded number of frames per request, so a long clip is seen sparsely; the caption pass exists to recover short actions, and how sampling behaves per model is an open question recorded in [`status.md`](status.md#4-known-issues).
6. **Embeddings** come from one text-embedding model on an OpenAI-compatible embeddings endpoint, used for evidence passages, revision summaries and queries alike, so cosine distance is meaningful; no local embedding model runs in the product.

### Secrets and Access Control

Credentials live only in server processes: the console's server routes, the gateway and the agent. Nothing credential-like is exposed to the browser, and the console's health route answers booleans. The database is locked down from its first migration: row-level security on every table with no policies, every grant revoked from the public roles, every function callable by the service role only and pinned to an empty search path, a self-check that fails the migration otherwise, and a security check to run after every applied migration. Services reach the database as the service role over PostgREST. Object storage is private and read through expiring signed URLs. The agent has no authentication, binds its host's loopback and is reached only through the SSH tunnel; its log redacts signed URLs to their object key. Security details: [`data.md`](data.md#row-level-security-rls--access-control).

### Resilience and Recorded Failure

Nothing optional fails an analysis: a caption, locate, zoom, tracking or embedding failure is recorded on the item or the run (a gap or a skip reason) and the pipeline continues; the console's thumbnail upload and searchable-crops step are best effort too. A contract or request failure is an attempt record on the run with the raw output, never a report, and the job fails with a plain reason that the page shows with a retry. Writes that cannot be atomic over PostgREST are database functions; the revision insert retries on a number clash; a lost console is detected by a missing heartbeat and its job re-queued once. Model calls are retried once on a runaway output and on rate limiting where a stage sends several calls; object tracking runs under a detector-call budget and a time cap, so an analysis stays bounded.

### Observability and Provenance

Every analysis returns its provenance: the agent's code identity, the flags that were in effect, and per stage the model, the prompt hashes from the registry, the HTTP attempts sent, failed and refused by a cap, the tokens and the time. The console keeps it in the run's immutable attempt record with the request, the raw response, the validation result and any repair ([decision](decisions.md#2026-11-01-every-analysis-returns-its-run-provenance-and-the-console-keeps-it-in-the-runs-notes)). Search records every query and its ranked results. The agent's model calls are traced to Phoenix. Health endpoints exist on the console, the agent and the gateway; the launcher waits on the agent's before it starts the console.

### Configuration and Feature Flags

Configuration describes roles here; the names and values are in the [runbook](incident-profile-operations.md), the skills and the live-configuration section of [`status.md`](status.md).

- **Console:** the analysis mode (gateway or agent), the default model from the allowlist, where the agent or mock and the gateway are, the database and object-storage settings, the contract directory, and a switch that keeps the job loop stopped. A model port is built only when its location is configured; the analysis refuses the mode whose port is missing.
- **Agent:** flags for person tracking, object tracking, set-of-marks prompting, the caption pass, the zoomed second look, the chat mode, the search critic and the experimental settings; the perception stack's locations; the embedding endpoint; the object-storage and database settings. The host-side start script applies the profile's defaults (tracking, object tracking and the caption pass on) unless the host's own settings turn them off.
- **Launcher:** the mode, a switch for the perception stack, a switch for the analytics services, and a flag that brings the backend up without the console.

### Testing Strategy

Each container is checked on its own, hermetically. The console runs Biome and the TypeScript compiler, unit tests beside each module, the import-boundary test, and contract suites that run the same cases against the in-memory fakes and the Supabase adapters over a fake PostgREST. The agent runs Ruff, mypy and a pytest tree that mirrors the source, with every external service mocked. The contract package's fixtures and goldens are checked by the console, the agent and the evaluation pipeline. The schema is applied from scratch on a throwaway Postgres by the SQL harness, which also runs the security check. The mocks have their own pytest suites, and the evaluation pipeline's tests need no database, agent or GPU. Live verification happens only on the GPU host through the launcher's vm mode.

## Architecture Decisions

The decisions that still shape the profile, newest first within each theme, with their records in [`decisions.md`](decisions.md). The log keeps every decision with its alternatives; a reversed decision is removed from it, and this table changes with it.

| Theme | Decision | Record |
|---|---|---|
| Platform | Remote inference on hosted endpoints; no local NIM containers | [2026-09-25](decisions.md#2026-09-25-remote-brev-inference-no-local-gpu-nims) |
| Platform | The agent and the optional analytics services run natively; appliances stay in containers | [2026-09-21](decisions.md#2026-09-21-native-services-on-kwanz-ws) |
| Platform | Every GPU-bound service is pinned to the one allotted GPU | [2026-10-19](decisions.md#2026-10-19-pin-all-vss-docker-appliances-and-native-services-to-gpu-0-only) |
| Platform | The console and the agent bind loopback; the tunnel forwards only the agent | [2026-10-18](decisions.md#2026-10-18-console-and-agent-bind-loopback) |
| Platform | Leaving the launcher leaves the host's backend running | [2026-11-02](decisions.md#2026-11-02-closing-startsh-leaves-the-vm-backend-running) |
| Data | Agent database access through PostgREST | [2026-09-17](decisions.md#2026-09-17-agent-database-access-through-postgrest) |
| Data | One database project per phase; finals from one baseline; midterm frozen | [2026-10-20](decisions.md#2026-10-20-one-supabase-project-per-phase-finals-built-from-one-baseline-midterm-frozen) |
| Data | Report revisions are append-only JSONB snapshots with an explicit official pointer | [2026-10-23](decisions.md#2026-10-23-versioned-report-revisions-as-jsonb-snapshots) |
| Data | The agent writes the AI revision in agent mode | [2026-10-23](decisions.md#2026-10-23-the-agent-writes-jsonb-report-revisions) |
| Data | Uploads go straight to object storage; the stock video storage is dropped from the path | [2026-10-31](decisions.md#2026-10-31-vst-is-dropped-from-the-consoles-upload-path-uploads-go-to-r2-only) |
| Data | Object keys are readable and deterministic, never hashes | [2026-10-28](decisions.md#2026-10-28-r2-object-names-are-readable-and-deterministic-the-original-file-name-run-n-and-edit-n-never-a-hash-or-uuid) |
| Data | A video with no official revision is searchable through its newest revision | [2026-10-23](decisions.md#2026-10-23-a-video-with-no-official-revision-is-searchable-through-its-newest-revision) |
| Contract | One contract for the evaluation pipeline, the console and the agent, with the `core-scored-v1` policy | [2026-09-27](decisions.md#2026-09-27-unified-contract-decisions-eval-console-agent), [2026-09-27](decisions.md#2026-09-27-validation-policy-core-scored-v1) |
| Contract | Every other prompt lives in one registry; request goldens pin every producer's request | [2026-11-01](decisions.md#2026-11-01-every-model-prompt-besides-p1-and-rp1-lives-in-one-registry-contractsprompts), [2026-11-01](decisions.md#2026-11-01-request-goldens-pin-every-model-request-checked-by-each-producer) |
| Contract | Every analysis returns its run provenance | [2026-11-01](decisions.md#2026-11-01-every-analysis-returns-its-run-provenance-and-the-console-keeps-it-in-the-runs-notes) |
| Search | Remote embeddings and a remote-VLM detector for evidence crops | [2026-10-18](decisions.md#2026-10-18-remote-embeddings-and-remote-vlm-cropping-for-evidence-search) |
| Search | One search function for evidence and incident scoping; the critic off by default | [2026-10-28](decisions.md#2026-10-28-incident-search-and-evidence-search-share-one-search-function-evidence-search-can-be-scoped-to-one-video-the-critic-is-off-by-default) |
| Search | Multi-subagent routing and full real-time indexing dropped from MVP2 | [2026-10-24](decisions.md#2026-10-24-drop-multi-subagent-routing-and-full-rt-cv--rt-embed-indexing-from-mvp2) |
| Evidence | The whole frame with a box is shown, the crop is kept for search | [2026-10-31](decisions.md#2026-10-31-evidence-shows-the-whole-frame-with-a-box-not-a-crop) |
| Evidence | Person tracking runs the stock perception stack per clip, behind a launcher switch | [2026-10-31](decisions.md#2026-10-31-person-tracking-runs-the-stock-perception-stack-from-its-own-script-behind-enable_tracking) |
| Evidence | Instruments and assets are followed by a seeded tracker corrected by the detector | [2026-11-01](decisions.md#2026-11-01-instruments-and-assets-are-followed-by-a-seeded-opencv-tracker-corrected-by-the-detector-flag-off-by-default) |
| Evidence | The caption pass runs before P1 and is on by default in vm mode | [2026-10-31](decisions.md#2026-10-31-chunked-captioning-before-p1-the-gateways-fixed-2-fps-and-ignored-num_frames), [2026-11-01](decisions.md#2026-11-01-the-caption-pass-is-on-by-default-in-vm-mode-ffmpeg-comes-from-a-repo-local-static-build) |
| Evidence | The zoomed second look renames a located item from its close-up | [2026-10-31](decisions.md#2026-10-31-zoomed-second-look-renames-a-located-item-from-its-close-up) |
| Console | A feature-module (hexagonal) layout with import boundaries enforced by a test | [2026-10-23](decisions.md#2026-10-23-incident-console-v2-uses-a-feature-module-layout-under-src), [2026-10-24](decisions.md#2026-10-24-npm-test-enforces-the-consoles-hexagonal-import-boundaries) |
| Console | Background analysis jobs: a database queue and one in-process loop per console | [2026-10-31](decisions.md#2026-10-31-background-analysis-jobs-a-supabase-table-and-one-in-process-loop-per-console) |
| Console | Dashboard chart requests are read by the agent's own LLM and drawn by the stock chart generator | [2026-10-31](decisions.md#2026-10-31-dashboard-chart-requests-a-model-picks-an-allowlisted-spec-our-code-fetches-the-figures-and-vss-chart_generator-draws-them), [2026-11-02](decisions.md#2026-11-02-dashboard-chart-requests-are-read-by-the-vss-agents-own-llm-not-the-local-vlm-gateway) |
| Chat | Grounded chat answers from one report version behind a mode switch; template mode stays the default | [2026-11-02](decisions.md#2026-11-02-grounded-chat-answers-from-one-report-version-with-a-chat-model-behind-a-mode-switch-gate-m2-approved-by-the-chatbot-feature-owner) |
| Evaluation | A separate evaluation pipeline on the same contract | [2026-09-24](decisions.md#2026-09-24-separate-eval-pipeline) |
| Documentation | This page follows C4 and arc42 views in NVIDIA's blueprint style and carries no deployment values | [2026-11-03](decisions.md#2026-11-03-architecture-documentation-follows-c4-and-arc42-views-in-nvidias-blueprint-style-and-carries-no-deployment-values) |

## Quality Requirements

Scenarios in the arc42 short form: a source and stimulus, the response, and how it is measured. ISO/IEC 25010 names the qualities.

| Quality | Scenario | Measure | Realised in |
|---|---|---|---|
| Traceability (accountability) | A reviewer asks, weeks later, which model, prompt and settings produced a report, and what the model actually returned | The run's attempt record and provenance name the model, the prompt hashes, every call and its outcome; the versions graph shows every AI branch and human edit | Immutable run notes, run provenance, append-only revisions |
| Functional correctness | A model returns JSON outside the contract | The attempt is recorded as a contract failure with the raw output; no report is written; only id renumbering is ever repaired | The shared validator and policy |
| Reproducibility | The same clip is analysed by the console in gateway mode, by the agent, and by the evaluation pipeline | All three send byte-identical P1 requests, checked by the request goldens in each test suite | `contracts/golden` |
| Fault tolerance | The perception stack is down, or a caption or zoom call fails, during an analysis | The analysis completes; the stage's skip or gap reason is recorded on the run or the item; the report text is unchanged | Soft stages in the agent |
| Recoverability | The reviewer closes the tab, or the console process dies, during an analysis | The job continues in the console process; a lost console is detected by a missing heartbeat and the job is re-queued once, then failed with a reason | The analysis job queue |
| Confidentiality | A browser user inspects the client bundle and the health route | No credential or private location appears; health returns booleans; media is read through expiring signed URLs | Server-only configuration, presigning |
| Integrity (access control) | A request reaches the database with the public key | Nothing is readable or callable: row-level security with no policies and revoked grants; only the service role used by server processes reaches tables and functions | The baseline migration and the security check |
| Time behaviour | A reviewer searches the library | One embedding call and one vector query; no critic call unless the critic is enabled | The search function and the HNSW index |
| Capacity under rate limits | The hosted endpoint rate-limits callers | One analysis per console at a time; caption calls bounded in flight with retries; object-tracking detector calls budgeted; tracking time-capped | The job loop and the stage budgets |
| Modifiability | A storage, model or detector provider changes | One adapter (console) or one interface implementation (agent) changes; domain and application code import no infrastructure, enforced by a test | The composition root, the import-boundary test, the agent's interfaces |
| Testability | A change touches a database function | The SQL harness applies every migration on a throwaway Postgres and runs the security check without network | `supabase/tests` |
| Operability | A developer starts the system for the day | One command preflights the host, heals missing services, opens the tunnel and runs the console; leaving it stops only the laptop side | The launcher and lifecycle scripts |
| Co-existence with upstream | Upstream VSS changes | Team code is confined to the profile directory and `incident_*` modules; stock files are touched only to register or configure | The boundary table above |

## Risks and Technical Debt

Open items are tracked in [`status.md`](status.md#4-known-issues) with stable numbers; the architectural ones:

- **The analysis queue is shared** by every console pointed at the same database, so a job may run on another person's console and code version; the opt-out is a per-console switch and jobs carry no owner yet.
- **The contract still carries the timeline** the console no longer shows; removing it means a contract version bump across the agent, the console and the evaluation fixtures.
- **Migration history is out of step with the folder:** two files share a version prefix and one was applied by hand, so a push could re-run them.
- **A pending contract revision** on another branch adds an optional field that is not strict-mode compatible; merging it needs a schema decision and a separate evaluation run.
- **Frame sampling by the hosted VLMs is described two ways** in the docs; it is model behaviour the code cannot settle.
- **Several agent fixes await their live check** on the host (the tracking time base, the screen-person rule, set-of-marks prompting, the reviewer box tool against the shared agent).
- **The agent has no authentication;** its safety rests on the loopback bind and the tunnel, and on the host staying inside a trusted network, as NVIDIA assumes for the blueprint.
- **One GPU on a shared host** bounds throughput: a clip of several minutes can exceed a model's timeout, and the perception stack holds GPU memory while it is up.
- **Hosted endpoints are outside the team's control:** rate limits, sparse sampling and endpoint changes reach the product directly.

## Glossary

| Term | Meaning here |
|---|---|
| Analysis mode | How the console gets a report: **agent** mode calls the VSS Agent, which also locates, tracks and persists; **gateway** mode calls `vlm-gateway` and the console persists. The launcher's vm mode is agent mode, its mock mode is gateway mode |
| Attempt record | The immutable JSON the console writes on a model run after each analysis attempt: request, raw response, validation, repair, outcome and provenance |
| Caption pass | An optional first pass that captions overlapping windows of a long clip and hands the captions to P1 as context |
| Critic | An optional VLM judgement of each search hit against the query; dormant by default, so hits are "unverified" |
| Developer profile | NVIDIA's term for a Compose deployment that assembles VSS microservices for one workflow; this profile reuses the search profile's Compose tag and adds its own services |
| Evidence item, evidence entry | An entity, instrument or asset of a report; its entry holds the crop, the frame, the box, the track and any gap reasons |
| Incident | In this profile, the event one uploaded clip documents (one video is one incident). In stock VSS analytics, a behaviour-analytics violation that persisted; the stock `incidents` tool refers to those |
| Incident contract | The extraction prompt, the JSON Schema and the request settings a model must satisfy; versioned (`incident-contract-v4` today) |
| Locate step, grounding | The detector call that finds an item's bounding box in the frame at its second |
| NAT | NVIDIA's NeMo Agent Toolkit, the framework the VSS Agent runs on |
| NIM | NVIDIA Inference Microservice, a model-serving container; none runs in this profile |
| Object track, person track | A list of timed boxes for one item: people from the perception stack, instruments and assets from the object tracker, both stored in one shape |
| Official revision, searchable revision | The revision a reviewer marked as the video's report; the official revision, or else the newest, is the one search indexes |
| P1, RP1 | The extraction call that returns the structured report, and the prose-report call that follows it (skipped by the console) |
| Perception stack | The stock VSS search-profile containers that turn a clip into per-frame person boxes with track ids |
| Phoenix | The trace viewer the agent's model calls are sent to |
| PostgREST, service role | Supabase's REST layer over Postgres, and the privileged role server processes use through it |
| Revision, branch | One stored report version (AI or human); each AI revision starts a new branch that human edits hang off |
| Set-of-marks | Numbered person boxes drawn on the clip P1 sees, so P1 can name which track each person is |
| VIOS, VST | NVIDIA's Video IO & Storage service of the stock blueprint; not started by this profile |
| VLM, LLM | A vision-language model (reads video and images) and a text-only language model |
| VSS Agent | NVIDIA's agent service from the blueprint, extended here with the incident routes and tool |
| Zoomed second look | Two blind VLM looks at a located item and a merge that may rename it with a recorded confidence |

## Conventions

- **Structure:** arc42's section order; C4's context, container, component and deployment views; a representative runtime view; decisions recorded in [`decisions.md`](decisions.md) and listed here.
- **Diagrams:** Mermaid flowcharts and sequence diagrams, which GitHub renders; each has a caption with its type and scope, and a key when a shape or style means something; every arrow is labelled in its direction.
- **Naming:** full names with the abbreviation in parentheses on first use; NVIDIA's names for stock components (VSS Agent, Video IO & Storage, developer profile, microservice).
- **Scope:** explanation only. Page behaviour and routes are in [`console-reference.md`](console-reference.md), contracts in [`vm-api-contracts.md`](vm-api-contracts.md), schema in [`data.md`](data.md), procedures in the skills and the runbook. No ports, hosts, addresses, user or machine names, GPU indices, paths, bucket names, private URLs, secrets or environment-file references appear here; when one is needed, link to where it lives.
- **Maintenance:** update this page in the same change as any topology, component or sequence change ([docs rule](../AGENTS.md#docs-rule)); record a new or reversed decision in [`decisions.md`](decisions.md) and adjust the decisions table.
