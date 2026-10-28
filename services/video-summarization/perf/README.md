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
