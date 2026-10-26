MCP adapter
===========

The VSS MCP server exposes public library capabilities over stdio for agent
harnesses. The adapter delegates to the library APIs and preserves their
structured JSON contracts.

Installation
------------

Install the complete server, including ingestion and OpenAI-compatible
summarization:

.. code-block:: console

   pip install "vss[mcp]"

For a smaller deployment, install ``vss[mcp-core]`` and add
``vss-ingestion`` and/or ``vss-summarization[openai]``. Core video-processing
and file-storage tools are always registered. Optional ingestion and
summarization tools are registered only when their packages import successfully.
An import failure inside an installed capability is reported rather than hidden.

Run the server
--------------

.. code-block:: console

   python -m vss.mcp --namespace cosmos --workspace /tmp/vss

The server registers these core tools:

* ``probe_video`` returns source metadata.
* ``extract_frames`` returns base64-encoded frames and timing metadata.
* ``storage_read`` reads a snapshot from ``<workspace>/.vss``.
* ``storage_write`` creates or compare-and-swaps a snapshot there.

Installing ingestion adds ``ingest`` and ``ingest_status``. Installing
summarization adds ``summarize`` and ``summarize_from_store``.

Local video paths, including Windows drive-letter paths and file URIs, must
resolve below the configured workspace. Remote URLs pass through to the selected
ingestion backend. The server retains the 128 most recent completed ingestion
jobs for status lookup. Each retained job owns its original result only; the
adapter does not retain a second serialized copy.
