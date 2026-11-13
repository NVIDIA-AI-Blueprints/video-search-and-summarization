# LVS performance benchmarks

This directory contains the performance benchmark package for the Long Video
Summarization (LVS) service. The runner starts one LVS Compose stack, waits for
it to become healthy, executes the selected benchmark scenarios, and optionally
tears the stack down.

## Prerequisites

- Docker with the Compose plugin
- NVIDIA Container Toolkit and the GPUs required by the selected Compose file
- `uv` for the host-side benchmark environment
- Access to the container images and models used by the selected configuration
- `ARTIFACTORY_USER` and `ARTIFACTORY_TOKEN` exported in the environment or
  configured in the generated Compose `.env` file

Run these commands from `services/video-summarization`.

## Setup

Create the Compose environment and shared media volume:

```bash
./perf/setup/scripts/setup.sh
```

Prepare the locked Python environment used by the benchmark client:

```bash
./perf/setup/setup_environment.sh
```

## Run

A Compose file is required. Names are resolved relative to
`perf/setup/compose/`; an absolute path is also accepted.

```bash
./perf/scripts/run_benchmark.sh \
  -f x86-rtvi-cr-nemo3-nano_4gpu.yaml \
  -s quick_test \
  -d
```

`-d` tears down the Compose stack when the benchmark finishes. Omit it to keep
the stack running for inspection.

Common examples:

```bash
# Run one scenario
./perf/scripts/run_benchmark.sh \
  -f x86-rtvi-cr-nemo3-nano_4gpu.yaml \
  -s single_file_test \
  -d

# Run multiple scenarios
./perf/scripts/run_benchmark.sh \
  -f x86-rtvi-cr-nemo3-nano_4gpu.yaml \
  -s single_file_test \
  -s file_burst_test \
  -d

# Use a different benchmark configuration and image
./perf/scripts/run_benchmark.sh \
  -f x86-rtvi-cr-nemo3-nano_4gpu.yaml \
  -c vss_sample_config.yaml \
  -I <lvs-image> \
  -d
```

Run the following command for all available options, including GPU assignment,
ports, release metadata, health timeout, and GPU overlays:

```bash
./perf/scripts/run_benchmark.sh -h
```

Benchmark configurations and scenario definitions are under `perf/configs/`.
Compose files and GPU overlays are under `perf/setup/compose/`.

## Tests

Run the offline performance-script tests with:

```bash
bash tests/perf/run_tests.sh
```

## Optional monitoring

The monitoring and OpenTelemetry Compose files attach to the network of an
already-running benchmark stack. Set `BENCHMARK_NETWORK` to that Docker network
before starting the monitoring stack:

```bash
export BENCHMARK_NETWORK=x86-rtvi-cr-nemo3-nano-2gpu_app-network
docker compose -f perf/setup/monitoring/compose.yaml up -d
```

Use `docker network ls` to find the network created for your Compose project.

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

### Common argument interface

The existing short options remain supported. The same entrypoint also accepts:

```bash
./scripts/run_benchmark.sh \
  --compose-file x86-rtvi-cr-nemo3-nano_2gpu.yaml \
  --config auto --scenario quick_test \
  --output-json ./results --config-id local-test --triggered-by manual
```

`--config auto` retains platform selection: Tegra configuration for Thor, Orin NX
and Spark; standard configuration for x86. An explicit `--config` overrides this.
Common invocation validates and pulls the resolved Compose stack after loading
`setup/configs/images.yaml`, writes canonical JSON under `results/vss-perf-results/`,
and tears down the deployment on exit. Set `IMAGE_TAG` to override the tested LVS
image. Existing short-option invocations retain their output and teardown defaults.

For common long-option invocations, the directory supplied through `--output-json`
is the run artifact root. Canonical reports remain in `vss-perf-results/`, native
benchmark logs and scenario outputs go into `native/`, and diagnostics stay in
`diagnostics/`. The `native/` directory must be absent or empty; existing results
are never deleted by the wrapper. Short-option invocations retain their output
paths. Image snapshots are saved after measurement so they do not interfere with
the native benchmark's empty-output-directory check.
