# D1 + Cosmos runtime

This component provides the D1 model service, H.264 RTSP ingest, trigger and Cosmos review workers, and Elasticsearch indexing for the Jetson Thor example. See the [example README](../README.md) for deployment.

| Image | Startup and state |
| --- | --- |
| `Dockerfile.model` | Downloads the pinned LiquidAI/d1-2B snapshot in [model-lock.json](model-lock.json) to `/models`; serves `/health` and `/classify` on port 8001. |
| `Dockerfile.worker` | Accepts the `ingest` (port 8020), `analytics` (port 8021) or `indexer` command. Analytics discovers VIOS through `D1_VSS_ORIGIN` using the checkout's VSS CLI. Each role has one process. |

Both Dockerfiles use the VSS repository root as their build context. The worker image uses the release's ARM64 RT-VLM base for Jetson GStreamer and hardware decoding; it starts a D1 worker. The model and ingest containers require the NVIDIA runtime. Use separate persisted state for the ingest and analytics roles. Store credentials in local deployment configuration.

The source selector accepts only the two bootstrap-created IDs `flowing` and `queued`. The example registers their RTSP inputs with VIOS and pairs each stream revision with a trigger rule. A selection toggles existing streams and rules while preserving their prompts and revisions. The selector does not launch processes or accept RTSP URLs from the browser.

Set `NEMOTRON_URL` to an optional private Nemotron Bolt endpoint for prompt drafting. The D1 decision loop, Cosmos verification and clip Q&A run without that endpoint.

Run the CPU-only tests from this directory with an isolated Python 3.12 environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest==9.1.1
.venv/bin/python -m pytest -q
```
