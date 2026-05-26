# `integrate-<microservice>.md` — Canonical Schema

This is the schema that every per-service `integrate-<microservice>.md` file in the VSS repo must follow. The `build-vision-agent` skill reads these files as ground truth for service interfaces, dependencies, and Kafka/REST topology when composing deployments.

**Filename convention:** `integrate-<microservice>.md` where `<microservice>` is the service name in lowercase-kebab-case (e.g., `integrate-rt-vlm.md`, `integrate-vios-service.md`, `integrate-elk.md`). Some upstream skill folders use a `-service` suffix (e.g. VIOS ships `integrate-vios-service.md` + `deploy-vios-service.md` to match its existing `-service.md` convention) — honor the upstream naming when consuming a microservice's reference files.

**Location:** `skills/<skill-folder>/references/integrate-<microservice>.md`

---

## Required Sections (in order)

### `# Integration Reference: <Service Name>`

H1 title. The display name of the service (e.g., `# Integration Reference: RT-VLM`).

### `## Overview`

One paragraph describing what this service does and when to include it in a deployment. Should make capability-to-service matching unambiguous (e.g., "Use this service when the workflow requires real-time dense captioning of RTSP streams or stored video files.").

### `## Required Peer Services`

Two parts in this section. The **prose part** is for humans; the **`component_services:` YAML block** is structured input the `build-vision-agent` skill consumes.

**Prose — peer microservices (cross-skill dependencies).** Bulleted list of OTHER VSS microservices that must be running alongside this one. For each:

- **Service name** — e.g., Kafka, Elasticsearch, VIOS
- **Why it is needed** — one sentence
- **Minimum version** if applicable
- **Required vs. optional** — explicit. If the peer is only required for a specific feature flag, document the flag.

**Structured — `component_services:` block.** A fenced YAML code block declaring the upstream compose service-keys (the `services:` keys in `deploy/docker/...`) that THIS microservice brings into a deployment when selected. The skill unions these blocks across the candidates it confirms in Step 4 to produce the per-generation allow-list that Step 6.5 uses for `bp_developer_*` flag insertion and `depends_on` strip-vs-keep decisions.

Schema:

```yaml
component_services:
  # Service-keys always added when this microservice is selected.
  always:
    - key: <compose-service-key>                # required field
      compose: <path/to/upstream/compose.yaml>  # relative to deploy/docker/, for traceability
      role: <one-line description>              # for the architecture proposal in Step 4
      # Any depends_on peers the skill should treat as required-by-this-microservice
      # so it never strips them even if they're not in another microservice's
      # component_services. Leave empty when the upstream depends_on annotations
      # (required: false on optional peers, no flag on hard requirements) suffice.
      required_peers: []
    - key: <compose-service-key>
      ...

  # Decisions the skill must resolve in Step 4 before producing the allow-list.
  # Each `variant` is a named choice with N options; one option is the default.
  variants:
    - name: <decision-id>                       # e.g., "sensor_topology"
      prompt: <human-readable question>          # e.g., "Live RTSP cameras or uploaded files?"
      default: <option-name>                     # used when caller passes accept-defaults
      options:
        - name: <option-name>
          when: <one-line user-intent matcher>   # e.g., "user prompts live camera or RTSP"
          add:
            - key: <compose-service-key>
              compose: ...
              role: ...
              required_peers: [...]
        - name: <other-option>
          when: ...
          add: [...]
```

Rules:

1. **Every entry under `always.add[*]` and `variants.options[*].add[*]` must name an upstream compose service-key that exists in the cited compose file.** The skill validates this at load time.
2. **`required_peers:` lists per-key allow-list members the skill MUST keep in the union even if no other microservice declares them.** Use sparingly — `broker-health-check` on `rtvi-vlm` is a legitimate case (RT-VLM's compose has it in `depends_on: required: false`, but it's defined in `services/infra/compose.yml` which lives in ELK's purview, not RT-VLM's). When `broker-health-check` is in ELK's `always`, leave RT-VLM's `required_peers` empty for it.
3. **Variants are only resolved in Step 4** — the skill asks the user (or accepts a default in non-interactive mode), then commits one option per variant into the per-generation allow-list. Variants the user does not need are dropped.
4. **`when:` is a hint** for the skill's prompt-classifier — it does NOT guarantee the option is picked. Step 4 still asks the user when the choice is non-obvious.

Example (`integrate-elk.md`):

```yaml
component_services:
  always:
    - key: elasticsearch
      compose: services/infra/compose.yml
      role: caption / metadata index store
    - key: elasticsearch-init-container
      compose: services/infra/compose.yml
      role: creates ILM policies + index templates
    - key: kafka
      compose: services/infra/compose.yml
      role: message bus producers publish to and Logstash consumes from
    - key: kafka-topic-init-container
      compose: services/infra/compose.yml
      role: creates canonical mdx-* topics before Logstash starts
    - key: redis
      compose: services/infra/compose.yml
      role: alternate transport for Logstash (STREAM_TYPE=redis); also used by VIOS SDR
    - key: kibana
      compose: services/infra/compose.yml
      role: dashboard UI
    - key: logstash
      compose: services/infra/compose.yml
      role: Kafka/Redis → Elasticsearch pipeline (mdx-kafka + mdx-lvs pipelines)
    - key: broker-health-check
      compose: services/infra/compose.yml
      role: init gate ensuring Kafka/Redis is responsive before downstream services
    - key: phoenix
      compose: services/infra/compose.yml
      role: OTel sink used by all dev-profile-base services
```

