# RTVI VLM Kafka Workflows

### 3. Dense captions with alerts from an RTSP stream (Kafka incidents)

The same `/v1/generate_captions` endpoint emits alerts — there is no
per-request alert flag. Alerts are driven by **prompt design + server-side phrase
detection**: the server lower-cases each chunk's VLM response and checks for the tokens
**`"yes"` or `"true"`**. If either appears, the server builds an incident protobuf
(`isAnomaly=True`, `info["triggerPhrase"]=<matched tokens>`, `info["verdict"]="confirmed"`)
and publishes it to `KAFKA_INCIDENT_TOPIC` in addition to the normal caption message on
`KAFKA_TOPIC`. Per <https://docs.nvidia.com/vss/latest/real-time-vlm.html>.

**Recommended prompt pattern** (from the docs):
```
Anomaly Detected: Yes/No
Reason: [Brief explanation]
```
Pair it with `system_prompt` that constrains the model to answer Yes/No.
For Kafka wiring validation, use a deterministic positive prompt first, such as
asking the model to output exactly `Anomaly Detected: Yes` with a short reason.
Once offsets move on both caption and incident topics, switch back to the real
scene-analysis prompt.

### 4. HTTP response vs. Kafka message bus

When `KAFKA_ENABLED=true`, the same request produces both outputs: an HTTP
response to the caller and Kafka records for downstream message-bus consumers.

**HTTP response** from `POST /v1/generate_captions`:
- **`stream=true`** — Server-Sent Events. One SSE event per chunk containing the
  `VlmCaptionResponse` fields (`start_time`, `end_time`, `content`, `chunk_id`
  when supported). Terminated by `[DONE]` per OpenAI-style SSE convention.
- **`stream=false`** (default) — single JSON object wrapping all chunks:
  ```json
  {
    "id": "<request_id>",
    "object": "caption",
    "chunk_responses": [
      {"start_time": "...", "end_time": "...", "content": "..."}
    ],
    "usage": {...}
  }
  ```

**Kafka publish** (when `KAFKA_ENABLED=true`):
- Every caption → **`KAFKA_TOPIC`** (current compose fallback is
  `vision-llm-messages`; set `RTVI_VLM_KAFKA_TOPIC` explicitly only if your
  deployment overrides caption records) with header
  `message_type: vision_llm` and `info["incidentDetected"] = "true"|"false"`.
- Alert-positive chunks → **also** published to **`KAFKA_INCIDENT_TOPIC`**
  (current compose fallback is `vision-llm-events-incidents`) with header
  `message_type: incident`.
- Any upstream/VLM error → **`ERROR_MESSAGE_TOPIC`** (default `vision-llm-errors`)
  with header `message_type: error`.
- **Partition key:** `<request_id>:<chunk_idx>` — all messages for one (request, chunk)
  pair land on the same partition so a consumer can join the caption and the incident.
- **Value format:** NvSchema protobuf, not JSON. Use metadata-only consumers for
  quick verification; use the protobuf descriptors under
  `deploy/docker/services/infra/elk/pb_definitions/descriptors/` for structured decoding.

For deterministic validation, first check topic offsets:
```bash
KAFKA_CONTAINER="${KAFKA_CONTAINER:-kafka}" # set to mdx-kafka if your deployment uses that name

for T in vision-llm-messages vision-llm-events-incidents vision-llm-errors; do
  docker exec "$KAFKA_CONTAINER" kafka-get-offsets \
    --bootstrap-server 127.0.0.1:9092 \
    --topic "$T"
done
```

### Standalone Kafka Listener Setup

The RT-VLM compose does not bundle Kafka. For standalone tests, either start the
repo infra Kafka service by itself or provide an equivalent broker before
starting RT-VLM. The critical requirement is that the broker advertises
`${HOST_IP}:9092`, because RT-VLM is configured with
`KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092`.

Using the repo infra Kafka only:

```bash
: "${VSS_CHECKOUT:?Set VSS_CHECKOUT to the video-search-and-summarization checkout}"
: "${HOST_IP:?Set HOST_IP to an address reachable from the vss-rtvi-vlm container}"
export VSS_APPS_DIR="${VSS_APPS_DIR:-$VSS_CHECKOUT/deploy/docker}"

docker compose -f "$VSS_CHECKOUT/deploy/docker/services/infra/compose.yml" \
  --profile bp_developer_alerts_2d_vlm up -d kafka

docker exec kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --if-not-exists --topic vision-llm-messages
docker exec kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --if-not-exists --topic vision-llm-events-incidents
docker exec kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --if-not-exists --topic vision-llm-errors
```

If you use a custom standalone Kafka container, configure the equivalent of:

```bash
KAFKA_LISTENERS=PLAINTEXT://0.0.0.0:9092,CONTROLLER://localhost:9093
KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://${HOST_IP}:9092
```

Do not advertise `localhost:9094` or `kafka:9092` unless RT-VLM is intentionally
using that same network alias. Those settings can let producer/consumer tests
inside the Kafka container pass while RT-VLM fails with
`KafkaTimeoutError: Failed to update metadata after 60.0 secs`.

After Kafka is running, confirm RT-VLM can reach the same broker address it was
configured with:

```bash
KAFKA_CONTAINER="${KAFKA_CONTAINER:-kafka}" # repo infra compose uses container_name: kafka

docker exec vss-rtvi-vlm printenv KAFKA_BOOTSTRAP_SERVERS
docker logs vss-rtvi-vlm 2>&1 | grep -i 'KafkaTimeoutError\\|Failed to update metadata' || true

for T in vision-llm-messages vision-llm-events-incidents vision-llm-errors; do
  docker exec "$KAFKA_CONTAINER" kafka-get-offsets \
    --bootstrap-server 127.0.0.1:9092 \
    --topic "$T"
done
```

The standalone RT-VLM compose sets `KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092`; a
`.env` value named `KAFKA_BOOTSTRAP_SERVERS` is ignored unless you edit the
compose. If Kafka was not reachable when RT-VLM started, or if you changed the
broker advertised listener, restart/recreate RT-VLM before checking offsets:

```bash
docker compose --env-file .env -f rtvi-vlm-docker-compose.yml \
  --profile bp_developer_alerts_2d_vlm up -d --force-recreate rtvi-vlm
```

Then consume bounded, metadata-only samples from all three topics. `--timeout-ms`
prevents a no-message topic from hanging indefinitely; `print.value=false` avoids
printing protobuf bytes:
```bash
KAFKA_CONTAINER="${KAFKA_CONTAINER:-kafka}" # use rtvi-vlm-kafka-1 only for that custom broker

for T in vision-llm-messages vision-llm-events-incidents vision-llm-errors; do
  docker exec "$KAFKA_CONTAINER" kafka-console-consumer \
    --bootstrap-server 127.0.0.1:9092 \
    --topic "$T" \
    --from-beginning \
    --timeout-ms 5000 \
    --max-messages 20 \
    --property print.timestamp=true \
    --property print.key=true \
    --property print.headers=true \
    --property print.value=false
done
```

Typical proof of an HTTP + Kafka alert pass:
```text
vision-llm-messages:0:8
vision-llm-events-incidents:0:1
vision-llm-errors:0:0

CreateTime:<ms> message_type:vision_llm <request_id>:5
CreateTime:<ms> message_type:incident   <request_id>:5
```

The incident key matching the caption key (`<request_id>:<chunk_idx>`) is the
join point between the normal caption message and the alert-positive incident.
On recent Confluent Kafka images, do not override the formatter with the older
`kafka.tools.DefaultMessageFormatter`; the default consumer formatter already
supports the `print.*` properties above.

**Docs reference:** <https://docs.nvidia.com/vss/latest/real-time-vlm.html>

---
