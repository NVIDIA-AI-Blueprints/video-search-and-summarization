# Local Development

## Docker compose (recommended)

`infrastructure/docker/docker-compose.yml` runs the full local stack:
**ui, api, agent, dynamodb-local** — no Kubernetes, GPU, NIM, Kafka, Redis,
or Elasticsearch.

```bash
cd infrastructure/docker
docker compose up --build

# create local DynamoDB tables (one time; requires AWS CLI)
../../infrastructure/scripts/create_local_tables.sh
```

| Service | URL | Notes |
|---|---|---|
| UI | http://localhost:3000 | talks to API at `http://localhost:8000` |
| API | http://localhost:8000 | `/health`, `/videos`, `/videos/{id}/chat` |
| Agent | http://localhost:8100 | `/health`, `/invocations` |
| DynamoDB Local | http://localhost:8001 (host) | in-memory, `-sharedDb` |

In `local` mode the API accepts unauthenticated requests as a synthetic dev
user. Set `COGNITO_USER_POOL_ID` / `COGNITO_APP_CLIENT_ID` and run with
`ENVIRONMENT=dev` to enforce Cognito JWT validation.

Bedrock calls from the agent require real AWS credentials:

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
```

## Environment variables

### services/api (`app/core/config.py`)

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `dev` | `local` enables anonymous dev user |
| `AWS_REGION` | `us-east-1` | AWS region for all clients |
| `MEDIA_BUCKET` / `ARTIFACTS_BUCKET` | `ava-*-dev` | S3 bucket names |
| `VIDEOS_TABLE` / `USERS_TABLE` / `AUDIT_TABLE` | `ava-*` | DynamoDB tables |
| `DYNAMODB_ENDPOINT_URL` | unset | point at dynamodb-local |
| `COGNITO_REGION` / `COGNITO_USER_POOL_ID` / `COGNITO_APP_CLIENT_ID` | empty | JWT validation |
| `AGENT_SERVICE_URL` | `http://localhost:8100` | agent service |
| `ALLOWED_ORIGINS` | localhost:3000 | CORS |

### services/agent (`app/config.py`)

| Variable | Default | Purpose |
|---|---|---|
| `ARTIFACTS_BUCKET` | `ava-artifacts-dev` | transcripts/events/chunks/embeddings |
| `BEDROCK_MODEL_ID` | claude-3-5-haiku | reasoning model |
| `BEDROCK_EMBEDDING_MODEL_ID` | titan-embed-text-v2 | query embeddings |
| `MAX_SEGMENTS_PER_SEARCH` | 8 | tool result cap |

## Running without Docker

```bash
# API
cd services/api && pip install -r requirements.txt
uvicorn app.main:app --reload

# Agent (needs AWS creds for Bedrock)
cd services/agent && pip install -r requirements.txt
AWS_REGION=us-east-1 uvicorn app.server:app --port 8100 --reload

# UI
cd services/ui && npm install && npm run dev
```

## Testing & checks

```bash
# Backend tests
cd services/api   && python -m pytest tests/ -q
cd services/agent && python -m pytest tests/ -q

# Worker tests (pure logic, no AWS calls)
cd services/workers && python -m pytest chunking/test_handler.py -q

# Frontend typecheck + tests + build
cd services/ui && npm run typecheck && npm test && npm run build

# Lint (python)
ruff check services/api/app services/agent/app services/workers
```
