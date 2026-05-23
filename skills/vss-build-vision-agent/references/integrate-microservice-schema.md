# `integrate-<microservice>.md` — Canonical Schema

This is the schema that every per-service `integrate-<microservice>.md` file in the VSS repo must follow. The `build-vision-agent` skill reads these files as ground truth for service interfaces, dependencies, and Kafka/REST topology when composing deployments.

**Filename convention:** `integrate-<microservice>.md` where `<microservice>` is the service name in lowercase-kebab-case (e.g., `integrate-rt-vlm.md`, `integrate-vios.md`, `integrate-elk.md`).

**Location:** `skills/<skill-folder>/references/integrate-<microservice>.md`

---

## Required Sections (in order)

### `# Integration Reference: <Service Name>`

H1 title. The display name of the service (e.g., `# Integration Reference: RT-VLM`).

### `## Overview`

One paragraph describing what this service does and when to include it in a deployment. Should make capability-to-service matching unambiguous (e.g., "Use this service when the workflow requires real-time dense captioning of RTSP streams or stored video files.").

### `## Required Peer Services`

Bulleted list of services that must be running alongside this one. For each:

- **Service name** — e.g., Kafka, Elasticsearch, VIOS
- **Why it is needed** — one sentence
- **Minimum version** if applicable
- **Required vs. optional** — explicit. If the peer is only required for a specific feature flag, document the flag.

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

Reference files that fail validation block the PR via the CI workflow.
