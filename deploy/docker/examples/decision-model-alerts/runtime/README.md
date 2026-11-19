# D1 + Cosmos runtime

This component provides the D1 model service, H.264 RTSP ingest, trigger and Cosmos review workers, and Elasticsearch indexing for the Jetson Thor example. See the [example README](../README.md) for deployment.

| Image | Startup and state |
| --- | --- |
| `Dockerfile.model` | Downloads the pinned LiquidAI/d1-2B snapshot in [model-lock.json](model-lock.json) to `/models`; serves `/health` and `/classify` on port 8001. |
| `Dockerfile.worker` | Accepts the `ingest` (port 8020), `analytics` (port 8021) or `indexer` command. Analytics discovers VIOS through `D1_VSS_ORIGIN` using the checkout's VSS CLI. Each role has one process. |

Both Dockerfiles use the VSS repository root as their build context. The worker image uses the release's ARM64 RT-VLM base for Jetson GStreamer and hardware decoding; it starts a D1 worker. The model and ingest containers require the NVIDIA runtime. Use separate persisted state for the ingest and analytics roles. Store credentials in local deployment configuration.

The source selector accepts only the two bootstrap-created IDs `flowing` and `queued`. The example registers their RTSP inputs with VIOS and pairs each stream revision with a trigger rule. A selection toggles existing streams and rules while preserving their prompts and revisions. The selector does not launch processes or accept RTSP URLs from the browser.

Set `NEMOTRON_URL` to an optional private Nemotron Bolt endpoint for prompt drafting. The D1 decision loop, Cosmos verification and clip Q&A run without that endpoint.

Verification requires a semantic condition: a yes/no condition, an answer label,
or a range of score labels. Confidence alone is not a supported trigger. Yes/no
rules use `gte` at 0.5 or higher to verify yes, or `lte` at 0.5 or lower to verify
no. Invalid settings are rejected before saving. On upgrade, an incompatible
saved rule is paused and its rule-list entry includes `validation_error`; edit
the condition and resume it after reviewing the error.
An individual decision with missing or incompatible question metadata is
ignored with a diagnostic and breaks the positive streak; it does not disable
valid settings or count as evidence that an existing incident has cleared.

Applying paired settings pauses review while D1 is updated. If that update
fails, reload the settings and retry: the original enablement is durably saved
and restored only after a successful paired update. A manual rule edit cancels
that recovery intent. Rules already disabled before this recovery mechanism was
installed remain disabled because their original intent cannot be inferred.

Kafka decision processing saves a topic/partition watermark in the same SQLite
transaction as rule state, review jobs and the outbox; the broker offset is
committed afterwards. Watermarks keep consumer replay safe without retaining an
ID forever for every check. A 4,096-entry cache additionally suppresses recent
duplicate producer IDs; direct offline callers without broker positions only
have this bounded deduplication window. The pre-upgrade `consumed` table is kept
as a frozen ledger so upgrading does not discard old deduplication evidence.
Do not reuse an analytics database with a recreated Kafka topic or a different
cluster: offset identity belongs to that deployment.

Run the CPU-only tests from this directory with an isolated Python 3.12 environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest==9.1.1
.venv/bin/python -m pytest -q
```
