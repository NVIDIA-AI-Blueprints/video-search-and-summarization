# Performance setup tools

`support_images.py` reads a service-owned `perf/setup/configs/images.yaml`.
It is a source-only setup helper, not an application dependency or an installed
Python package. It uses PyYAML from the existing isolated perf environment.

Each image has a stable logical ID and a complete `ref`. Multiple Compose services
can bind to the same ID. Architecture/model variants use separate IDs. The
`scopes` section binds files relative to a setup tree to Compose service images
or zero-based Dockerfile `FROM` stages. Internal Dockerfile stages stay unchanged.
Missing files, missing service images, malformed references, and symlink bindings
fail before deployment. No container commands are executed by this helper.

Service runners render private files and retain production deployment templates.
The benchmark's target image is deliberately absent from the support bindings;
the selected artifact remains authoritative. The `environment` section supports
existing VIOS/VST setup settings; explicit nonempty environment overrides win.

For example, from the repository root:

```bash
services/analytics/video-analytics-api/perf/setup/.venv/bin/python \
  libs/vss-perf-tools/support_images.py \
  --config services/analytics/video-analytics-api/perf/setup/configs/images.yaml \
  --scope infra --source deploy/docker/services/infra \
  --destination /tmp/my-api-perf-images/infra
```

`support-images.json` is a snapshot of configured references, not proof of image
approval or the immutable digests used by Docker. OSRB verification and switching
registries/versions are separate work. Existing mutable tags are preserved here.

RTVI Embed still reads an existing VST deployment package. Its known explicit
image overrides are configurable, but other package-defined references cannot be
inventoried until that package is inspected. This limitation is recorded in its
image configuration and README.

When blueprint dependencies change, review the corresponding service image file
and bindings together. Do not silently synchronize versions during a benchmark.
Run `pytest libs/vss-perf-tools/tests` in a perf test environment to verify all
configured source bindings before deployment.
