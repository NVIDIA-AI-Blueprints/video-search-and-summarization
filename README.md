# Archive Video Search & Summarization

An AI video analysis application. Upload videos, let an AWS pipeline
transcribe and visually analyze them, then ask questions and get answers
cited to transcript timestamps.

```
Upload ──► S3 ──► EventBridge ──► Step Functions
                                    ├─ Amazon Transcribe   (speech → text)
                                    ├─ Vision worker       (Bedrock multimodal)
                                    ├─ Chunking worker     (merged context chunks)
                                    └─ Embedding worker    (Titan vectors)
                                              │
                              DynamoDB ◄──────┘ (metadata, status, audit)

Next.js UI ──► FastAPI API ──► LangChain agent (Bedrock)
     │                             ▲
     └── player · transcript · chat with timestamp citations
```

## Repository layout

```
services/
  ui/            Next.js 15 (App Router) — library, upload, player,
                 transcript viewer, chat with citations
  api/           FastAPI — auth boundary (Cognito), video CRUD,
                 presigned uploads, processing status, agent proxy
  agent/         LangChain/LangGraph on Bedrock — tools:
                 search_transcript, search_visual_events,
                 retrieve_context, get_timestamp_reference
  workers/       Pipeline stages: transcription, vision, chunking,
                 embeddings

infrastructure/
  docker/        Local dev compose: ui, api, agent, dynamodb-local
  terraform/     S3, DynamoDB, EventBridge, Step Functions, Cognito, IAM
  scripts/       Bootstrap/deploy/local-table helpers

docs/            architecture.md · api.md · development.md
legacy/          Frozen NVIDIA-era agent code kept only as a porting
                 reference for retrieval logic (not built or deployed)
```

## Quick start

```bash
cd infrastructure/docker
docker compose up --build

# one time: create local DynamoDB tables
../scripts/create_local_tables.sh
```

| Service | URL |
|---|---|
| UI | http://localhost:3000 |
| API | http://localhost:8000 (`/health`) |
| Agent | http://localhost:8100 (`/health`) |

See [docs/development.md](./docs/development.md) for environment variables,
running without Docker, and test commands.
See [docs/architecture.md](./docs/architecture.md) for the system design and
[docs/api.md](./docs/api.md) for the HTTP contract.

## Status

- Working locally end-to-end: UI ↔ API ↔ DynamoDB Local; agent health + tool layer in place
- Pipeline workers implemented against the Step Functions contract; Lambda packaging pending
- Cognito JWT validation implemented behind `ENVIRONMENT=local` bypass; untested against a real pool
