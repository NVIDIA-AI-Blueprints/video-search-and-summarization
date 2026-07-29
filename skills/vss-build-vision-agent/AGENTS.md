# AGENTS.md

## Scope

Applies to `vss-build-vision-agent`, the skill that composes VSS deployments
from natural-language capability requests.

## First Reads

- Read `SKILL.md` first, then only the references needed for the request.
- Use `references/composition.md` for foundation and delta-profile rules.
- Use `references/profiles/` and `references/sizing.md` before choosing a
  profile or hardware placement.
- Use `references/deployment.md`, `references/readiness.md`, and
  `references/troubleshooting.md` only after a deploy is actually requested.

## Rules

- Pick exactly one current developer profile as the Foundation. Ask only when
  two foundations have the same smallest delta.
- Pick the Foundation by requested primary capability before counting generic
  utility peers. For streamed/uploaded VLM Q&A and dense captioning with Kafka
  and Elasticsearch storage, use `base` and add Kafka, Elasticsearch,
  Logstash, and their init/health peers; do not choose `lvs` just because it
  already includes ELK, because LVS adds unrequested summarization services and
  profile-specific Kibana machinery.
- For base-derived dense captioning, keep `vss-haproxy-ingress` even when the
  Agent and UI are removed: it fronts VIOS/VST and other non-Agent backends,
  and the HAProxy template uses `init-addr none` so missing Agent/UI backends
  do not prevent startup. Do not add a custom Kafka topic definition for
  `mdx-vlm-captions`; the default `KAFKA_TOPICS` owned by
  `kafka-topic-init-container` already includes it.
- For a base-derived dense-captioning proposal, explicitly list the six added
  service keys as `kafka`, `kafka-topic-init-container`,
  `broker-health-check`, `elasticsearch`, `elasticsearch-init-container`, and
  `logstash`. `logstash` is mandatory: RT-VLM publishes captions to Kafka and
  `pipelines/kafka/mdx-lvs-logstash.conf` is the checked-in bridge that
  consumes `mdx-vlm-captions` and writes to Elasticsearch. Also explicitly
  state that HAProxy fronts VIOS/VST and ten non-Agent HTTP backends, uses
  `init-addr none`, and that no custom topic definition is required because
  `mdx-vlm-captions` is already in the default `KAFKA_TOPICS` list.
- Do not route warehouse or industry-profile requests through this skill unless
  the request is explicitly for a developer-profile-derived composition.
- In delta mode, add or remove only canonical Compose profile keys and only the
  environment knobs requested or required by the selected references.
- Treat explicitly excluded capability owners as hard removals from the
  Foundation. For example, a Search-derived RT-CV-only build that excludes
  embeddings, Search analytics/API, Agent/UI/ingress, tracing, RT-VLM, and LLM
  inference should keep VIOS, RT-CV, Kafka, Elasticsearch, Redis, Kibana,
  Logstash, and required init/wait peers, while removing `rtvi-embed`,
  `vss-search-analytics-2d-fusion`, `vss-video-analytics-api-fusion`,
  `vss-agent`, `vss-ui`, `vss-haproxy-ingress`, `phoenix`, `rtvi-vlm`, and
  `llm_${LLM_MODE}_${LLM_NAME_SLUG}`. Set `ENABLE_CRITIC=false` when critique
  or RT-VLM is excluded.
- Generate and validate `_builds/<name>/override.env`, `compose.yml`, and
  `resolved.yml` as a unit. Never treat the label `<name>` as a Compose profile.
- Present the architecture and data-flow summary before writing or deploying
  generated artifacts.
- For generation-only builds, do not create, touch, or modify files under
  `deploy/docker/` to satisfy validation. Validate checked-in bind sources as
  they exist; generated artifacts belong under `_builds/<name>/`, and runtime
  data/log directories are only prepared when deployment is requested.
- For LVS summaries, include the user-visible API shape in the architecture and
  final proof: VIOS-uploaded or recorded media flows to LVS, readiness is
  `GET /v1/ready`, models are listed at `GET /models`, summaries are requested
  with `POST /v1/summarize` using a VIOS-provided `url` or `id`, and the result
  text is in `choices[0].message.content`.
- In runtime LVS validation, never fabricate a direct file URL or use a sidecar
  file server for media. Register or select media through VIOS, obtain the clip
  URL/id from a VIOS API such as `/storage/file/<streamId>/url?container=mp4`,
  then issue exactly one LVS `POST /v1/summarize` after readiness. Include
  `model`, `scenario`, `events`, `chunk_duration`,
  `num_frames_per_second_or_fixed_frames_chunk`, `use_fps_for_chunking`, and
  the VIOS-provided `url` or `id` in that single request.

## Eval Behavior

- In non-interactive evals, follow the prompt's pre-authorization for deploy or
  teardown steps.
- Keep proof concrete: selected Foundation, effective service set, changed env
  values, readiness checks, and browser/API endpoints.
