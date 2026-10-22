# AL Archiver

Consumes the AOI Client's inference-result records from the
`aoi-inference-results` Kafka topic and forwards each one to the Object
Store-ELK Backend (BE), which upserts its Elasticsearch document by
`objStorePath`.

## Requirements

- Python >= 3.14 (managed with [uv](https://docs.astral.sh/uv/))
- A reachable Kafka broker and BE

## Project structure

```
al_archiver/
  application.py      # FastAPI factory + lifespan (starts/stops consumer)
  config.py           # pydantic-settings Settings singleton
  api/
    router.py         # /health and /ready endpoints
  core/
    consumer.py       # AIOKafkaConsumer loop, manual commit (at-least-once)
    forwarder.py      # POST each record to the BE with retry/backoff
  shared/             # reserved for shared infrastructure
  tests/
```

## Local setup

```bash
uv sync
cp .env.example .env
uv run uvicorn al_archiver.application:app --reload --port 8011
```

## Configuration

| Env var                    | Default                   | Meaning                            |
| -------------------------- | ------------------------- | ---------------------------------- |
| `KAFKA_BOOTSTRAP_SERVERS`  | `localhost:9092`          | Kafka bootstrap servers            |
| `KAFKA_TOPIC`              | `aoi-inference-results`   | Topic consumed                     |
| `KAFKA_GROUP_ID`           | `al-archiver`             | Consumer group                     |
| `BE_BASE_URL`              | `http://backend-api:8000` | Backend base URL                   |
| `BE_INFERENCE_RESULT_PATH` | `/api/inference-results`  | Backend endpoint receiving records |
| `FORWARD_TIMEOUT_S`        | `30.0`                    | Per-request timeout to the BE      |
| `MAX_RETRIES`              | `5`                       | Forward retry attempts             |
| `RETRY_BASE_DELAY_S`       | `1.0`                     | Initial backoff delay (seconds)    |
| `RETRY_MAX_DELAY_S`        | `30.0`                    | Maximum backoff delay (seconds)    |
| `RETRY_JITTER`             | `true`                    | Add jitter to backoff delays       |
| `LOG_LEVEL`                | `INFO`                    | Python logging level               |

## Behavior

- Offsets are committed manually after each record is handled (at-least-once).
- The forwarder retries transient failures (429/5xx, network) with exponential
  backoff. If a record still cannot be delivered, the offset is committed to
  make progress — the BE's reconciliation job resends `POST /api/inference` for
  any board left without an inference update (design §8), and forwarding is
  idempotent (keyed by `objStorePath`).

## API contract the BE must implement

The AL Archiver forwards each inference-result record by POSTing it to the BE at
`BE_BASE_URL + BE_INFERENCE_RESULT_PATH` (default
`http://backend-api:8000/api/inference-results`). The BE **must implement this
endpoint** — it is the sink for step 8 of the flow. Point
`BE_INFERENCE_RESULT_PATH` at whatever route the BE exposes.

### `POST /api/inference-results`

- **Content-Type:** `application/json`
- **Body:** exactly the record the AOI Client published to Kafka — forwarded
  verbatim, unwrapped (the archiver adds no envelope):

```json
{
  "objStorePath": "deft-aoi/3f8a1b2c/photo1.jpg",
  "status": "partial",
  "cropCount": 3,
  "completedCount": 2,
  "failedCount": 1,
  "deployment_type": "shadow",
  "cropResults": [
    {
      "cropId": "U16",
      "status": "completed",
      "old_model": {
        "version": "1",
        "score": 0.999967,
        "verdict": "DEFECT",
        "threshold": 0.332784
      },
      "new_model": {
        "version": "2",
        "score": 0.8,
        "verdict": "DEFECT",
        "threshold": 0.332784
      }
    },
    {
      "cropId": "U40",
      "status": "completed",
      "old_model": {
        "version": "1",
        "score": 0.12,
        "verdict": "PASS",
        "threshold": 0.332784
      },
      "new_model": {
        "version": "2",
        "score": 0.09,
        "verdict": "PASS",
        "threshold": 0.332784
      }
    },
    {
      "cropId": "U55",
      "status": "failed"
    }
  ]
}
```

### Field semantics

| Field                             | Type                                 | Meaning                                                                                        |
| --------------------------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `objStorePath`                    | string                               | The board's ELK key set by the BE at ingestion. **Upsert the document matched by this value.** |
| `status`                          | `completed` \| `partial` \| `failed` | Board-level job status: all crops ok (incl. `cropCount: 0`), some failed, or all failed.       |
| `cropCount`                       | int                                  | Number of flagged components inspected.                                                        |
| `completedCount`                  | int                                  | Crops with a verdict.                                                                          |
| `failedCount`                     | int                                  | Crops with no verdict.                                                                         |
| `cropResults[]`                   | array                                | One entry per flagged component.                                                               |
| `cropResults[].cropId`            | string                               | Matches the request's `cropInfo[*].id` (e.g. `U16`).                                           |
| `cropResults[].status`            | `completed` \| `failed`              | Per-crop status. `failed` crops carry no model results.                                        |
| `cropResults[].old_model`         | object \| null                       | Result from the currently deployed model (present when `completed`).                           |
| `cropResults[].new_model`         | object \| null                       | Result from the candidate model (present when `completed`).                                    |
| `cropResults[].*_model.version`   | string                               | Model version identifier.                                                                      |
| `cropResults[].*_model.score`     | float                                | Defect score.                                                                                  |
| `cropResults[].*_model.verdict`   | `PASS` \| `DEFECT`                   | Verdict.                                                                                       |
| `cropResults[].*_model.threshold` | float                                | Decision threshold used.                                                                       |
| `deployment_type`                 | `normal` \| `shadow` \| `canary`     | Active Learning deployment mode (lowercase enum; rejected with 422 otherwise).                 |

### Required behavior

- **Idempotent upsert by `objStorePath`.** The same board may be delivered more
  than once (at-least-once bus + retries + BE reconciliation resends). A repeat
  must **overwrite** the same ELK document, never create a second one.
- **Response codes the archiver understands:**
  - **2xx** — accepted; the archiver commits the Kafka offset.
  - **429 / 500 / 502 / 503 / 504** — transient; the archiver retries with
    backoff (`MAX_RETRIES`), then commits and moves on.
  - **Other 4xx** — permanent rejection; the archiver logs and commits (does not
    retry). The board is then recovered by the BE's reconciliation job.
- The response body is ignored; only the status code matters.

> The archiver keeps no durable state, so the BE's reconciliation job (design
> §8) — which resends `POST /api/inference` for boards left without an inference
> update — is the durability backstop and is **not optional in production**.

## Testing

```bash
uv run pytest
```

## Docker

```bash
docker build -t al-archiver .
docker run -p 8011:8000 --env-file .env al-archiver
```

Or via the umbrella compose: `docker compose -f deploy/docker/compose/compose.yaml up -d al-archiver`.
