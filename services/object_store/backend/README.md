# deft-aoi-object-store

A FastAPI service for uploading batches of AOI images with structured metadata to SeaweedFS (S3-compatible object storage). After each successful upload, the metadata is published to a Kafka topic for downstream processing.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) — Python package manager
- Docker (optional, for containerized deployment)
- A running SeaweedFS (or any S3-compatible) instance
- A running Kafka broker

## Project structure

```
app/
├── api/
│   ├── metadata.py     # AOIMetadata Pydantic model (strict schema)
│   ├── router.py       # POST /api/samples endpoint
│   ├── schemas.py      # Request/response Pydantic models + validation
│   └── service.py      # Per-image upload + Kafka publish logic
├── shared/
│   ├── kafka.py        # aiokafka producer service
│   └── storage.py      # boto3 S3-compatible client (upload + delete)
├── application.py      # FastAPI app factory + lifespan
└── config.py           # Settings loaded from environment
tests/
├── conftest.py         # Shared fixtures (mock storage, mock kafka, test client)
├── test_schemas.py
├── test_service.py
└── test_router.py
```

## Local setup

**1. Install dependencies**

```bash
uv sync
```

**2. Configure environment**

Copy the example and fill in your values:

```bash
cp .env.example .env
```

`.env` variables:

| Variable                  | Description                   | Example                 |
| ------------------------- | ----------------------------- | ----------------------- |
| `AWS_ENDPOINT`            | SeaweedFS S3 endpoint URL     | `http://localhost:8333` |
| `AWS_ACCESS_KEY_ID`       | S3 access key                 | `your-access-key`       |
| `AWS_SECRET_ACCESS_KEY`   | S3 secret key                 | `your-secret-key`       |
| `AWS_BUCKET`              | Target bucket name            | `aoi-samples`           |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker address(es)      | `localhost:9092`        |
| `KAFKA_TOPIC`             | Topic to publish AOI metadata | `aoi-metadata`          |

**3. Start the server**

```bash
uv run uvicorn app.application:app --reload
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

## API

### `POST /api/samples`

Upload a batch of images with AOI metadata.

**Constraints:**

- Maximum 10 images per request
- Maximum 10 MB total decoded size per request
- `metadata` must be a JSON string conforming to the required fields below

**Request body:**

```json
{
  "images": [
    {
      "filename": "photo1.jpg",
      "content_type": "image/jpeg",
      "data": "<base64-encoded-image>",
      "metadata": "{\"version\":\"1.0\",\"description\":\"PCB top side\",\"timeStamp\":\"2026-06-12T00:00:00Z\",\"objStorePath\":\"\",\"machineInfo\":{\"machineId\":\"m-01\",\"location\":\"line-1\",\"jobType\":\"inspection\"},\"aoiDefects\":{\"count\":0,\"details\":[]},\"imageInfo\":{}}"
    }
  ]
}
```

**Required metadata fields** (JSON string must contain all of these):

| Field          | Type   | Description                                                                   |
| -------------- | ------ | ----------------------------------------------------------------------------- |
| `version`      | string | Schema version                                                                |
| `description`  | string | Image description                                                             |
| `timeStamp`    | string | Capture timestamp (ISO 8601)                                                  |
| `objStorePath` | string | Set server-side to the storage key — any client value is overwritten          |
| `machineInfo`  | object | `machineId`, `location`, `jobType` (all required)                             |
| `aoiDefects`   | object | `count` and `details` array                                                   |
| `imageInfo`    | object | Optional sub-fields: `imageType`, `path`, `layer`, `cropInfo`. `imageSize` (bytes) is set server-side from the decoded image |

**Response (HTTP 207):**

```json
{
  "uploaded": [
    {
      "filename": "photo1.jpg",
      "storage_key": "deft-aoi/<sha256-prefix>/photo1.jpg",
      "url": "http://localhost:8333/aoi-samples/deft-aoi/<sha256-prefix>/photo1.jpg",
      "size_bytes": 204800
    }
  ],
  "failed": []
}
```

The endpoint always returns `207 Multi-Status`. Per-image failures appear in the `failed` list without aborting the batch.

**Per-image failure conditions:**

- `AOIMetadata` sub-field validation fails (e.g. missing `machineInfo.machineId`)
- Storage upload fails
- Kafka publish fails (a compensating delete is attempted; if delete also fails, the orphaned key is logged)

## Storage key design

Keys use a deterministic SHA-256 prefix:

```
deft-aoi/{sha256(image_bytes)[:16]}/{filename}
```

This makes orphaned objects (upload succeeded, Kafka failed) **findable by content** — the same image always maps to the same key regardless of when it was uploaded.

## Upload + Kafka flow

```
decode base64
    │
    ▼
generate deterministic key (sha256)
    │
    ▼
parse + validate AOIMetadata (sets objStorePath = key)
    │
    ▼
storage.upload(key, data, content_type, metadata)
    │
    ├─ fails ──────────────────────────────► FailedImage
    │
    ▼
kafka.send(metadata_dict)
    │
    ├─ fails ──► storage.delete(key)
    │                │
    │                ├─ delete fails ──► log orphaned key
    │                │
    │                └─────────────────► FailedImage
    │
    ▼
