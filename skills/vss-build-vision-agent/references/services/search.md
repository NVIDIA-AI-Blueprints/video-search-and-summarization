# Search Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Search analytics and indexing flow | `vss-search-analytics-2d-fusion` |

## Required peers

- Requires RT-CV, RT-Embed, Elasticsearch, Kafka, Logstash, and the Search
  profile's VIOS/NvStreamer path.
- Agent search requires `COSMOS_EMBED_ENDPOINT`, `ELASTIC_SEARCH_ENDPOINT`, and
  `ELASTIC_SEARCH_INDEX`.
- Critique is enabled by default, so RT-VLM is required unless the user
  explicitly disables critique or `rtvi-vlm` is excluded from the build.
  When `rtvi-vlm` is absent — whether excluded by the user, or present only for
  a non-critique purpose (e.g. alert verification) — set `ENABLE_CRITIC=false`
  in `override.env`.
- `vss-video-analytics-api-fusion` is a **separate** service key from
  `vss-search-analytics-2d-fusion`. The analytics API (`:9901`) provides the
  REST query/browse surface over ES indices; the search-analytics service is the
  Behavior-Analytics container that produces `mdx-embed-filtered` and
  `mdx-behavior`. Both are in the Search Foundation's service set; do not
  confuse or substitute one for the other.

## Write-path topic flow

Surface this topic-level flow in the architecture preview (SKILL.md step 6
requires principal data flows and topics); it is authoritatively defined in
`skills/vss-setup-behavior-analytics/references/integrate-behavior-analytics-service.md`.

- Detection: `perception-2d-fusion -> mdx-raw`.
- Embeddings: `rtvi-embed -> mdx-embed -> vss-search-analytics-2d-fusion ->
  mdx-embed-filtered`. `vss-search-analytics-2d-fusion` is the **sole** producer of
  `mdx-embed-filtered`.
- Behaviors: `vss-search-analytics-2d-fusion -> mdx-behavior`.
- Indexing: Logstash consumes these output topics into Elasticsearch index
  patterns `mdx-raw-*`, `mdx-behavior-*`, and `mdx-embed-filtered-*`.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_BEHAVIOR_ANALYTICS_IMAGE`, `VSS_BEHAVIOR_ANALYTICS_TAG` | Select the Search analytics image inherited from Behavior Analytics. |
| `STREAM_TYPE` | Select the checked-in Kafka or Redis analytics config. |
| `COSMOS_EMBED_ENDPOINT` | Point the Agent at RT-Embed. |
| `ELASTIC_SEARCH_ENDPOINT`, `ELASTIC_SEARCH_INDEX` | Point the Agent at indexed search data. |
| `ENABLE_CRITIC` | Keep enabled by default; set to `false` when critique is excluded or `rtvi-vlm` is absent/repurposed (see Required peers). |

## Sources

- `deploy/docker/developer-profiles/dev-profile-search/.env`
- `deploy/docker/developer-profiles/dev-profile-search/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`
- `deploy/docker/services/analytics/behavior-analytics/compose.yml`
- `skills/vss-build-vision-agent/references/deployment_resolution.md`
