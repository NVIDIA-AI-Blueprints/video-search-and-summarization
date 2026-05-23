# Deployment Reference: ELK

## Container Image

ELK is composed of three main service containers plus a one-shot init container. All four are defined in `deploy/docker/services/infra/compose.yml` and all run with `network_mode: host`.

| Container | Image | Tag pattern | Build context | Registry |
|---|---|---|---|---|
| `elasticsearch` (`mdx-elastic`) | Built locally from `Dockerfiles/elasticsearch.Dockerfile`; image tag `elasticsearch` | tracks the base image pinned in the Dockerfile (Elasticsearch 9.x) | `$MDX_SAMPLE_APPS_DIR/foundational` | local build (no registry pull at compose-up time) |
| `elasticsearch-init-container` (`mdx-elasticsearch-init`) | Built locally from `Dockerfiles/elastic-init.Dockerfile` | tracks the base image in the Dockerfile | `$MDX_SAMPLE_APPS_DIR/foundational` | local build |
| `kibana` (`mdx-kibana`) | `docker.elastic.co/kibana/kibana:9.3.0` | exact tag — pinned in compose | n/a | `docker.elastic.co` (public) |
| `logstash` (`mdx-logstash`) | `docker.elastic.co/logstash/logstash:9.3.0` | exact tag — pinned in compose | n/a | `docker.elastic.co` (public) |

**NGC required?** No — all images are built locally from public bases or pulled from `docker.elastic.co`.

**Architecture support:** x86_64 confirmed on the foundational stack. aarch64 is supported by upstream Elasticsearch / Kibana / Logstash images but verify on the local Dockerfile bases.

**GPU-aware Elasticsearch image** (`Dockerfiles/elasticsearch-gpu.Dockerfile`) exists but is not used by the default foundational compose path. The default path uses the CPU-only `elasticsearch.Dockerfile`.

## GPU Requirements

**GPU required:** No. ELK is CPU/RAM bound. None of the four containers reserve GPU devices.

**Minimum VRAM:** Not applicable.

**Supported architectures:** Not applicable — no GPU dependency.

**Can share GPU with other services:** Not applicable.

**Compose snippet for device reservation:** None.

## CPU & Memory

Tuned via heap settings; values in the foundational compose are conservative.

| Container | CPU | RAM (heap + overhead) | Tuning |
|---|---|---|---|
| `elasticsearch` | 2+ cores | `ES_JAVA_OPTS="-Xmx1024m -Xms256m"` (~2 GB resident) | Increase `-Xmx` for larger indices; rule of thumb ≤ 50% of host RAM and ≤ 31 GB |
| `kibana` | 1 core | ~1 GB | default Node.js heap |
| `logstash` | 1+ cores | `LS_JAVA_OPTS="-Xmx1024m -Xms256m"` (~2 GB resident) | Tune for pipeline throughput |
| `elasticsearch-init-container` | < 1 core | ~256 MB | one-shot, exits |

Total: ~5–6 GB RAM steady-state for the foundational stack at default heap; scale up for production caption volumes.

`shm_size`, `ulimits`, and `ipc` are left at compose defaults.

## Storage

ELK uses two named volumes bound to host paths under `${MDX_DATA_DIR}/data_log/elastic/` plus several read-only config bind mounts. Logstash also uses a named volume for plugin libraries.

| Mount Path (container) | Purpose | Type | Size estimate | Required permissions |
|---|---|---|---|---|
| `/usr/share/elasticsearch/config/elasticsearch.yml` | ES config | bind file (ro) | small | readable by ES uid (1000) |
| `/tmp/elastic/data` | Elasticsearch data dir | named volume `mdx-elastic-data` (bound to `${MDX_DATA_DIR}/data_log/elastic/data`) | scales with index size — GB to TB | uid 1000 must own / write; `chmod 777` on parent works |
| `/tmp/elastic/logs` | Elasticsearch logs | named volume `mdx-elastic-logs` (bound to `${MDX_DATA_DIR}/data_log/elastic/logs`) | grows | uid 1000 must own / write |
| `/usr/share/kibana/config/kibana.yml` | Kibana config | bind file (ro) | small | readable by Kibana |
| `/usr/share/logstash/config/logstash.yml` | Logstash config | bind file | small | readable by Logstash |
| `/usr/share/logstash/pipeline/logstash.conf` | Logstash pipeline (selected by `STREAM_TYPE`) | bind file | small | readable by Logstash |
| `/opt/logstash-data-libs/logstash/pb_definitions/{ext,schema}_pb.rb` | Protobuf decoder definitions | bind files | small | readable by Logstash |
| `/opt/logstash-data-libs/logstash/pb_definitions/descriptors/{ext,schema}.desc` | Protobuf descriptors | bind files | small | readable by Logstash |
| `/opt/logstash-data-libs` | Plugin library cache (persists installed plugins between restarts) | named volume `mdx-logstash-libs` | < 100 MB | n/a (named) |
| `/usr/share/logstash/gems/logstash-redis-stream-input-java.gem` | Bundled Redis-stream input plugin gem | bind file | small | readable by Logstash |

