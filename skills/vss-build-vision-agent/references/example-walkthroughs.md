# Streaming Dense Caption Walkthrough - Concrete Example

A worked example of the nine-step `build-vision-agent` flow for the **IN-1 streaming + on-demand dense captioning** profile.

User prompt:
> "Create a profile for streaming and on-demand video dense captioning. Streamed dense captions should be published to the kafka message bus and stored in elasticsearch."

Skill execution:

1. **Step 0 — Parse**: capability = "streaming + on-demand dense captioning, Kafka + ES storage". No existing deployment, no 3P descriptor. Output target = compose (default). Net-new.

2. **Step 1 — Catalog**: tag-match against `microservice-catalog.md`. Candidates: RT-VLM (`dense-captioning`, `streaming-inference`, `on-demand-inference`), VIOS (`rtsp-ingestion`, `video-upload`), ELK (`caption-storage`, `kafka-ingestion`). All required peers satisfiable from within the candidate set plus `kafka` from the foundational stack.

3. **Step 2 — Read integrate refs**: `integrate-rt-vlm.md`, `integrate-vios-service.md`, `integrate-elk.md`. Note: RT-VLM's `clip_storage` mount must be the same host path as VIOS's `${VST_VIDEO_STORAGE_PATH}`. RT-VLM publishes to `${RTVI_VLM_KAFKA_TOPIC}`. **VSS 3.2 ships with two Logstash pipelines** at `deploy/docker/services/infra/elk/logstash/pipelines/kafka/`: `mdx-logstash.conf` subscribes to `mdx-vlm-incidents` + `mdx-vlm-alerts` (alert path); `mdx-lvs-logstash.conf` subscribes to `mdx-vlm-captions` + `mdx-structured-events-summary` (caption path → indexes via via-ctx-rag schema). **For plain captions to reach ES, the skill must set `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions`** — the raw compose default `vision-llm-messages` and the pre-3.2 legacy `mdx-vlm` are both unsubscribed.

4. **Step 3 — Conflicts**: none for net-new. Note that VIOS uses `network_mode: host` while RT-VLM uses default bridge — wiring crosses through `${HOST_IP}:9092` for Kafka and a shared volume for video.

5. **Step 4 — Proposal**:
   - Services to add: VIOS (4 containers), RT-VLM (1 container + sibling NIM), ELK (4 containers), Kafka (foundational), Redis (foundational), `broker-health-check`.
   - Sibling VLM NIM choice: `cosmos-reason2-8b` by default. Confirm or ask.
   - GPU assignment: RT-VLM on GPU 0; sibling NIM on GPU 0 (`local_shared`) or GPU 1 (`local`). Ask.
   - Shared ES: single instance (default).
   - Caption topic: emit `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions` (the VSS-3.2 upstream-subscribed topic). Do NOT use the raw compose default `vision-llm-messages` (unsubscribed) or the pre-3.2 `mdx-vlm` (also unsubscribed). Caption docs will land in ES indices named `<collection>_<id>` per the via-ctx-rag schema, not in `mdx-vlm-*` date indices.

6. **Step 5 — Read deploy refs**: `deploy-rt-vlm.md`, `deploy-vios-service.md`, `deploy-elk.md`. Validate host has GPU with ≥ 16 GB VRAM for the cosmos-reason2-8b backend.

7. **Step 6 — Generate**: write `<BUILD_DIR>/compose.yml` plus per-service includes, `.env.template`, `MANIFEST.md`, and `<BUILD_DIR>/allow-list.yml` (the per-generation union from Step 4). Invent a fresh flag (e.g. `bp_developer_in_1`) and record it under `flag:` in `allow-list.yml`; pass it as `--profile <flag>` at deploy time. Step 6.5 will copy the upstream `rtvi-vlm`, VIOS, **SDRC** (`services/infra/sdrc/docker-compose.yaml`), and foundational ELK/Kafka composes into `<BUILD_DIR>/patched/` and add the flag to every service-key in `allow-list.yml`; every other service in those files stays unmodified, and upstream files stay untouched. Patch 3 additionally materializes the SDRC config templates (`config.yml.tmpl` + `docker_cluster_config-streamprocessing.json.tmpl`) under `<BUILD_DIR>/sdrc/configs/configs/` (sourced from `developer-profiles/dev-profile-alerts/sdrc/2d_vlm/configs/`) and sets `SDR_CONTROLLER_CONFIG_PATH=<BUILD_DIR>/sdrc/configs` in the build-output `.env` so the SDRC chain can render. Bundle the `vss-manage-video-io-storage/` and `vss-deploy-dense-captioning/` skills from `<vss-repo>/skills/` into `build-output/skills/` (ELK references already live inside `vss-build-vision-agent/references/`). Scan `<vss-repo>/skills/` for a use-case skill matching "streaming dense captioning" and bundle if present, otherwise skip. Generate `build-output/skills/deploy-<flag-slug>/SKILL.md` with the exact compose path, env path, RT-VLM `1200s` cold-boot window, GPU assignment, healthcheck loop, and bring-up / tear-down commands hardcoded.

8. **Step 7 — Dry-run**: `docker compose --env-file .env.template config > resolved.yml` and confirm no `${...}` remain.

9. **Step 8 — Review and prompt to deploy**: present summary including bundled skills and the generated `deploy-in-1` skill; on confirmation, write to `./build-output/`. Then ask "Deploy this profile now? [y/N]" — on `y`, verify `.env` is filled in and invoke `deploy-in-1`; on `n`, print the bring-up command.
