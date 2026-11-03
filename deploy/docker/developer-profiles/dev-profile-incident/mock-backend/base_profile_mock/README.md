# base_profile_mock

A zero-GPU mock of the agent API, VIOS/VST and inference in one process on port 7777, for local work on `incident-console-v2` and the stock VSS UI (`services/ui/apps/nv-metropolis-bp-vss-ui`). It is not a real agent: answers are canned or come from a fixed snapshot of finals, and most state resets on restart. Uploads and analyses do write to the real R2 bucket and finals database.

## Run it

`./start.sh --mode mock` starts it with the VLM gateway and the console. To run it alone:

```sh
cd deploy/docker/developer-profiles/dev-profile-incident/mock-backend/base_profile_mock
uv sync
uv run --env-file ../../.env.local \
  uvicorn base_profile_mock.app:create_app --factory --host 127.0.0.1 --port 7777 --reload
```

The shared `.env.local` must hold the `R2_*` and `INCIDENT_SUPABASE_*` values. `GET http://127.0.0.1:7777/health` should return `{"value": {"isAlive": true}}`. Keep `MOCK_PUBLIC_BASE_URL` on `127.0.0.1`, not `localhost` (IPv6 clients cannot reach the IPv4 bind).

## Point the real UI at it

Set these in `services/ui/apps/nv-metropolis-bp-vss-ui/.env.local` (or export them
before `npx turbo dev --filter=./apps/nv-metropolis-bp-vss-ui`):

```
NEXT_PUBLIC_AGENT_API_URL_BASE=http://localhost:7777/api/v1
NEXT_PUBLIC_VST_API_URL=http://localhost:7777/vst/api
NEXT_PUBLIC_SIDEBAR_CHAT_WEBSOCKET_CHAT_COMPLETION_URL=ws://localhost:7777/websocket
NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL=http://localhost:7777/chat/stream
NEXT_PUBLIC_SIDEBAR_CHAT_WEB_SOCKET_DEFAULT_ON=true
NEXT_PUBLIC_ENABLE_CHAT_TAB=false
NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR=true
NEXT_PUBLIC_ENABLE_SEARCH_TAB=false
NEXT_PUBLIC_ENABLE_ALERTS_TAB=false
NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB=true
NEXT_PUBLIC_SEARCH_TAB_MEDIA_WITH_OBJECTS_BBOX=true
```

Set `NEXT_PUBLIC_SEARCH_TAB_MEDIA_WITH_OBJECTS_BBOX` even though it is not backend-related: the UI serialises it on every page load and fails when it is unset.

## Refresh the finals snapshot

Search, revision search and chat answer from `src/base_profile_mock/finals_snapshot.json`, a static copy of the finals AI revisions (last captured 2026-10-25: six revisions), so newer uploads do not appear in mock search. To refresh it, read `report_revisions` where `kind = 'ai'` through PostgREST (`INCIDENT_SUPABASE_URL`, `INCIDENT_SUPABASE_SERVICE_ROLE_KEY`; a `GET` only, `select=id,video_id,revision_no,model_run_id,payload&order=id.asc`) and reshape each row into one entry of the JSON's `revisions` list:

- `revision_id`, `video_id`, `revision_no`, `model_run_id` from the row;
- `incident`: `incident_type`, `severity_level`, `title`, `description`, `start_seconds`, `end_seconds` from `payload.incident` (`type`, `severity_level`, `title`, `description`, `start_timestamp`, `end_timestamp`);
- `items`: the payload's entities, then instruments, then assets, each `{item_type, item_id, name, label, description, timestamp_seconds, threat_level, holder_entity_id}`. An entity's `name` is its `label`; an instrument's `holder_entity_id` is its `entity_id`; fields an item type lacks are `null`.

Update `source` with the capture date. A refresh changes no other field of the surviving rows, so the diff shows only added and removed revisions. `search_profile_mock` reads the same file; both mocks' `tests/test_incident_search.py` check that no video deleted from finals is served.

What the mock serves: [`../../.docs/architecture.md`](../../.docs/architecture.md#level-1-containers). Tests: `uv run pytest`.
