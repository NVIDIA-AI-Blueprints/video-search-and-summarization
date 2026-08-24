# Legacy reference code

Frozen source from the original NVIDIA VSS blueprint, kept **for reference
only** — it is not built, tested, or deployed.

`legacy/agent` contains the former NeMo Agent Toolkit implementation. We keep
it because its retrieval logic is the donor for our LangChain tools:

| Legacy file | Port into |
|---|---|
| `src/vss_agents/tools/search.py` | query decomposition + RRF/weighted fusion → `services/agent/app/tools/search_transcript.py`, `search_visual_events.py` |
| `src/vss_agents/tools/embed_search.py` | kNN patterns → `retrieve_context.py` |
| `src/vss_agents/video_analytics/es_client.py`, `query_builders.py` | query-layer semantics |
| `src/vss_agents/prompt.py`, `agents/search_agent.py` | prompt engineering |

Once a port is complete and covered by tests in `services/agent`, delete the
corresponding legacy file. Delete this whole directory when the agent rewrite
is finished.
