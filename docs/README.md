# Docs

- [Architecture](./architecture.md) — system overview, service map, pipeline contract
- [Migration plan](./migration-plan.md) — disposition of legacy NVIDIA VSS code, ports checklist, risks

## Quick start (local)

```bash
# API
cd services/api && pip install -r requirements.txt
DYNAMODB_ENDPOINT_URL=http://localhost:8000 uvicorn app.main:app --reload

# Agent
cd services/agent && pip install -r requirements.txt
AWS_REGION=us-east-1 uvicorn app.server:app --port 8100 --reload

# UI
cd services/ui && npm install && npm run dev
```

Infrastructure: see `infrastructure/README.md`.
