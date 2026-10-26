VSS Python Libraries
====================

VSS provides Python APIs for video processing, ingestion, persistence, and
summarization. Install the optional dependencies needed by your workflow:

.. code-block:: console

   pip install "vss[storage]" vss-ingestion "vss-summarization[openai]"

API reference
-------------

.. toctree::
   :maxdepth: 2

   api/video_processing
   api/ingestion
   api/storage
   api/summarize_video
   api/mcp
