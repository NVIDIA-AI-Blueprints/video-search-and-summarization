# ReID service tests

Two tiers.

| Tier | Marker | Needs | What it covers |
|---|---|---|---|
| Unit | `unit` | python | `POST /search` request validation, from the checked-out source. |
| Integration | `integration` | docker | Starts the image given by `--image-ref`, plus Kafka and Milvus. Publishes one embedding to `mv3dt` and checks `GET /health/*` and `POST /search`. |

Debug endpoints stay off. The embedding is a protobuf `Frame` on Kafka, which is the service default, not `POST /insert`.

## Install

From this directory:

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Run

With that environment still active, from this directory:

```bash
# Checked-out source. No docker.
pytest -m unit -v

# The image under test. Docker must be able to pull it.
pytest -m integration -v --image-ref=nvcr.io/your-org/reid-embed:tag
```

`--image-ref` can also be set with `IMAGE_REF`. Integration tests are skipped when neither is set. `--keep-stack` leaves the compose project up for debugging.
