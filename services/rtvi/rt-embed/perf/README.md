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

## Hardware profiles

`configs/perf-configs.yaml` declares six enabled, single-GPU configurations.
An optional `benchmarkConfig` selects a profile relative to this `perf/` directory;
rows without it retain `configs/config.yaml`.

| Hardware | Image variant | Benchmark profile (`perf/` relative) |
|---|---|---|
| RTXPRO6000BW-SE | amd64 | `configs/config.yaml` |
| H100-SXM | amd64 | `configs/config.yaml` |
| RTXPRO4500BW | amd64 | `configs/config.yaml` |
| DGX-SPARK | arm64-sbsa | `benchmark/rtvi_embed_bcd_3_2_thor_config.yaml` |
| IGX-T7000 (IGX-THOR platform) | arm64-igpu | `benchmark/rtvi_embed_bcd_3_2_thor_config.yaml` |
| GB300 | arm64-sbsa | `configs/config.yaml` |

Spark and IGX use the source Thor/DGX workload, including its smaller non-streaming
and text concurrency sweep. GB300 retains the existing standard workload. These
are the existing Embed configurations; VLM's BCD 3.3 version does not apply here.

For a local run, select the matching profile explicitly with `--config` and use
its configuration ID. For example, on Spark after setting the variables above:

```bash
./perf/scripts/run_benchmark.sh \
  --config perf/benchmark/rtvi_embed_bcd_3_2_thor_config.yaml \
  --compose-file perf/setup/compose/compose.perf.yaml \
  --scenario warmup_test --config-id rtvi-embed-dgx-spark-1gpu \
  --vlm-gpus 0 --output-json perf-results/spark
```

Choose a service image compatible with the target architecture and model. The
configured image variant does not rewrite an explicit image reference or prove
runtime compatibility. New targets require native deployment and measurement
validation; the existing benchmark algorithms and supporting image tags are unchanged.
Select an existing scenario from the chosen profile before running its full suite.

Reports retain the selected profile, model, GPU assignment and configuration ID in
`metadata.config`, alongside the native measurements and test outcomes. No new
Python dependency is needed for hardware selection.

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
scripts. Both RTVI services stage the same repository-owned VIOS deployment
through `libs/vss-perf-tools/stage_vst.py`, including the NVStreamer configs,
startup scripts, video mounts and port configuration. Embed no longer downloads
the mutable VST package by default. Its videos, workload configuration and
measurement code remain unchanged.

The four public VIOS images remain pinned to `3.2.0`. PostgreSQL `17.10-alpine`
and Redis `8.10.0-alpine` match the repository deployment defaults and are
listed in `setup/configs/images.yaml`. An explicit `VST_LOCAL_PACKAGE` override
remains available for non-Thor setups; use a fresh `VST_DIR`.

VST and NVStreamer use separate Compose projects derived from the
`VST_COMPOSE_PROJECT` namespace: `<namespace>-vst` and
`<namespace>-nvstreamer`. Setup scopes the shared deployment helper’s Compose commands to
the selected component, so VST cleanup preserves the running NVStreamer.
Diagnostics cover both projects; normal and failure teardown stop both.

IGX Thor uses the same full, unsuffixed service image tag as AMD64, with an
ARM64-compatible image variant. Use the `arm64-igpu` artifact classifier for
IGX; Spark and GB300 keep `arm64-sbsa`. Supplying a full `-sbsa` image reference
still selects that exact image, so use separate artifact runs for IGX and the
SBSA platforms. This changes image selection only; workloads and model profiles
remain unchanged. Image/runtime compatibility still requires a hardware run.

### Thor runtime validation

Both RTVI services use the same Thor-only CUDA preflight before deploying VIOS.
It checks the actual image architecture, CUDA initialization and selected GPU
identity, and saves image IDs and errors in `vst-runtime.json` under
`PERF_DIAGNOSTICS_DIR` (or beside the benchmark configuration). The staged
Compose overrides apply only to this run’s VIOS containers. Other hardware
keeps its existing runtime settings.

The default runtime remains `nvidia`. If the node administrator provides a
separate Thor-compatible Docker runtime, `PERF_THOR_DOCKER_RUNTIME` selects it
for both VIOS and the RTVI container. This setting does not provision a runtime
or change the Docker daemon. A host with broken CUDA library/device injection
still requires node repair; the preflight reports that failure before deployment.
