# VLM Gateway

A thin proxy that keeps the Brev inference key on the server, so `incident-console-v2` can call the hosted VLMs in mock (gateway) mode without exposing it.

## Run it

`./start.sh --mode mock` starts it for you. To run it alone:

```bash
cd deploy/docker/developer-profiles/dev-profile-incident/vlm-gateway
uv sync
uv run --env-file .env.local uvicorn app:app --host 127.0.0.1 --port 8600
```

`.env.local` is a symlink to the profile's shared `../.env.local`, which must set `VLM_GATEWAY_API_KEY`. Check it with `curl http://127.0.0.1:8600/health`.

## Configuration

- `POST /v1/chat/completions`: reads the caller's JSON body and forwards it unchanged to the configured endpoint with the server-side key; model-agnostic, no streaming. `GET /health` answers `{"status": "ok"}`.
- `VLM_GATEWAY_API_KEY`: the hosted inference key. Required: without it the route answers an error naming the variable.
- `VLM_GATEWAY_BASE_URL`: the chat-completions endpoint the gateway forwards to; the default in `app.py` is the hosted endpoint the profile uses.
- `VLM_GATEWAY_TIMEOUT_SECONDS`: the upstream timeout, default 330 s, longer than the slowest per-model timeout in [`../contracts/inference.json`](../contracts/inference.json).
- Used in mock (gateway) mode only: in vm mode the console never calls the gateway, and the agent reads dashboard chart requests with its own LLM. A Compose service for the gateway exists in [`compose.yml`](compose.yml), but `start.sh` never starts it.

Its place in the system: [`../.docs/architecture.md`](../.docs/architecture.md#level-1-containers).