Example (`integrate-vios-service.md` — sketches the `sensor_topology` variant; see also the authoritative schema in `component-services-schema.md`, which uses a flat list-of-entries form with embedded `variants:` blocks rather than the `always:` / `variants:` form sketched below):

```yaml
component_services:
  always:
    - key: centralizedb
      compose: services/vios/foundational/docker-compose.yaml
      role: VIOS Postgres for sensor + stream metadata
    - key: vst-ingress
      compose: services/vios/foundational/docker-compose.yaml
      role: nginx ingress on :30888 — unified VIOS API surface
  variants:
    - name: sensor_topology
      prompt: "How does the deployment ingest video — live RTSP cameras (Topology A) or uploaded file replays (Topology B)?"
      default: rtsp_quartet
      options:
        - name: rtsp_quartet
          when: user prompts mention live camera, RTSP, or streaming inference
          add:
            - key: sensor-ms
              compose: services/vios/initiator/docker-compose.yaml
              role: dev-profile-base RTSP sensor variant
            - key: streamprocessing-ms
              compose: services/vios/sdr/streamprocessing/docker-compose.yaml
              role: dev-profile-base streamprocessing variant
            - key: sdr-streamprocessing
              compose: services/vios/sdr/streamprocessing/docker-compose.yaml
              role: WDM agent that drives streamprocessing-ms (mandatory; see VIOS quartet finding)
            - key: envoy-streamprocessing
              compose: services/vios/sdr/streamprocessing/docker-compose.yaml
              role: L7 proxy sensor-ms calls on localhost:10000 for /sensor/add
        - name: file_driven
          when: user prompts mention sample / on-disk files only (no live camera)
          add:
            - key: nvstreamer-alerts
              compose: developer-profiles/dev-profile-alerts/compose.yml
              role: file-driven RTSP server — replaces the RTSP quartet
```

The structured block lives alongside the prose; the prose stays focused on cross-microservice integration concerns (Kafka topic conventions, schema requirements, etc.).

### `## Integration Interfaces`

#### `### Inputs`

How this service receives data. For each input:

- **Method** — REST API endpoint, Kafka topic consumed, RTSP stream, file path, gRPC, etc.
- **Address / topic / endpoint** — concrete identifier (e.g., `POST /v1/generate_captions_alerts`, `kafka topic: rtvi.cv.detections`)
- **Expected schema** — JSON schema, Protobuf descriptor reference, or "see API Schema section"
- **Authentication** — Bearer token, none, mutual TLS, etc.

#### `### Outputs`

How this service publishes data. For each output:

- **Method** — Kafka topic produced, REST response, webhook callback, file write
- **Topic / endpoint / path** — concrete identifier
- **Schema** — payload shape, with reference to protobuf descriptor or JSON schema
- **Frequency / trigger** — per-request, per-frame, per-chunk, on event

### `## API Schema`

Key request/response schemas for the service's public API. Either:
- Reference an external OpenAPI spec by URL or repo path, OR
- Embed the critical schemas inline as annotated JSON/YAML

If the service does not expose a REST API, write `Not applicable — this service has no REST surface; see Integration Interfaces above for Kafka topic schemas.`

### `## Environment Variables`

Table of all environment variables the service consumes. Columns:

| Variable | Purpose | Default | Required? |
|---|---|---|---|

For variables that are rewritten at the compose boundary (host name → container name), document both names and the rewrite.

### `## Network Requirements`

- **Ports exposed** — host:container pairs and protocol
- **Inbound traffic** — from where (other services, host, external)
- **Outbound traffic** — what hosts/services this service must reach
- **DNS / hostname assumptions** — e.g., "expects `kafka` resolvable on the compose network", or "uses `${HOST_IP}:9092` because Kafka is on host networking"
- **`network_mode`** — bridge, host, or other

### `## Known Integration Constraints`

Anything non-obvious that affects how this service can be wired:

- Startup ordering requirements (`depends_on` conditions)
- Single-instance restrictions (e.g., hardcoded `container_name`)
- Limitations on parallelism or concurrency
- Schema-version pinning requirements between this service and its peers
- Known protocol mismatches with otherwise-compatible peers

### `## Example Compose Snippet`

A minimal but complete `services:` block showing how this service is wired in compose, including:

- `image:` line (or `build:` reference)
- `environment:` block with the minimum required variables
- `ports:` mapping
- `volumes:` if any are required
- `healthcheck:` if defined
- `depends_on:` showing peer-service dependencies
- `profiles:` if profile-gated

---

## Optional Sections

These can be added below the required sections when relevant:

- `## Authentication & Authorization` — if the service has a non-trivial auth model
- `## Rate Limits & Quotas` — if the service enforces caller-side limits
- `## Schema Compatibility` — when this service's input/output schema must align with a specific peer's schema (e.g., RT-VLM caption protobuf schema must match what Logstash decodes)
- `## Test / Smoke Hooks` — known endpoints or topics for verifying the service is wired correctly

---

## Validation Rules

The `validate-references.py` script in the `build-vision-agent` skill enforces:

1. The file's H1 starts with `# Integration Reference: `.
2. All required sections above exist as H2 / H3 headings in the listed order.
3. Required-section bodies are non-empty.
4. The Environment Variables table has the four required columns.
5. The Example Compose Snippet block is fenced as ` ```yaml ` and parses as valid YAML.
6. `§ Required Peer Services` contains a fenced ` ```yaml ` block whose top-level key is `component_services:` and whose content matches the schema above (at least one entry under `always:` OR `variants:`; every `key:` resolves to a service defined in the cited `compose:` file).

Reference files that fail validation block the PR via the CI workflow.
