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
`nvcr.io/nvidia/vss-core/vss-vios-*` images tagged `3.3.0` and stages the
repository-owned VIOS deployment through `scripts/stage_vst.py`. Its PostgreSQL
`17.10-alpine` and Redis `8.10.0-alpine` defaults match
`services/vios/deployment/stream-processing/docker-compose/compose.env`.
The previous image references remain commented in `setup/configs/images.yaml`.
Keep the public VIOS exception when synchronizing source image tags. It changes supporting
deployment selection, while measurement and latency calculations remain in the
native benchmark implementation.