**Volume retention:**
- `docker compose down` keeps `mdx-elastic-data`, `mdx-elastic-logs`, `mdx-logstash-libs`. Restart preserves index data and installed Logstash plugins.
- `docker compose down -v` wipes the named volumes. Elasticsearch index data is destroyed; Logstash re-installs its plugin on next boot.
- Bind mounts under `${MDX_DATA_DIR}/data_log/elastic/{data,logs}` are addressed by the named-volume `device:` directive — the named volume is essentially a renamed bind. `down -v` removes the volume reference but does not `rm -rf` the host path; the host data on disk remains until manually deleted.

**Required host-path setup:**

```bash
mkdir -p \
  "${MDX_DATA_DIR}/data_log/elastic/data" \
  "${MDX_DATA_DIR}/data_log/elastic/logs"
chmod -R 777 "${MDX_DATA_DIR}/data_log/elastic"
```

`chmod 777` on parents matches the `dev-profile.sh` reference pattern. Do NOT recursive-chown to a user — Elasticsearch boots as uid 1000 and a later user-chown breaks startup.

## Startup Behavior

- **Expected startup time:**
  - Cold (first boot, image build needed): 3–5 minutes for Elasticsearch + Logstash + Kibana, plus Logstash's first-boot plugin install (~1–2 min).
  - Warm: 30–60 seconds for the full stack.
- **Startup ordering dependencies:**
  - `elasticsearch-init-container` `depends_on: [elasticsearch]` — runs after ES is up, but before Logstash is fully wired.
  - `kibana` `depends_on: elasticsearch (service_healthy)`.
  - `logstash` `depends_on: broker-health-check (service_completed_successfully) AND elasticsearch-init-container (service_completed_successfully)`. Logstash will not start consuming until both conditions are met.
- **Health check endpoints / tuning:**
  - `elasticsearch`: `curl -f http://localhost:9200/_cluster/health` — `interval 10s`, `timeout 10s`, `retries 15`, `start_period 30s`.
  - `kibana`: `curl -f http://localhost:5601/api/status` — `interval 10s`, `timeout 10s`, `retries 30`, `start_period 60s`.
  - `logstash`: no compose-level healthcheck; verify via Elasticsearch index counts and Logstash logs.
- **Healthy log signatures:**
  - Elasticsearch: `started` line in `/tmp/elastic/logs` and `Cluster health status changed from [YELLOW] to [GREEN]` for single-node, or `... to [YELLOW]` (acceptable in single-node mode where there is no replica peer).
  - Kibana: `Server running at http://0.0.0.0:5601`.
  - Logstash: `Pipeline started successfully` after the initial `Installing plugin` and `Successfully installed` lines.
  - Init container: `[i] ILM policies created`, `[i] Index templates created`, `[i] Ingest pipelines created` then exits 0.

## Known Deployment Issues

| Symptom | Root cause | Fix |
|---|---|---|
| Elasticsearch `AccessDeniedException` on startup | `${MDX_DATA_DIR}/data_log/elastic/{data,logs}` owned by root, ES runs as uid 1000 | `chmod -R 777 "${MDX_DATA_DIR}/data_log/elastic"` (matches `dev-profile.sh` reference) |
| Logstash exits before pipeline starts | `STREAM_TYPE` unset → command branch evaluates neither `kafka` nor `redis` plugin install path | Set `STREAM_TYPE=kafka` (IN-1) or `STREAM_TYPE=redis` |
| Logstash starts but no documents land in Elasticsearch | `broker-health-check` exited unsuccessfully — Kafka topics not created or unreachable | Check `docker logs mdx-broker-health-check`. Confirm `kafka-topic-init-container` ran successfully and Kafka is healthy. |
| Logstash decode errors on `vision-llm-messages` topic | Protobuf descriptor mismatch — RT-VLM's published schema and `schema.desc` / `ext.desc` are out of sync | Re-generate or update the descriptors in `deploy/docker/services/infra/elk/logstash/pb_definitions/descriptors/` to match the RT-VLM build |
| Kibana shows red status / "Kibana server is not ready yet" | Elasticsearch not healthy yet, or version mismatch (Kibana 9.3.0 needs ES 9.x) | Wait past `start_period: 60s`. If persistent, confirm ES image is on a 9.x base. |
| ES init container fails at `elasticsearch-template-creation.sh` | ILM policy step ran before ES was responsive (rare) | Increase `ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS` (default 20) and re-run the init container: `docker compose up -d --force-recreate elasticsearch-init-container` |
| Logstash plugin install hangs | Outbound to RubyGems blocked by host firewall / proxy | Pre-bake the `logstash-codec-protobuf` plugin into a custom Logstash image, or supply the gem via an additional bind mount |
| `docker compose down -v` followed by `up -d` produces empty Elasticsearch | Index data was in `mdx-elastic-data` named volume; wiped by `down -v` | If retention matters, never use `down -v` without backing up `${MDX_DATA_DIR}/data_log/elastic/data`. The on-disk path lingers but the volume reference is recreated on next `up`. |
| Kibana dashboards missing after profile-specific init never ran | Profile-specific `kibana-init-container-{lvs,search,alerts}` not gated for the active compose profile | Add the active compose profile to the init container's `profiles:` list, or import dashboards manually via `POST /api/saved_objects/_import` |

