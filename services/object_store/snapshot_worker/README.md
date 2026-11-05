# snapshot-worker

Data-plane workers for the Catalog Snapshot pipeline (design Steps 3–7). The
backend (`backend/`) opens one shared Elasticsearch PIT and fans out N slice
work items to Kafka; these workers do the actual export. Two entrypoints share
this package and run as two separate containers:

- **`snapshot_worker.slice_service`** — consumes `snapshot-slices` (one slice per
  partition), runs the sliced `search_after` + PIT scan for its slice, streams
  `manifest<i>.jsonl` to the object store, and reports the outcome to
  `snapshot-summary`.
- **`snapshot_worker.finalizer_service`** — consumes `snapshot-summary`,
  aggregates a snapshot's slice reports, writes `snapshot.json`, promotes
  `latest_snapshot.json`, and closes the shared PIT.

## Object-store layout

```
<bucket>/snapshots/
├── <snapshot_id>/
│   ├── manifest0.jsonl … manifest<N-1>.jsonl   # one JSONL record per doc
│   ├── _status/slice0.json … slice<N-1>.json   # durable per-slice markers
│   └── snapshot.json                           # summary
└── latest_snapshot.json                        # last successful snapshot.json (delta baseline)
```

## Key behaviors

- **Consistency** — every slice reads the one shared PIT (`pit_id` travels in the
  work item), so the union of slices is a single frozen, duplicate-free view.
- **Streaming manifests** — each slice pages ES and writes its manifest to a
  temp file, then uploads it via a single managed (multipart) transfer — constant
  memory regardless of slice size; the manifest object appears atomically.
- **Durable, restart-safe finalization** — completion is derived from the
  `_status/` markers in the object store, not in-memory counters. A finalizer
  restart loses no progress, and a `snapshot.json` existence check keeps
  finalization idempotent (no double PIT close).
- **Per-slice retries** — transient errors are retried with exponential backoff
  (`MAX_RETRIES`, default 3) before a slice is marked failed.
- **Ready-only baseline** — only successful snapshots promote
  `latest_snapshot.json`; a failed snapshot never becomes the delta baseline.

## Configuration (env)

| Variable                  | Default                     | Description                          |
| ------------------------- | --------------------------- | ------------------------------------ |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092`            | Kafka broker                         |
| `ES_URL`                  | `http://localhost:9200`     | Elasticsearch (holds the shared PIT) |
| `AWS_ENDPOINT`            | `http://seaweedfs-s3:8333`  | Object store (S3)                    |
| `AWS_BUCKET`              | `aoi-samples`               | Snapshot bucket                      |
| `SNAPSHOT_PREFIX`         | `snapshots`                 | Key prefix                           |
| `PAGE_SIZE`               | `1000`                      | `search_after` page size             |
| `MAX_RETRIES`             | `3`                         | Per-slice retries on transient error |

## Test

```bash
uv sync --no-install-project
cd .. && snapshot_worker/.venv/bin/python -m pytest snapshot_worker/tests -q
```
