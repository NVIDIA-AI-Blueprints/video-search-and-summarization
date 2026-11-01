# Incident Search & Reporting Capstone - Action Plan

Goals, scope and standing decisions for the Incident Search and Reporting profile (`dev-profile-incident`), a capstone on NVIDIA's Video Search and Summarization (VSS) Blueprint sponsored by NVIDIA NVAITC. Live status: [`status.md`](status.md).

---

## 1. Goals

An intelligent, video-driven incident search and reporting system for physical security, warehouse operations and anomaly footage. It replaces manual video inspection with VLM analysis:

1. **Ingest** surveillance and anomaly clips and keep canonical copies in Cloudflare R2.
2. **Extract** structured, timestamped incident reports: type, severity, confidence, summary, entities, instruments, assets.
3. **Review** AI findings in a web console (edit into new versions, choose an official report per video).
4. **Benchmark** extraction quality against human ground truth.
5. **Search** the archive in natural language over evidence crops and embeddings (MVP2).

## 2. Scope

### Incident reporting (delivered)

- **Ingestion:** the console converts every upload to an H.264 MP4 and stores it straight in Cloudflare R2 (no VST); playback links are signed on demand.
- **Analysis:** the credential-holding `vlm-gateway` or the native `vss-agent`, on `incident-contract-v4`, with any of three allowlisted VLMs, strict validation and ID-only recovery; every attempt is recorded ([`analysis-schema.md`](analysis-schema.md)).
- **Review UI (`incident-console-v2`):** upload, versioned reports with reviewer edits saved as new revisions, run history and re-analysis, an explicit official revision per video, and a dashboard that counts each video once through its official revision, else its newest, split into official and not yet official ([`architecture.md`](architecture.md#report-versions-view-edit-and-history)).
- **Beyond the core flow:** person and object tracking with a presence bar and clips, a privacy blur for faces, chunked captioning before the first pass, background analysis jobs, dashboard charts and report chat. State and flags: [`status.md`](status.md).
- **Evaluation:** the standalone P1/RP1 benchmark in `eval/` ([`mvp2/eval.md`](mvp2/eval.md) §1.1). The console has no ground-truth screen.

### MVP2: natural-language incident search

Delivered: evidence crops at VLM item timestamps, pgvector search over them (one shared search for incidents and evidence, optionally scoped to one video; the VLM critic is dormant by default), `/library` search and the report chat. Specification and architecture: [`mvp2/README.md`](mvp2/README.md); state: [`status.md`](status.md#2-mvp2-natural-language-evidence-search). The multi-subagent routing config and full RT-CV / RT-Embed indexing are dropped ([decision](decisions.md#2026-10-24-drop-multi-subagent-routing-and-full-rt-cv--rt-embed-indexing-from-mvp2)).

## 3. Standing architectural decisions

1. **Profile tag:** reuses NVIDIA's `bp_developer_search` compose tag (`BP_PROFILE=bp_developer_search` in `.env`), so the stack is a superset of the base and search pipelines.
2. **1 video = 1 incident:** `videos.id` is the incident id; every `report_revisions` row names its `video_id` ([`data.md`](data.md#the-report-model)).
3. **Stateless users:** reviewer names (`report_revisions.created_by`) are plain text; there is no account table.

The other standing rules (analysis modes, storage, append-only revisions, PostgREST from the VM, GPU 0 only) are in the profile [`AGENTS.md`](../AGENTS.md#architecture-rules), [`decisions.md`](decisions.md) and the [GPU rule](incident-profile-operations.md#5-vm-deployment-configuration-principles-generatedenvremote).
