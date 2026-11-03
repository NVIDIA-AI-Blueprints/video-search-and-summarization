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

Edit `setup/configs/images.yaml` to manage this benchmark's supporting images.
`images` contains complete image references; `scopes` binds them to Compose
services and Dockerfile build stages. Keep logical image IDs stable when changing
versions. The service being tested continues to use the image selected for the run.

Setup renders image choices into private runtime files. It does not modify the
shared blueprint deployment definitions. The configured inventory is saved as
`support-images.json` alongside the run output. These references preserve existing
image versions; their presence here does not establish OSRB approval.

The `environment` section maps VIOS/VST image settings to image IDs. Explicit
existing environment overrides retain their previous precedence.