## Prerequisites

- **Docker Engine 28.2+** with Compose plugin **2.36+**. The foundational compose itself is straightforward, but adjacent rt-vlm / vios composes require 2.36 — keep one minimum across the stack.
- **No NVIDIA Driver / Container Toolkit dependency** for ELK itself. (Other containers in the same compose project — RT-VLM, sensor-ms — do require them, but ELK can run on a CPU-only host.)
- **Free TCP ports:** `9200` (Elasticsearch), `5601` (Kibana). All `network_mode: host`, so any host-side process binding these ports prevents the stack from coming up.
- **Outbound network:**
  - `docker.elastic.co` for Kibana / Logstash image pulls (or pre-pull behind a corporate registry mirror).
  - RubyGems for Logstash plugin install on first boot (Kafka mode pulls `logstash-codec-protobuf`).
- **Disk:** depends on retention. Plan for at least 50 GB on `${MDX_DATA_DIR}/data_log/elastic/data` for steady-state IN-1 workloads at moderate caption volume; scale linearly with stream count and `ELASTICSEARCH_ILM_MIN_AGE`.
- **`MDX_SAMPLE_APPS_DIR` and `MDX_DATA_DIR`** set to the canonical absolute paths used by the rest of the foundational stack (Kafka, Redis, etc. share the `mdx-*` named volumes by convention).

## Verify Deployment

```bash
# Elasticsearch reachable, single-node yellow/green
curl -fsS http://${HOST_IP}:9200/_cluster/health | jq

# Kibana ready
curl -fsS http://${HOST_IP}:5601/api/status | jq '.status.overall'

# ES indices created by the init container (look for ILM policies + templates)
curl -fsS http://${HOST_IP}:9200/_ilm/policy | jq 'keys'
curl -fsS http://${HOST_IP}:9200/_index_template | jq '.index_templates | map(.name)'

# Logstash actively consuming (count grows over time as RT-VLM publishes captions)
curl -fsS http://${HOST_IP}:9200/_cat/indices?v
```

For IN-1 end-to-end verification: after RT-VLM streams captions, query the relevant ES index for documents and confirm caption text appears with the expected timestamp / source-stream metadata.

## Tear Down

```bash
# Graceful — preserves index data and Logstash plugins
docker compose -f resolved.yml stop kibana logstash elasticsearch elasticsearch-init-container
docker compose -f resolved.yml rm -f kibana logstash elasticsearch elasticsearch-init-container

# Full stack teardown — same effect via the foundational compose project
docker compose -f resolved.yml down --remove-orphans

# Wipes named volumes — Elasticsearch index data destroyed, Logstash re-installs plugin
docker compose -f resolved.yml down -v

# Wipe on-disk Elasticsearch data (host paths persist past `down -v`)
# rm -rf "${MDX_DATA_DIR}/data_log/elastic"
```

## References

- Foundational compose: `deploy/docker/services/infra/compose.yml`
- ELK config files: `deploy/docker/services/infra/elk/logstash/configs/`
- Init scripts: `deploy/docker/services/infra/elk/elasticsearch/init-scripts/`
- Protobuf definitions: `deploy/docker/services/infra/elk/logstash/pb_definitions/`
- Per-profile Kibana dashboards: `deploy/docker/developer-profiles/dev-profile-{lvs,search,alerts}/kibana-dashboard/`
- Elasticsearch reference: <https://www.elastic.co/guide/en/elasticsearch/reference/9.x/>
- Kibana reference: <https://www.elastic.co/guide/en/kibana/9.x/>
- Logstash reference: <https://www.elastic.co/guide/en/logstash/9.x/>
