Storage
========

Choose a backend
----------------

Storage is opt-in. Ingestion persists captions only when supplied with
``storage=...``, ``storage_config=...``, or explicit ``VSS_STORAGE_BACKEND``
environment selection.

.. list-table:: Built-in backends
   :header-rows: 1
   :widths: 20 40 40

   * - Backend
     - Use
     - Requirements
   * - ``memory``
     - Process-local snapshots; no restart resume
     - Base package
   * - ``file``
     - Durable captions and local VLM checkpoints
     - Trusted local filesystem; default root ``~/.vss``
   * - ``elasticsearch``
     - Remote snapshots and local VLM checkpoints
     - Storage extra, running server, same-host ingestion claim directory

Memory and file adapters do not require the Elasticsearch client. Storage saves
JSON records, not source videos or decoded frames. Keep immutable source media
separately when resuming ingestion.

Persist and summarize captions
------------------------------

Configure the VLM endpoint for ingestion and the summary LLM endpoint for later
summarization. A durable local VLM job requires an explicit model, concrete
decoder, caller-maintained pipeline revision, and stable source identity:

.. code-block:: python

   import os
   from pathlib import Path

   from vss.ingestion import RequestSource, ingest
   from vss.storage import StorageConfig, create_storage
   from vss.summarization import summarize_from_store

   config = StorageConfig(backend="file", root=Path(".vss-data"))
   source = RequestSource(url="/path/to/video.mp4", source_id="demo-video")

   async def ingest_and_save() -> str:
       async with create_storage(config) as storage:
           job = ingest(
               source,
               pipeline="cpu_vlm",
               model=os.environ["VLM_MODEL"],
               decoder="opencv",
               pipeline_revision="demo-v1",
               storage=storage,
           )
           print(f"job_id={job.id} source_id={source.source_id}", flush=True)
           try:
               await job
           finally:
               await job.wait_closed()
           return str(job.id)

   async def summarize_saved(job_id: str):
       return await summarize_from_store(job_id, storage_config=config)

Retain the printed identifiers before inference. To resume after the previous
attempt has stopped, repeat the original ingestion call with
``job_id=UUID(saved_job_id)`` and ``resume=True`` using the same source/settings
and storage backend. Import ``UUID`` from ``uuid``. Confirmed windows skip
inference; incompatible or missing checkpoints fail. Whole-request RTVI supports
completed-caption storage, without partial-request resume.

Stored summarization needs completed captions and an LLM endpoint. It does not
need the original video, decoder, or VLM endpoint. Injected adapters remain
caller-owned; configuration-created adapters close their own resources.

Elasticsearch configuration
---------------------------

Install ``.[storage]`` from the repository root and supply a running server.
Use ``StorageConfig(backend="elasticsearch", elasticsearch=ElasticsearchConfig(
url="https://elasticsearch.example.com:9200", index="vss-video-checkpoints"))``
in the same workflow. Adapter-created clients read their authentication key from
``VSS_STORAGE_ES_API_KEY``; keep credentials out of the URL and config.

Ingestion claims coordinate same-host processes using the same endpoint, index,
claim root, and OS identity. Remote snapshots do not provide distributed job
ownership, automatic queue recovery, or migration from file snapshots.

See the repository's ``src/vss/storage/README.md`` for runnable CLI commands,
environment settings, error recovery, and operational limits.

API reference
-------------

.. automodule:: vss.storage
   :members:
   :imported-members:
   :show-inheritance:
