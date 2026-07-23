# Patch Reference: Long Video Summarization (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the
orchestrator needs to add Long Video Summarization (LVS) to an existing generated
profile: the `component_services:` block, the Step 6.5 patch specifics, and the
stored-video API smoke tests. It is NOT the LVS microservice contract.

For the underlying LVS API, env vars, ports, and deployment constraints, read
the skill-neutral pair files in the LVS skill:

- `skills/vss-summarize-video/references/integrate-lvs-service.md` - LVS integration contract for VIOS-stored or uploaded video summarization through `POST /v1/summarize`.
- `skills/vss-summarize-video/references/deploy-lvs-service.md` - LVS deployment contract: image, peers, storage, startup, verify, tear-down.
- `skills/vss-summarize-video/references/video-summarization-api.md` - detailed API reference.

Schema for the `component_services:` block is in `references/component-services-schema.md`; the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is `references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 1** tag-matches prompts such as "add summarization to this profile",
  "summarize uploaded videos", "summarize VIOS recordings", and "return the
  summary through the API" against the LVS catalog tags.
- **Step 2 / Step 4** read the `component_services:` block below to learn the
  upstream compose service key LVS owns and the optional LLM placement variants.
  The skill unions this block with the other selected or reused profile services
  and writes the flat allow-list to `<BUILD_DIR>/allow-list.yml`.
- **Step 6.5** reads ONLY the resulting sidecar and applies the patches below to
  the patched copies under the build directory's `patched/` tree.

## component_services block

LVS owns only the `lvs` service. VIOS, RT-VLM, Kafka, Elasticsearch, and
Logstash are peers owned by other catalog entries and should be reused when the
target profile already has them. The optional LLM NIM variant is included only
when the deployment uses a local LLM for LVS event merging or request handling;
remote LLM mode adds no compose service key.

```yaml
component_services:
  # LVS REST API for stored/uploaded video summarization.
  - key: lvs
    file: services/video-summarization/docker/deploy/compose.yaml
    role: Long Video Summarization API on :38111; summarizes VIOS-stored or uploaded video through POST /v1/summarize and returns choices[0].message.content.
    required: true

  # Optional LLM backend for LVS. Select exactly one local case if local LLM is
  # requested; omit the variant for remote LLM endpoints already supplied by env.
  - variants:
      key: lvs_llm_placement
      required: false
      cases:
        local:
          - key: nvidia-nemotron-nano-9b-v2
            file: services/nim/nvidia-nemotron-nano-9b-v2/compose.yml
            role: Nemotron Nano 9B v2 LLM NIM on a dedicated GPU for LVS request/event merging.
        local-shared:
          - key: nvidia-nemotron-nano-9b-v2-shared-gpu
            file: services/nim/nvidia-nemotron-nano-9b-v2/compose.yml
            role: Nemotron Nano 9B v2 LLM NIM sharing a GPU with the VLM for LVS request/event merging.
        remote: []
```

> **Add-on behavior.** LVS is an extension to another generated profile, not a
> replacement for that profile. When the user asks to add summarization to base,
> dense-captioning, alerts, search, or a customer profile, preserve the existing
> services and add `lvs`; reuse VIOS, RT-VLM, Kafka, Elasticsearch, and
> Logstash if they are already present. If any required peer is missing and can
> be added from the catalog, propose adding that peer. If a peer is missing and
> has no reference files, report the gap instead of generating a partial compose.

## Stored-video-only scope

For the current build-agent LVS add-on, video summarization is limited to media
uploaded to VIOS or stored/recorded by the VIOS recorder. The generated
architecture and deploy skill MUST use the LVS REST API:

- `GET http://<host>:38111/v1/ready`
- `GET http://<host>:38111/models`
- `POST http://<host>:38111/v1/summarize`

Do not use or recommend these endpoints for this add-on unless the user
explicitly asks for live stream summarization in a future request:

- `POST /v1/generate_captions`
- `POST /v1/stream_summarize`

The presence of RT-VLM in the graph does not make this a live-stream
summarization profile. RT-VLM is the VLM backend that LVS calls while processing
the VIOS-provided file or clip source.

## Patch specifics (Step 6.5)

Applied to patched copies under `<BUILD_DIR>/patched/`; the upstream tree is
never modified.

### Patch 0 - extract only allow-listed LVS services

`services/video-summarization/docker/deploy/compose.yaml` is the source-of-truth
compose for LVS, but it also carries self-contained development siblings such as
Elasticsearch, Kafka, Logstash, and readiness helpers. In build-vision-agent
profiles, those peers are owned by other catalog entries and are usually already
present in the target profile. Do not activate the whole source compose as an
LVS component.

When the allow-list includes `lvs` from this file:

- Keep and patch the `lvs` service block.
- Do not allow this copied file to introduce duplicate `elasticsearch`, `kafka`,
  or `logstash` services when those peers are already contributed by ELK/shared
  infrastructure entries.
- Remove or profile-gate non-allow-listed sibling service blocks from the
  patched copy unless they are explicitly listed in `<BUILD_DIR>/allow-list.yml`.
- If a removed sibling was a `depends_on` target of `lvs` (for example
  `wait-for-elasticsearch` or `wait-for-llm`), remove that `depends_on` edge and
  rely on the generated deploy skill's readiness waits before smoke testing.
- Keep direct peer references such as `elasticsearch` only when that service key
  is defined elsewhere in the generated include graph.

Record every removed or deactivated sibling service in `PATCHES.md`.

### Patch 1 - invented flag

The upstream `lvs` block in `services/video-summarization/docker/deploy/compose.yaml`
is the source-of-truth LVS service for generated builds. It may not carry the
same dev-profile flag shape as wrapper composes. Step 6.5 appends the
per-generation invented flag (for example `bp_developer_an_1`) to `lvs` in the
patched copy, preserving any upstream profile list if present. If a local LLM
NIM is selected, append the same flag to exactly one selected LLM service key in
`services/nim/nvidia-nemotron-nano-9b-v2/compose.yml`. Preserve all upstream
profile flags.

### Patch 2 - strip undefined optional peers

The upstream `lvs` block declares peer `depends_on` entries for Elasticsearch,
LLM readiness, and similar support services. Wrapper composes may also declare
optional LLM and VLM NIM peers. The generalized Patch 2 rule strips whichever
optional peers are undefined in the patched include graph and keeps defined
peers:

- Keep `rtvi-vlm` when RT-VLM is selected or reused for the generated profile.
- Keep the selected local LLM NIM key when `lvs_llm_placement` is `local` or
  `local-shared`.
- Strip all other sibling NIM peers unless they are explicitly selected by a
  separate catalog entry.

### Patch 3 - materialize LVS config bind mount

`lvs` bind-mounts the LVS config file. In the source-of-truth compose the
relative mount is:

```text
../../config/config.yaml:/opt/nvidia/via/config/default_config.yaml:ro
```

Because the patched copy preserves the upstream relative path under
`<BUILD_DIR>/patched/services/video-summarization/docker/deploy/compose.yaml`,
Patch 3 must materialize the sibling source file at
`<BUILD_DIR>/patched/services/video-summarization/config/config.yaml` or rewrite
the bind source to another real file. In either case, the generated manifest
must state which source is used. Do not let Docker create an empty directory at
the config path; that produces confusing LVS startup failures.

## Env overrides the skill applies

When LVS is selected, the generated `.env.template` and `.env` must include the
LVS API and backend values needed for stored-video summarization:

- `LVS_BACKEND_URL=http://${HOST_IP}:38111`
- `LVS_IMAGE=nvcr.io/nvidia/vss-core/vss-video-summarization` and an appropriate release `LVS_TAG`, or a patched `image:` value matching the source-of-truth LVS compose. Do not let an unrelated generic image variable leak into `lvs`.
- `LVS_ENABLE_MCP=false`
- `LVS_DATABASE_BACKEND=elasticsearch_db`
- `KAFKA_ENABLED=true`
- `KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092`
- `KAFKA_STRUCTURED_SUMMARY_TOPIC=mdx-structured-events-summary`
- `LVS_ENABLE_LLM_MERGING=true`
- `RTVI_VLM_URL=http://${HOST_IP}:${RTVI_VLM_PORT}`
- `RTVI_VLM_BASE_URL=http://${HOST_IP}:8018`
- `RTVI_VLM_MODEL_TO_USE=cosmos-reason2`
- `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions`
- `VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208` unless `GET /v1/models` advertises a different id
- `LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2` and `LLM_NAME_SLUG=nvidia-nemotron-nano-9b-v2` when a local Nemotron LLM is selected

If the target profile already defines compatible values, preserve them. If the
values conflict, surface the conflict in Step 4 rather than silently overwriting.

## Generated deploy-skill smoke test

When LVS is selected, the generated per-deployment deploy skill MUST include a
stored-video summarization smoke test:

1. Verify LVS readiness: `curl -sf http://${HOST_IP}:38111/v1/ready`.
2. Verify the model list: `curl -sf http://${HOST_IP}:38111/models` and use an
   advertised model id.
3. Upload a warehouse MP4 to VIOS or select an existing VIOS-recorded clip and
   obtain an HTTP-reachable file or clip URL through VIOS.
4. Call `POST http://${HOST_IP}:38111/v1/summarize` with `model`, `scenario`,
   `events`, and either `url` or `id`.
5. Assert the response has `.choices[0].message.content` and that the content is
   non-empty summary JSON or text.

The generated smoke test MUST NOT use `/v1/stream_summarize` or
`/v1/generate_captions` as the summarization output path for this add-on.

## Emitted shape

The patched `lvs` block is `include:`d from `<BUILD_DIR>/compose.yml`.
Deploy with:

```bash
docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile <invented-flag> up -d
```

The operator calls the summarization API at:

```text
http://<host>:38111/v1/summarize
```
