# RTVI Embed performance benchmarks

The native measurement implementation is under `benchmark/`.
`scripts/run_benchmark.sh` is the stable service entrypoint and `setup/`
contains deployment assets and the locked host environment.

Run one local scenario from the service directory:

```bash
export IMAGE_TAG=ghcr.io/nvidia-ai-blueprints/vss/vss-rt-embed:develop-latest
export NGC_API_KEY=<key>
export ARTIFACTORY_USER=<user>
export ARTIFACTORY_TOKEN=<token>
export PERF_UV_BIN=uv
./perf/scripts/run_benchmark.sh --config perf/configs/config.yaml \
  --compose-file perf/setup/compose/compose.perf.yaml \
  --scenario warmup_test --config-id local-rtvi-embed \
  --vlm-gpus 0 --output-json perf-results
```

Dashboard JSON is written below `perf-results/vss-perf-results/`; native
reports are kept below `perf-results/native/`. See
`benchmark/PERF_GUIDE.RTVI_EMBED.md` for scenario and tuning guidance.

## Supporting container images

Edit `setup/configs/images.yaml` to select this benchmark's supporting images.
It contains a flat map of image variable names to complete references:

```yaml
schema_version: 1
images:
  PERF_PROMETHEUS_IMAGE: prom/prometheus:latest
```

Use the variable names already present in the service's file. The benchmark runner
loads and validates them before deployment; no separate image setup command is
needed. Explicit nonempty environment overrides take precedence. Clear an override
to use the file's value.

Compose files refer directly to these variables. Shared Dockerfiles receive base
images through build arguments and retain their existing defaults for ordinary
blueprint deployments. Image selection does not copy or rewrite those templates.
The service under test still uses the artifact selected for the run.

`support-images.json` records the effective support image variables alongside the
run output. Image values preserve existing references and do not certify OSRB
approval or record the immutable digests pulled by Docker.

The VST image settings retain the variable names expected by the native setup
scripts. Native package staging, port configuration, and compatibility handling
remain unchanged.

Embed still consumes its existing VST package. Only its four explicit VST image
overrides and perf monitoring images are inventoried here. Any other package
references remain package-managed until the package contents are verified.
