# RTVI VLM performance benchmarks

The native measurement implementation is under `benchmark/`.
`scripts/run_benchmark.sh` is the stable service entrypoint and `setup/`
pins the host dependencies.

Run one local scenario from the service directory:

```bash
export IMAGE_TAG=ghcr.io/nvidia-ai-blueprints/vss/vss-rt-vlm:develop-latest
export NGC_API_KEY=<key>
export PERF_UV_BIN=uv
./perf/scripts/run_benchmark.sh --config perf/configs/config.yaml \
  --compose-file perf/setup/compose/compose.perf.yaml \
  --scenario e2e_latency_1_token_2k --config-id local-rtvi-vlm \
  --vlm-gpus 0 --output-json perf-results
```

Dashboard JSON is written below `perf-results/vss-perf-results/`; native
reports are kept below `perf-results/native/`. See
`benchmark/PERF_GUIDE.RTVI_VLM.md` for scenario and tuning guidance.

## Hardware profiles

`configs/perf-configs.yaml` declares six enabled, single-GPU configurations.
An optional `benchmarkConfig` selects a profile relative to this `perf/` directory;
rows without it retain `configs/config.yaml`.

| Hardware | Image variant | Benchmark profile (`perf/` relative) | Model preset |
|---|---|---|---|
| RTXPRO6000BW-SE | amd64 | `configs/config.yaml` | cr3-super-reasoner-nvfp4 |
| H100-SXM | amd64 | `benchmark/rtvi_vlm_bcd_3_3_h100_sxm_config.yaml` | cr3-super-reasoner-fp8 |
| RTXPRO4500BW | amd64 | `benchmark/rtvi_vlm_bcd_3_3_rtx_pro_4500_config.yaml` | cr3-nano-reasoner-nvfp4 |
| DGX-SPARK | arm64-sbsa | `benchmark/rtvi_vlm_bcd_3_3_dgx_spark_config.yaml` | cr3-nano-reasoner-nvfp4 |
| IGX-T7000 (IGX-THOR platform) | arm64-sbsa | `benchmark/rtvi_vlm_bcd_3_3_agx_thor_t5000_config.yaml` | cr3-nano-reasoner-nvfp4 |
| GB300 | arm64-sbsa | `benchmark/rtvi_vlm_bcd_3_3_gb300_config.yaml` | cr3-super-reasoner-nvfp4 |

All selections use BCD 3.3 workloads. The AGX Thor T5000 profile is the
owner-approved initial workload for IGX-T7000; it is not a claim of measured IGX
capacity. Preserve the selected profile's search seeds, model and workload values.

For a local run, select the matching profile explicitly with `--config` and use
its configuration ID. For example, on Spark after setting the variables above:

```bash
./perf/scripts/run_benchmark.sh \
  --config perf/benchmark/rtvi_vlm_bcd_3_3_dgx_spark_config.yaml \
  --compose-file perf/setup/compose/compose.perf.yaml \
  --scenario e2e_latency_1_token_2k --config-id rtvi-vlm-dgx-spark-1gpu \
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

### Source image versions and the public VIOS exception

Monitoring image tags match `docker/rtvi_vlm/deploy/compose.perf.yaml` in
`rtvi-microservices` at commit `5f1eb00f89aa70d0bc1500c28e8ceeb886988caf`.

The public VIOS stack is an intentional integration exception. The original
setup script defaults to private `nvcr.io/rxczgrvsg8nx/vst-dev` images tagged
`2.1.0-26.04.1`; this benchmark retains the public
`nvcr.io/nvidia/vss-core/vss-vios-*` images tagged `3.2.0` and stages the
repository-owned VIOS deployment through `scripts/stage_vst.py`. Its PostgreSQL
`17.10-alpine` and Redis `8.10.0-alpine` defaults match
`services/vios/deployment/stream-processing/docker-compose/compose.env`.
The supporting image references are defined in `setup/configs/images.yaml`.
Keep the public VIOS exception when synchronizing source image tags. It changes supporting
deployment selection, while measurement and latency calculations remain in the
native benchmark implementation.
