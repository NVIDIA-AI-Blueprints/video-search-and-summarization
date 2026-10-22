# eval-set-worker

Data-plane workers for the **Eval-set** pipeline (design Steps 3–7, track 11.3).
The backend (`backend/`) opens one shared Elasticsearch PIT and fans out N slice
work items to Kafka (`eval-slices`); these workers do the actual export. Two
entrypoints share this package and run as two separate containers:

- **`eval_set_worker.slice_service`** — consumes `eval-slices` (one slice per
  partition), runs the sliced `search_after` + PIT scan for its slice, writes
  `manifest<i>.jsonl` to the object store, and publishes a completion to
  `eval-results`.
- **`eval_set_worker.summary_service`** — a completion is only a wake-up; the
  consumer counts the durable per-slice markers under `_status/`, and once all N
  are present aggregates them, writes `eval.json`, flips `status.json` to
  `ready`/`failed`, closes the shared PIT, and **deletes the `_status/` markers**.
  Object-store driven ⇒ restart-safe; eval.json makes finalise idempotent.

The eval **API** (`POST /v1/results`, `GET /v1/eval/<eval_id>`) lives in
`backend/` — this package is only the data plane, mirroring `snapshot_worker/`.

## Object-store layout (design §8)

```
<bucket>/evals/<eval_id>/
├── status.json                       # progress + terminal state (running|ready|failed)
├── manifest0.jsonl … manifest<N-1>.jsonl   # one JSONL record per doc in the slice
└── eval.json                     # aggregate summary, written on finalise
```

While a job is running there is also a transient `_status/slice<i>.json` per slice
(internal bookkeeping the summary consumer counts). These are **deleted on finalise**,
so a finished eval set contains only the manifests + `eval.json` + `status.json`
that the ML team consumes.

`GET /v1/eval/<eval_id>` returns the agreed contract (`EvaluationSetStats`):
`{eval_id, status, total_records, manifests[], created_at}`, where each manifest is
`{format:"jsonl", uri:"s3://<bucket>/evals/<eval_id>/manifest<i>.jsonl", size_bytes,
sha256}`. Clients resolve the `s3://` uri to a download via `POST /v1/get_presigned_urls`.

## Key behaviors

- **Consistency** — every slice reads the one shared PIT (`pit_id` travels in the
  work item), so the union of slices is a single frozen, duplicate-free view.
- **Constant-memory export** — each slice streams its `manifest<i>.jsonl` straight
  to a temp file page-by-page (never holding the whole slice in RAM) and uploads via
  a single managed multipart transfer; sha256/size are computed incrementally. Safe
  for large eval datasets.
- **Idempotent manifests** — the whole-object PUT plus a stable `_shard_doc` sort
  makes a redelivered slice write identical bytes (effectively once-complete).
- **Deduped aggregation** — the summary consumer dedupes completions by
  `slice_id`, so a redelivered completion cannot double-count.
- **SeaweedFS keep-alive guard** — S3 requests force `Connection: close` +
  bounded timeouts/retries so a poisoned pooled socket can never hang the
  long-lived consumer (BUG-A).

- **Always-on workers** — the consumers are long-running and do not exit/restart
  on a message failure. A transient S3/Kafka blip is retried in place with
  exponential backoff (`HANDLER_MAX_RETRIES`); handlers are idempotent (marker PUT
  keyed by slice_id; `try_finalize` guarded by `eval.json`), so a retry can't
  double-count. A message that keeps failing is skipped so the worker keeps
  serving other events; a poison (undecodable) message is committed + skipped.

## Configuration (env)

| Variable                  | Default                    | Description                          |
| ------------------------- | -------------------------- | ------------------------------------ |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092`           | Kafka broker                         |
| `EVAL_SLICES_TOPIC`       | `eval-slices`              | Work items (slice i → partition i)   |
| `EVAL_RESULTS_TOPIC`      | `eval-results`             | Completions, keyed by `eval_id`      |
| `EVAL_SLICES_GROUP`       | `eval-slices-consumer`     | Slice consumer group                 |
| `EVAL_SUMMARY_GROUP`      | `eval-summary-consumer`    | Summary consumer group               |
| `ES_URL`                  | `http://localhost:9200`    | Elasticsearch (holds the shared PIT) |
| `AWS_ENDPOINT`            | `http://seaweedfs-s3:8333` | Object store (S3)                    |
| `AWS_BUCKET`              | `aoi-samples`              | Bucket                               |
| `EVAL_PREFIX`    | `evals`                     | Key prefix                           |
| `EVAL_PAGE_SIZE`          | `1000`                     | `search_after` page size             |

## Test

```bash
cd eval_set_worker && uv sync
cd .. && eval_set_worker/.venv/bin/python -m pytest eval_set_worker/tests -q
```

## Run (Docker Compose)

```bash
# slice consumers scale to the slice count (EVAL_PARTITIONS):
docker compose -f deploy/docker/compose/compose.yaml up -d --scale eval-slice-consumer=3
```
