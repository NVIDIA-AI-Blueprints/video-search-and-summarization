# Ambient → NvSchema + Custom VLM Parser (Alerts profile add-on)

This guide shows how to build, deploy, and test the **Ambient adapter** and the
**pluggable VLM response parser** that ride on top of the standard
`dev-profile-alerts` stack.

It is an **add-on**, not a replacement. Deploy the normal alerts profile exactly
as documented in the existing guides, then use the extra steps here:

- **[`deploy/docker/README.md`](README.md)** → *Developer profiles (recommended path)* — the `dev-profile.sh` workflow, data-dir setup, and requirements.
- **[`deploy/docker/scripts/deploy_vss_launchable.ipynb`](scripts/deploy_vss_launchable.ipynb)** → the launchable notebook for a guided bring-up.
- **[`deploy/docker/services/alert/README.md`](services/alert/README.md)** → alert-bridge (`vlm-as-verifier`) config reference.

---

## What this add-on changes

Compared to a stock `alerts` deployment, this branch:

1. **Adds an incident source** — two containers built from the separate
   [`ambient-alert-retrieval`](#prerequisites) repo:
   - `ambient-alert-receiver` — local sink for forwarded alerts (`:8090`, internal).
   - `ambient-adapter` — converts fixture Ambient alerts into **NvSchema `nv.Incident`**
     protobuf, publishes them to the `mdx-incidents` Kafka topic, and serves the
     referenced clip at `:8091` (internal, over the compose bridge network).
2. **Disables the native generators** (`perception-alerts`, `vss-behavior-analytics-alerts`)
   so Ambient is the sole producer to `mdx-incidents`.
3. **Wires a custom parser** into alert-bridge — `parsers.tailgating_enrichment.TailgatingVerifier`
   fully replaces the built-in Cosmos-Reason verdict parsing and emits structured
   JSON (`verdict`, `description`, `severity`, `confidence`, `reasoning`) into
   `info.vlm_response`.

### Files involved

| File | Change |
|------|--------|
| `developer-profiles/dev-profile-alerts/compose.yml` | Adds `ambient-adapter` + `ambient-alert-receiver`; disables native generators |
| `developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml` | `response_parser`, direct-media path (`vst_pass_through_mode`, `media_download`), base64 media, `use_vlm_media_defaults` |
| `developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/alert_type_config.json` | Tailgating / Unauthorized Entry / Loitering prompts |
| `developer-profiles/dev-profile-alerts/vlm-as-verifier/parsers/` | `TailgatingVerifier` parser package |
| `services/alert/compose.yml` | Bind-mounts `${VLM_AS_VERIFIER_PARSERS_DIR}` → `/app/parsers` |
| `developer-profiles/dev-profile-alerts/.env` | `VLM_AS_VERIFIER_PARSERS_DIR`, `AMBIENT_REPO_DIR` |

---

## Prerequisites

Everything in [`README.md` → Requirements](README.md#requirements) (Docker, Compose v2,
NVIDIA driver + Container Toolkit, `NGC_CLI_API_KEY`), **plus**:

1. **The `ambient-alert-retrieval` repo**, cloned locally. The adapter image is built
   from it (`docker/Dockerfile.mock`), and its `mock/fixtures/` are bind-mounted at runtime.

2. **Git LFS objects fetched.** The fixture clips are stored via Git LFS. A fresh clone
   contains pointer files, not real MP4s — the adapter would serve broken clips and VLM
   verification would fail. In the ambient repo run:

```bash
git lfs install
git lfs pull
# sanity check: these should be multi-MB binaries, not ~130-byte text pointers
ls -lh mock/fixtures/videos/*.mp4
```

3. **`AMBIENT_REPO_DIR` set** to that repo's root in
   `developer-profiles/dev-profile-alerts/.env` (the committed value is a
   `/path/to/...` placeholder):

```env
AMBIENT_REPO_DIR=/absolute/path/to/ambient-alert-retrieval
```

> The custom-parser half of this branch needs none of the above — it is self-contained
> in this repo. The Ambient repo is only required to run the adapter as the incident source.

---

## Build & deploy

Bring up the alerts profile the standard way (see [`README.md`](README.md)); the only
addition is that the Ambient services build from `AMBIENT_REPO_DIR`, so make sure the
build runs.

```bash
cd /path/to/video-search-and-summarization

export NGC_CLI_API_KEY="<your-key>"

# Standard alerts bring-up (verification mode). Use the hardware profile for your GPU.
./deploy/docker/scripts/dev-profile.sh up \
  --profile alerts \
  --mode verification \
  --hardware-profile RTXPRO6000BW
```

If the `ambient-adapter` / `ambient-alert-receiver` images are not built automatically,
build them once against the generated env, then re-run `up`:

```bash
cd deploy/docker
docker compose -f compose.yml --env-file developer-profiles/dev-profile-alerts/generated.env \
  build ambient-adapter ambient-alert-receiver
```

Confirm the containers are running:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'ambient|alert-bridge'
```

You should see `ambient-adapter`, `ambient-alert-receiver`, and `vss-alert-bridge`.
`vss-rtvi-cv` (perception) and `vss-behavior-analytics` should **not** be present —
they are disabled by design.

---

## Test the pipeline

The adapter auto-sends one fixture incident every ~10s (`MOCK_AUTO_SEND_INTERVAL`), so
data flows on its own. To trigger on demand, exec into the adapter (it is on the internal
bridge network with no host port):

```bash
# Replay all bundled fixtures once
docker exec ambient-adapter curl -s -XPOST http://localhost:8091/mock/replay-fixtures \
  -H 'Content-Type: application/json' -d '{}'

# Or send N random fixtures
docker exec ambient-adapter curl -s -XPOST http://localhost:8091/mock/trigger \
  -H 'Content-Type: application/json' -d '{"count": 3}'
```

### 1. Incidents on Kafka (`mdx-incidents`)

```bash
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:29092 --topic mdx-incidents \
  --from-beginning --max-messages 1
```

### 2. Alert-bridge verification

```bash
docker logs -f vss-alert-bridge 2>&1 | grep -E 'VLM response received|Publishing to Elastic'
```

A healthy line looks like `VLM response received ... category=Tailgating` followed by
`Publishing to Elastic ... index=mdx-vlm-incidents`.

### 3. Verified incidents in Elasticsearch

```bash
docker exec elasticsearch curl -s \
  'http://localhost:9200/mdx-vlm-incidents*/_search?size=3&sort=end:desc' | python3 -m json.tool
```

Each hit's `info.vlm_response` carries the parser's structured JSON
(`verdict`, `description`, `severity`, `confidence`, `reasoning`).

### 4. VSS UI

Open the UI (port `3000`) and go to the **Alerts** tab. The alerts table is served by
`video-analytics-api` through HAProxy on `VSS_PUBLIC_PORT` (default **7777**), so both
ports must be reachable from your browser host.

> **Time-window note:** the bundled fixtures carry historical timestamps. The Alerts tab
> defaults to a rolling ~10-minute window, so widen the time window in the UI if freshly
> published incidents don't appear, or confirm directly via the Elasticsearch query above.

---

## How the custom parser is wired

`vlm-as-verifier/configs/config.yml`:

```yaml
vlm:
  response_parser: "parsers.tailgating_enrichment.TailgatingVerifier"
```

- The parser package is bind-mounted into alert-bridge at `/app/parsers` via
  `VLM_AS_VERIFIER_PARSERS_DIR` (see `services/alert/compose.yml`).
- When `response_parser` is set it **fully replaces** the built-in verdict parsing:
  its dict is JSON-serialized into `info.vlm_response` and `info.verdict` is forced to `""`.
- Alert-bridge loads the class **once** at startup and shares one instance across all
  worker threads, so `parse()` must stay stateless / thread-safe.

To adapt this to a different use case, drop a new class implementing
`parse(raw_response: str) -> dict` into the parsers directory and point
`response_parser` at it (matching the prompts in `alert_type_config.json`). See
[`services/alert/README.md`](services/alert/README.md) for the full config reference.

---

## Teardown

```bash
./deploy/docker/scripts/dev-profile.sh down
```

To also reset `data_log` volumes (Elasticsearch/Kafka/Redis), use
`scripts/cleanup_all_datalog.sh` with the same env file, as described in
[`README.md`](README.md).
