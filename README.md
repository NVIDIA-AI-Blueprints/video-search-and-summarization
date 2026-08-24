# Archive Video Search & Summarization

Upload archived videos, get automatic transcripts and visual analysis, then
ask questions with answers cited to transcript timestamps. Built on AWS
managed services (S3, DynamoDB, EventBridge, Step Functions, Amazon
Transcribe, Bedrock).

> The original codebase was an NVIDIA VSS blueprint; legacy sources are kept
> under [`legacy/`](./legacy) for reference only and are not built or deployed.
> See [docs/migration-plan.md](./docs/migration-plan.md).

## Repository layout

```
services/
  ui/            Next.js 15 app — library, upload, player, transcript, chat w/ citations
  api/           FastAPI — auth boundary (Cognito), video CRUD, status, agent proxy
  agent/         LangGraph + Bedrock agent with composable tools
  workers/       Pipeline stages: transcription, vision, chunking, embeddings

infrastructure/
  terraform/     S3, DynamoDB, EventBridge, Step Functions, Cognito, IAM
  scripts/       bootstrap/deploy helpers

docs/            Architecture + migration docs
legacy/          Frozen NVIDIA-era services (reference only)
```

## How it works

1. **Upload** — the API issues a presigned S3 PUT; the browser uploads directly.
2. **Process** — S3 `ObjectCreated` triggers EventBridge → Step Functions:
   Transcribe → Vision (Bedrock multimodal) → Chunking → Embeddings,
   with status written to DynamoDB at every hop.
3. **Ask** — the agent answers questions using `search_transcript`,
   `search_visual_events`, `retrieve_context`, and `get_timestamp` tools;
   every answer carries citations the UI links to the player timeline.

See [docs/architecture.md](./docs/architecture.md) for details.

## Local development

```bash
# UI
cd services/ui && npm install && npm run dev        # http://localhost:3000

# API
cd services/api && pip install -r requirements.txt
uvicorn app.main:app --reload                        # http://localhost:8000

# Agent
cd services/agent && pip install -r requirements.txt
AWS_REGION=us-east-1 uvicorn app.server:app --port 8100 --reload
```

Environment variables are documented in each service (`app/core/config.py`
for the API, `app/config.py` for the agent). Infrastructure provisioning is
in `infrastructure/`.

## Status

Scaffold phase. Worker Lambda packaging, the Terraform workers module, and
Cognito wiring are tracked in `infrastructure/README.md`.
