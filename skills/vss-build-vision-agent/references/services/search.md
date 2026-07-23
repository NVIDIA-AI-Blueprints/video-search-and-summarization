# Search Capability Owner

## Capabilities and service keys

| Capability | Canonical service profile keys |
|---|---|
| Search analytics and indexing flow | `vss-search-analytics-2d-fusion` |
| Search HTTP API | `vss-video-analytics-api-fusion` |

## Required peers

- Requires RT-CV, RT-Embed, Elasticsearch, Kafka, Logstash, and the Search
  profile's VIOS/NvStreamer path.
- Agent search requires `COSMOS_EMBED_ENDPOINT`, `ELASTIC_SEARCH_ENDPOINT`, and
  `ELASTIC_SEARCH_INDEX`.
- VLM NIM is required only when critique is enabled.

## Configuration knobs

| Environment variable | Use |
|---|---|
| `VSS_BEHAVIOR_ANALYTICS_IMAGE`, `VSS_BEHAVIOR_ANALYTICS_TAG` | Select the Search analytics image inherited from Behavior Analytics. |
| `VSS_VIDEO_ANALYTICS_API_IMAGE`, `VSS_VIDEO_ANALYTICS_API_TAG`, `MDX_PORT` | Configure the Search API image and port. |
| `STREAM_TYPE` | Select the checked-in Kafka or Redis analytics config. |
| `COSMOS_EMBED_ENDPOINT` | Point the Agent at RT-Embed. |
| `ELASTIC_SEARCH_ENDPOINT`, `ELASTIC_SEARCH_INDEX` | Point the Agent at indexed search data. |
| `ENABLE_CRITIC` | Add or remove critique VLM requirements. |
| `REACT_APP_API_ENDPOINT_BASE_URL`, `UI_PORT` | Configure the Search UI/API route where exposed. |

## Sources

- `deploy/docker/developer-profiles/dev-profile-search/.env`
- `deploy/docker/developer-profiles/dev-profile-search/overrides.env`
- `deploy/docker/developer-profiles/dev-profile-search/video-analytics-2d-app/compose.yml`
- `deploy/docker/services/analytics/behavior-analytics/compose.yml`
- `deploy/docker/services/analytics/video-analytics-api/compose.yml`
- `skills/vss-search-archive/references/deployment_resolution.md`
