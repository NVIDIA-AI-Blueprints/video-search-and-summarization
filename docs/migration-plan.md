# Migration Plan (from NVIDIA VSS blueprint)

The original repository was the NVIDIA VSS blueprint (Kafka/Redis messaging,
DeepStream perception, VIOS surveillance ingest, NeMo Agent Toolkit, Helm
charts). Legacy code is preserved under `legacy/` as a donor reference; it is
not built or deployed.

## Disposition summary

| Legacy area | Action | Salvaged into |
|---|---|---|
| `services/vios` (VST platform, 21k files) | DELETE | nothing (surveillance-only) |
| `services/rtvi` (rt-cv / rt-embed / rt-vlm) | DELETE | contracts → vision/embeddings workers |
| `services/analytics` | DELETE | incident query semantics only |
| `services/alert` | DELETE | persistence factory pattern |
| `services/video-summarization` | DELETE after harvest | `ChunkInfo` model, FastAPI patterns, pydantic schemas |
| `services/agent` (nvidia-nat) | REWRITE | fusion/RRF search logic, prompts → new agent tools |
| `services/ui` monorepo | REWRITE on App Router | chat components, chunked upload, search list/parser |
| `deploy/helm`, compose blueprints | DELETE | replaced by Terraform |
| Kafka/Redis/ELK/Mosquitto infra | DELETE | EventBridge + Step Functions |

## What to port from legacy (checklist)

- [ ] RRF + weighted-linear fusion and query decomposition from
      `legacy/agent/src/vss_agents/tools/search.py` into agent tools.
- [ ] Chat message components from `legacy/ui/packages/nemo-agent-toolkit-ui/components/Chat`.
- [ ] Chunked upload flow from `legacy/ui/packages/nv-metropolis-bp-vss-ui/video-management/lib-src/chunkedUpload.ts`.
- [ ] Agent response parsing/citation formatting from
      `legacy/ui/packages/nv-metropolis-bp-vss-ui/search/lib-src/utils`.
- [ ] Chunk windowing heuristics informed by
      `legacy/video-summarization/src/chunk_info.py`.

## Removal phases

1. **Phase 0** — snapshot: commit the staged NVIDIA-infrastructure purge;
   tag `vss-legacy`.
2. **Phase 1** — scaffold target tree (this change); move untouched legacy to
   `legacy/`; prune dead CI workflows.
3. **Phase 2+** — build UI/API/agent/workers against the new architecture,
   then delete `legacy/` entirely once ports are complete.

## Known risks

- Retrieval index: brute-force cosine over S3 embeddings is fine for small
  libraries; plan OpenSearch Serverless or pgvector before scale-out.
- nvidia-nat coupling means the old agent's eval/tracing plumbing is not
  portable; budget for a real rewrite rather than a mechanical port.
- Timestamp normalization across Transcribe offsets, ffmpeg pts, and display
  timecodes — standardize on integer milliseconds everywhere.
