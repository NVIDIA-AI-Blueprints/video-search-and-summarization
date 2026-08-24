# Architecture

## Overview

Archive video search & summarization on AWS managed services. Users upload
videos, an asynchronous pipeline transcribes and visually analyzes them, and a
Bedrock-backed agent answers questions with timestamped citations.

```
Next.js UI ──► FastAPI API ──► LangGraph agent (Bedrock)
                    │                 ▲
                    │ presigned PUT   │ reads artifacts
                    ▼                 │
                  S3 (media)          │
                    │ ObjectCreated   │
                    ▼                 │
                EventBridge           │
                    │                 │
                    ▼                 │
              Step Functions ──► Transcribe ─► Vision worker ─► Chunking ─► Embeddings
                    │                                                  │
                    └────────────► DynamoDB status/artifacts ◄──────────┘

Stores:
  S3 media bucket      original uploads
  S3 artifacts bucket  transcripts, visual events, chunks, embeddings
  DynamoDB             users, video metadata + processing state, audit events
```

## Services

| Path | Stack | Responsibility |
|---|---|---|
| `services/ui` | Next.js 15 (App Router), React, TypeScript | Library, upload, player, transcript viewer, chat with citations |
| `services/api` | FastAPI | Auth boundary (Cognito JWT), video CRUD, presigned uploads, status, agent proxy |
| `services/agent` | LangChain/LangGraph + Bedrock | Q&A over video content via composable tools |
| `services/workers` | Python Lambdas (containers) | Pipeline stages: transcription, vision, chunking, embeddings |
| `infrastructure/terraform` | Terraform | S3, DynamoDB, EventBridge, Step Functions, Cognito, IAM |

## Agent tools

| Tool | Purpose |
|---|---|
| `search_transcript()` | Keyword/timestamped search over spoken content |
| `search_visual_events()` | Search detected objects/actions/scenes per frame range |
| `retrieve_context()` | Embedding-based semantic retrieval over merged chunks |
| `get_timestamp()` | Normalize/validate citation time ranges |

Every tool returns `{results, citations}` so answers can be linked to
transcript timestamps in the UI.

## Processing pipeline contract

Step Functions state machine: see
`infrastructure/terraform/state-machine.asl.json.tftpl`.
Each worker exposes a `lambda_handler(event)` dispatching on `event.mode` or
fixed task type; status transitions are written to DynamoDB by a shared
status-update task (`UPLOADED → TRANSCRIBING → VISION_PROCESSING → CHUNKING →
EMBEDDING → READY | FAILED`).

Artifact layout in the S3 artifacts bucket:

```
transcripts/{video_id}/transcript.json    [{start_ms,end_ms,text,speaker?}]
artifacts/{video_id}/visual_events.json   [{start_ms,end_ms,label,description,confidence}]
chunks/{video_id}/chunks.json             [{chunk_id,start_ms,end_ms,text,visual_summary}]
embeddings/{video_id}/embeddings.json     [{chunk_id,vector}]
```

## Deliberate omissions (V1)

Elasticsearch/OpenSearch, Kafka, Redis, Phoenix tracing, Kubernetes/Helm,
NVIDIA NIM/GPU containers, RTSP/surveillance ingest, MCP servers. Retrieval is
currently brute-force cosine over per-video embedding sets; swap in a vector
index when scale demands it.
