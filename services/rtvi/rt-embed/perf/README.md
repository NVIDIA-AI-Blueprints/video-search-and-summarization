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
| IGX-T7000 (IGX-THOR platform) | arm64-sbsa | `benchmark/rtvi_embed_bcd_3_2_thor_config.yaml` |
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
scripts. Native package staging, port configuration, and compatibility handling
remain unchanged.

Embed still consumes its existing VST package. Only its four explicit VST image
overrides and perf monitoring images are inventoried here. Any other package
references remain package-managed until the package contents are verified.

VST and NVStreamer use separate Compose projects derived from the
`VST_COMPOSE_PROJECT` namespace: `<namespace>-vst` and
`<namespace>-nvstreamer`. Setup scopes the staged package's Compose commands to
the selected component, so VST cleanup preserves the running NVStreamer.
Diagnostics cover both projects; normal and failure teardown stop both.
