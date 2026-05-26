# Video Summarization Debugging Reference

Use this for video summarization-specific troubleshooting after the `lvs` profile has been
deployed or partially deployed.

## Fast Status

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  "${LVS_BACKEND_URL:-http://localhost:38111}/v1/ready"

docker ps --filter name=vss-lvs --format '{{.Names}} {{.Status}}'
docker logs --tail 100 vss-lvs
```

HTTP 200 on `/v1/ready` means ready. HTTP 503 means the service is warming or a
dependency is unavailable.

## Video Summarization Service Not Ready

Check dependencies:

```bash
curl -sf "http://${HOST_IP}:8018/v1/models" | jq '.data[].id'
curl -sf "http://${HOST_IP}:9200/_cluster/health" | jq .
docker logs --tail 100 vss-rtvi-vlm
docker logs --tail 100 vss-lvs
```

Common causes:

| Symptom | Likely cause | Fix |
|---|---|---|
| `400 BadParameters: No such model` | `VLM_NAME` does not match RT-VLM `/v1/models`. | Copy the advertised id into `VLM_NAME` and recreate `vss-lvs` / `vss-agent`. |
| `/v1/ready` returns 503 | LLM, RT-VLM, ES, or another dependency is warming/unreachable. | Check dependency logs and endpoint URLs. |
| `curl` to the video summarization service works on host but not in an agent sandbox | Network namespace or sandbox visibility differs. | Use host-visible shell/deployment context. |
| Summarize returns 503 | The video summarization service is busy processing another file. | Wait and retry. |
| HTTP 200 with empty `events` / `video_summary` even though RT-VLM processed chunks | Elasticsearch could not read back per-video event docs, often because flood-stage disk watermark marked indices read-only or left shards unavailable. | Check ES health, watermarks, index blocks, and free disk; clear read-only blocks after restoring ES health. |
| Empty or weak event output | Scenario/events too narrow or no matching content. | Re-run with broader events or scenario. |

## Model Id Mismatch

The default `lvs` profile routes VLM calls through RT-VLM. Verify:

```bash
curl -sf "http://${HOST_IP}:8018/v1/models" | jq -r '.data[].id'
```

For the default integrated Cosmos Reason 2 path, `VLM_NAME` should be:

```text
nim_nvidia_cosmos-reason2-8b_hf-1208
```

Do not use `nvidia/cosmos-reason2-8b` unless the endpoint advertises that id.

## Elasticsearch Flood-Stage Watermark / Empty Aggregation

If `/v1/summarize` returns HTTP 200 but the response has an empty `events` list or
empty `video_summary`, first confirm whether RT-VLM actually produced chunk output.
If RT-VLM processed chunks and LVS logs show Elasticsearch readback errors, the
failure is likely in the storage/aggregation path rather than VLM inference.

Common log indicators:

```text
ApiError(503, 'search_phase_execution_exception')
NoShardAvailableActionException
flood stage disk watermark ... exceeded
all indices on this node will be marked read-only
```

Check Elasticsearch health and allocation:

```bash
ES_URL="${ES_URL:-http://${HOST_IP:-localhost}:9200}"

curl -sf "$ES_URL/_cluster/health?pretty"
curl -sf "$ES_URL/_cat/allocation?v"
curl -sf "$ES_URL/_cat/indices/default_*?v"
```

For a Helm/Kubernetes profile, also check the data path seen by Elasticsearch:

```bash
NAMESPACE="${NAMESPACE:-vss-lvs}"

kubectl logs -n "$NAMESPACE" statefulset/elasticsearch --tail=200 | \
  grep -Ei 'flood stage|watermark|read-only|NoShard|search_phase_execution' || true

kubectl exec -n "$NAMESPACE" elasticsearch-0 -- \
  df -h /usr/share/elasticsearch/data
```

Elasticsearch can enter flood-stage protection when its data path has too little
free space. On hostPath-backed Kubernetes storage, the PVC size may say `10Gi`,
but Elasticsearch may still see the underlying host filesystem usage. A mostly
full host disk can therefore trigger flood-stage protection even when the ES
index data itself is small.

Preferred recovery is to free disk or give Elasticsearch more usable storage.
For a development cluster only, you can temporarily relax disk watermarks and
then clear read-only index blocks:

```bash
curl -sS -X PUT "$ES_URL/_cluster/settings" \
  -H 'Content-Type: application/json' \
  --data-binary '{
    "transient": {
      "cluster.routing.allocation.disk.watermark.low": "97%",
      "cluster.routing.allocation.disk.watermark.high": "98%",
      "cluster.routing.allocation.disk.watermark.flood_stage": "99%",
      "cluster.routing.allocation.disk.watermark.flood_stage.max_headroom": "50GB"
    }
  }'

curl -sS -X PUT "$ES_URL/_all/_settings?expand_wildcards=all" \
  -H 'Content-Type: application/json' \
  --data-binary '{"index.blocks.read_only_allow_delete": null}'
```

After recovery, rerun:

```bash
curl -sf "$ES_URL/_cluster/health?pretty"
```

After the test is complete and disk pressure is actually resolved, remove the
temporary transient watermark override unless the values are intentionally managed
for that development cluster:

```bash
curl -sS -X PUT "$ES_URL/_cluster/settings" \
  -H 'Content-Type: application/json' \
  --data-binary '{
    "transient": {
      "cluster.routing.allocation.disk.watermark.low": null,
      "cluster.routing.allocation.disk.watermark.high": null,
      "cluster.routing.allocation.disk.watermark.flood_stage": null,
      "cluster.routing.allocation.disk.watermark.flood_stage.max_headroom": null
    }
  }'
```

The cluster should be green or at least have the per-video `default_<uuid>`
indices searchable before using an empty summary as evidence of a model or prompt
quality issue.

## Kafka / Logstash Path

The 3.2 `lvs` profile uses Kafka and shared Logstash for streaming captions and
structured summaries.

Expected topics:

| Topic | Producer / consumer |
|---|---|
| `mdx-vlm-captions` | RT-VLM produces raw captions; Logstash consumes. |
| `mdx-structured-events-summary` | the video summarization service publishes structured summaries; Logstash consumes. |

Checks:

```bash
docker logs --tail 100 logstash
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic mdx-vlm-captions \
  --max-messages 1
```

If Logstash starts but does not index video summarization data, check that the shared infra
Logstash pipeline is loading the video summarization pipeline and that protobuf
definitions are mounted from `deploy/docker/services/infra/elk/logstash`.

## API Validation Failures

`422` usually means the request body violates the OpenAPI schema.

Rules:

- `model`, `scenario`, and `events` are required for `/v1/summarize`.
- `additionalProperties: false` means extra fields can fail validation.
- Prefer `num_frames_per_second_or_fixed_frames_chunk` and
  `use_fps_for_chunking`; `num_frames_per_chunk` is deprecated.
- `schema` is a JSON schema serialized as a string, not a nested object.

## Logs

```bash
docker logs -f vss-lvs
docker logs -f vss-rtvi-vlm
docker logs -f logstash
docker logs -f kafka
```

Use bounded logs in automated checks:

```bash
docker logs --tail 200 --since 10m vss-lvs
```
