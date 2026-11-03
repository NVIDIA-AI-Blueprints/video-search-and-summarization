# RTVI VLM performance benchmarks

The native measurement implementation is under `benchmark/`.
`scripts/run_benchmark.sh` is the stable service entrypoint and `setup/`
pins the host dependencies.

The enabled hardware configurations are `rtvi-vlm-rtxpro6000bw-se-1gpu`
(`amd64`) and `rtvi-vlm-dgx-spark-1gpu` (`arm64-sbsa`). When using the standard
`configs/config.yaml`, the Spark configuration ID selects the existing native
DGX-SPARK BCD 3.3 profile: `cr3-nano-reasoner-nvfp4` and its Spark concurrency
settings. The RTX configuration retains its current profile. Explicit custom
`--config` files take precedence over this selection.

For Spark, supply an ARM/SBSA-compatible tested image (for example the
`develop-latest-sbsa` image tag) and use `--config-id rtvi-vlm-dgx-spark-1gpu`.
Hardware enablement selects the profile; it does not certify image/model
compatibility on a particular node.

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