UploadedImage
```

## Smart City frame retention and compression

Archived ITS frames (`deft-its/{sha256[:16]}/{frameId}.{ext}`, indexed in `sc_*`)
are kept until the retention pass in `api/sc_gc.py` removes them. With
`SC_GC_ENABLED=true` the backend runs a pass every `SC_GC_INTERVAL_S` (the first
one a few minutes after startup); run it on one replica only. A pass:

1. **deletes** the `sc_*` documents that were hidden more than `SC_GC_HOLD_S`
   ago, then their objects, except objects another document still points to,
   objects a `kpi/*/manifest*.jsonl` lists, and objects written again after
   their frame was first archived. A hidden document whose frame was archived
   again is made visible instead;
2. **hides** the documents whose `ingestedAt` is older than
   `SC_GC_RETENTION_DAYS` and, with `SC_GC_REQUIRE_SNAPSHOT`, older than the
   latest ITS snapshot request less one hour, by setting `expiredAt`. ITS
   snapshots leave hidden documents out. KPI tags (`kpi`, `kpi-golden`) are
   never hidden;
3. with `SC_GC_PRUNE_SNAPSHOTS` (default on), deletes ITS snapshot
   directories (`snapshots/<id>/`, whose manifests list the whole pool) once
   every object in them is older than `SC_GC_HOLD_S`, keeping the latest ITS
   snapshot and anything not marked `"catalog": "its"`;
4. with `SC_GC_ORPHAN_SWEEP`, deletes objects under `ITS_OBJECT_PREFIX` that no
   `sc_*` document points to and that are older than `SC_GC_ORPHAN_GRACE_S`.
   Only keys shaped like archived frames (`<prefix>/<16 hex>/<frameId>.<ext>`)
   are considered. The sweep stops if any `sc_*` index is closed or fails to
   answer, if the prefix overlaps another area of the bucket, or if more than
   `SC_GC_ORPHAN_MAX_RATIO` of a listing page is unreferenced;
5. calls `SEAWEEDFS_MASTER_URL/vol/vacuum?garbageThreshold=...`. SeaweedFS only
   marks deleted objects as garbage; the disk space comes back when their
   volume is compacted. Read-only volumes (a volume server marks all of them
   once its disk is nearly full) are not compacted and are reported as a
   failure: free space, then run `weed shell volume.vacuum`.

A snapshot taken before a frame was hidden may list it, but the frame is only
deleted after the hold, so every snapshot, its manifests and its frames, stays
readable for at least `SC_GC_HOLD_S` after it was taken. Set it above the
longest time between a snapshot request and the end of the fine-tuning run
that reads it. With retention on, an ITS snapshot selects the frames of the
last `SC_GC_RETENTION_DAYS` rather than the whole history.

`SC_GC_DRY_RUN=true`, or a one-off `python -m backend.api.sc_gc --dry-run`,
reports what a pass would do; the command exits 1 when a pass reports an error.
The ITS snapshot stats count `records_since_last_snapshot` by `ingestedAt`, so
deleted records do not lower it.

**Lifecycle backstop.** ITS frames other than KPI-set ones are written with
the S3 tag `deft-retention=its-frame` (`SC_FRAME_TAGGING`, on by default; the
tag alone changes nothing). With `SC_LIFECYCLE_ENABLED=true` the backend keeps
one rule, `deft-its-frame-expiry`, in the bucket's lifecycle configuration: objects
under `ITS_OBJECT_PREFIX/` carrying that tag expire after
`ceil(SC_GC_RETENTION_DAYS + SC_GC_HOLD_S / 86400) + 1` days (17 with the
defaults), so the retention pass normally deletes them first and the rule
only catches what it misses. AOI images, snapshots, KPI manifests and untagged
frames never match. Other rules in the bucket are kept; switching the setting
off removes only this rule. The rule is synced at startup and after every
pass, and only written when:

- `SC_GC_ENABLED=true` (otherwise expired objects would leave catalog documents
  that snapshots still select);
- the master at `SEAWEEDFS_MASTER_URL` reports SeaweedFS 4.24 or later. 4.17
  and older turn a tag-filtered rule into a TTL on the whole bucket, AOI images
  included; 4.18 to 4.23 accept it but never apply it;
- something runs it: SeaweedFS evaluates tag rules only in the `s3_lifecycle`
  job of `weed admin` + `weed worker`. Compose starts them with
  `COMPOSE_PROFILES=its-lifecycle`; the worker serves that job only. The
  Blueprint's SeaweedFS (nvidia-vss) has no worker, so leave it off there.

A refused rule is logged at ERROR and reported by the pass. Frames written
before tagging existed carry no tag; the retention pass still handles them.

`SC_FRAME_COMPRESSION` re-encodes ITS JPEG frames before their key is derived:
`lossless` (jpegtran, pixel-identical) or `lossy` (djpeg piped into cjpeg at
`SC_FRAME_JPEG_QUALITY`). A result that is not smaller is discarded, and KPI
frames are stored as received. The tools come from `libjpeg-turbo-progs`, which
the image installs.

## Testing

```bash
PYTHONPATH=..:. uv run pytest tests/ -v
```

Kafka and storage are fully mocked — no external services needed to run the test suite.

## Docker

**Build the image:**

```bash
docker build -t deft-aoi-be .
```

**Run the container:**

```bash
docker run -p 8000:8000 --env-file .env deft-aoi-be
```

Pass individual environment variables instead of a file if preferred:

```bash
docker run -p 8000:8000 \
  -e AWS_ENDPOINT=http://your-seaweedfs:8333 \
  -e AWS_ACCESS_KEY_ID=your-key \
  -e AWS_SECRET_ACCESS_KEY=your-secret \
  -e AWS_BUCKET=aoi-samples \
  -e KAFKA_BOOTSTRAP_SERVERS=your-broker:9092 \
  -e KAFKA_TOPIC=aoi-metadata \
  deft-aoi-be
```
