# Performance setup tools

`support_images.py` loads a service-owned `perf/setup/configs/images.yaml`.
It validates and exports image variables; it does not copy or rewrite Compose
files or Dockerfiles. This source-only helper uses PyYAML from the existing
isolated perf environment and adds no application dependency.

```yaml
schema_version: 1
images:
  PERF_KAFKA_IMAGE: confluentinc/cp-kafka:8.3.0
  PERF_ELASTICSEARCH_BASE_IMAGE: docker.elastic.co/elasticsearch/elasticsearch:9.4.4
```

Perf-owned Compose files refer directly to required image variables:

```yaml
services:
  kafka:
    image: ${PERF_KAFKA_IMAGE:?Load the performance image configuration}
```

Shared blueprint Compose files retain their existing deployment default:

```yaml
image: ${PERF_KAFKA_IMAGE:-confluentinc/cp-kafka:8.3.0}
```

For locally built services, Compose forwards named base-image build arguments.
Shared Dockerfiles retain their original base as the ARG default, so a deployment
without perf variables uses the same base image as before:

```dockerfile
ARG PERF_ELASTICSEARCH_BASE_IMAGE=docker.elastic.co/elasticsearch/elasticsearch:9.4.4
FROM ${PERF_ELASTICSEARCH_BASE_IMAGE}
```

The normal service benchmark entrypoint loads its file automatically. For manual
Compose use, load it from the repository root using that service's perf Python:

```bash
image_environment="$(services/analytics/video-analytics-api/perf/setup/.venv/bin/python \
  libs/vss-perf-tools/support_images.py \
  --config services/analytics/video-analytics-api/perf/setup/configs/images.yaml \
  --shell)" && eval "$image_environment"
```

Explicit nonempty environment overrides win and are validated. The optional
`--snapshot <path>` writes the effective image variables to JSON. Neither config
nor snapshot contains credentials. The target artifact is deliberately excluded
from the supporting image file and stays controlled by the benchmark runner.

Image approval, registry migration, and tag upgrades are separate work. Existing
mutable tags remain unchanged. Snapshots record references, not Docker's resolved
image digests. RTVI Embed's downloaded VST package remains a separate unverified
inventory; its four explicit image overrides are configured in the same flat map.

Run `pytest libs/vss-perf-tools/tests` in a perf test environment to validate the
loader, deployment defaults, build-argument forwarding, and Compose interpolation.
Compose tests need the Docker Compose CLI but do not start containers or pull images.
