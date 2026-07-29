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
  `kafka-topic-init-container` already includes it. In the proposal and final
  proof, explicitly state that `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions`
  overrides RT-VLM's default topic `vision-llm-messages`.
- For a base-derived dense-captioning proposal, explicitly list the six added
  service keys as `kafka`, `kafka-topic-init-container`,
  `broker-health-check`, `elasticsearch`, `elasticsearch-init-container`, and
  `logstash`; do not include `redis` in the added set because it is retained
  from `base`. Use separate "Retained", "Added", and "Removed" lists if you
  present a table. `logstash` is mandatory: RT-VLM publishes captions to Kafka
  and `pipelines/kafka/mdx-lvs-logstash.conf` is the checked-in bridge that
  consumes `mdx-vlm-captions` and writes to Elasticsearch. Also explicitly
  state these proof facts in the architecture preview and final answer:
  `vss-haproxy-ingress` is retained, fronts VIOS/VST and ten non-Agent HTTP
  backends, and uses `init-addr none`; `RTVI_VLM_KAFKA_ENABLED=true` changes
  the base default from `false`; `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions`
  overrides the RT-VLM default topic `vision-llm-messages`; no custom Kafka
  topic definition is required because `mdx-vlm-captions` is already in the
  default `KAFKA_TOPICS` list.
- Do not route warehouse or industry-profile requests through this skill unless
  the request is explicitly for a developer-profile-derived composition.
- In delta mode, add or remove only canonical Compose profile keys and only the
  environment knobs requested or required by the selected references.
- Treat explicitly excluded capability owners as hard removals from the
  Foundation. For example, a Search-derived RT-CV-only build that excludes
  embeddings, Search analytics/API, Agent/UI/ingress, tracing, RT-VLM, and LLM
  inference should keep VIOS, RT-CV, Kafka, Elasticsearch, Redis, Kibana,
  Logstash, `kibana-init-container-search`, and required init/wait peers, while
  removing `rtvi-embed`,
  `vss-search-analytics-2d-fusion`, `vss-video-analytics-api-fusion`,
  `vss-agent`, `vss-ui`, `vss-haproxy-ingress`, `phoenix`, `rtvi-vlm`, and
  `llm_${LLM_MODE}_${LLM_NAME_SLUG}`. Set `ENABLE_CRITIC=false` when critique
  or RT-VLM is excluded.
- For that Search-derived RT-CV-only build, the effective profile set must
  include `kibana-init-container-search` and the generated delta must include
  `ENABLE_CRITIC=false`; do not omit either from the final proof.
- When a Search-derived RT-CV-only build asks for RT-DETR person detection,
  multi-object tracking, Kafka events, Elasticsearch storage, and VIOS
  retrieval, use this exact effective service profile set:
  `kibana-init-container-search,nvstreamer-2d-fusion,perception-2d-init,perception-2d-fusion,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms`.
  Do not keep `vss-video-analytics-api-fusion`; streamed and on-demand
  detection plus Kafka/Elasticsearch persistence are satisfied by RT-CV,
  VIOS/VST, Kafka, Logstash, Elasticsearch, and the init/wait peers without
  the VSS video analytics API service.
- For Search-derived ingestion + detection + embedding builds, keep
  `vss-search-analytics-2d-fusion` and `rtvi-embed` when indexing/search data
  is requested, but still remove `vss-video-analytics-api-fusion` unless the
  user explicitly asks for the analytics API. Do not retain Agent/UI/ingress,
  RT-VLM, Phoenix, or LLM NIM keys when the request excludes those user-facing
  orchestration layers.
- For Search-derived ingestion + detection + embedding builds, the effective
  profile set should retain these search storage/query peers exactly:
  `kibana-init-container-search`, `vss-search-analytics-2d-fusion`,
  `elasticsearch`, `elasticsearch-init-container`, `kibana`, `logstash`,
  `kafka`, `kafka-topic-init-container`, `redis`, and `rtvi-embed`, plus the
  RT-CV, VIOS, init, wait, and broker peers required by the Search Foundation.
  Do not exclude `kibana` or `kibana-init-container-search`; they are part of
  the checked-in Search composition even for generation-only builds.
- For that ingestion + detection + embedding build, use this exact effective
  service profile set:
  `kibana-init-container-search,vss-search-analytics-2d-fusion,nvstreamer-2d-fusion,perception-2d-init,perception-2d-fusion,elasticsearch,elasticsearch-init-container,kafka,kafka-topic-init-container,redis,kibana,logstash,broker-health-check,init-dirs,render-config,wdm-env-from-config,wait-for-redis,wait-for-docker-workloads,sdr-controller,rtvi-embed,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms`.
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
- For generation-only LVS builds, state those LVS endpoint and response details
  in both the architecture preview and the final proof even when no deployment
  or live API call is requested. Use an explicit "LVS API contract" block with
  separate lines for `GET /v1/ready`, `GET /models`, `POST /v1/summarize`, the
  VIOS-provided `url` or `id` request field, and
  `choices[0].message.content`. Do not describe `GET /models` only as an LLM
  or RT-VLM probe; it is also the LVS model-list endpoint for this summary API.
- In the LVS architecture preview, spell out that the `POST /v1/summarize`
  request accepts the VIOS-provided `url` or `id`; naming the endpoint without
  the media-reference field is incomplete proof.
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
- For generation-only evals, include an explicit proof checklist in both the
  architecture preview and final answer when the request matches one of these
  common compositions:
  - Dense captions: state that `vss-haproxy-ingress` is retained because it
    fronts VIOS/VST plus ten non-Agent HTTP backends and uses `init-addr none`;
    state that `RTVI_VLM_KAFKA_ENABLED=true` changes the `base` Foundation
    default from `false`; state that `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions`
    overrides RT-VLM's default topic `vision-llm-messages`; state that no
    custom Kafka topic definition is needed because `mdx-vlm-captions` is
    already in `kafka-topic-init-container`'s default `KAFKA_TOPICS`.
  - Search-derived RT-CV-only person detection: state the exact effective
    `COMPOSE_PROFILES`, name `kibana`, `logstash`, and
    `kibana-init-container-search` as retained search peers, and state that
    `vss-video-analytics-api-fusion` is excluded.
  - LVS summaries: state that `POST /v1/summarize` uses a VIOS-provided `url`
    or `id` and returns text in `choices[0].message.content`.
