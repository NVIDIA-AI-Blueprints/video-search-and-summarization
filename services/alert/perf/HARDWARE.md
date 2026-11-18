# Alerts Verification hardware validation

The benchmark uses the same dataset, synthetic Kafka workload, native latency
calculation and reporting on every platform. Full E2E remains disabled.
`configs/perf-configs.yaml` owns hardware selection; it does not create a node pool.

| Config ID suffix | Platform | Image architecture | Native profile | State |
|---|---|---|---|---|
| Original `alerts-verification-compose` | RTXPRO6000BW-SE | amd64 | RTXPRO6000BW | Existing enabled baseline |
| h100 | H100-SXM | amd64 | H100 | Enabled; smoke test pending |
| rtxpro4500bw | RTXPRO4500BW | amd64 | RTXPRO4500BW | Enabled; remote LLM reachability and worker validation pending |
| dgx-spark | DGX-SPARK | arm64-igpu | DGX-SPARK | Enabled; smoke test pending |
| igx-thor | IGX-THOR | arm64-igpu | IGX-THOR | Enabled; smoke test pending |
| gb300 | GB300 | arm64-igpu | GB300 | Enabled; smoke test pending |

Non-baseline IDs have the prefix `alerts-verification-compose-`.
The original ID and arbitrary local report identifiers preserve automatic GPU
profile detection. Named hardware configurations validate detected GPU type;
Thor detection is shared, but the IGX configuration selects the IGX native profile.

Spark and Thor use the native edge profile's GPU 0 placement. Do not supply
`--vlm-gpus` or `--llm-gpus` for these profiles: the native deployment rejects
explicit model device options. GB300 selects GPU 0 for both local models when
launched with its performance configuration. For a local invocation, pass
`--vlm-gpus 0 --llm-gpus 0` to select that same placement on a multi-GPU host.

RTX PRO 4500's source deployment requires at least two GPUs, driver 595.58.03
or newer, and a remote LLM. It and Spark use the source CI remote LLM topology. Set
`ALERTS_PERF_REMOTE_LLM_URL` to the shared LLM base URL (without `/v1`); the
native deployment resolves its advertised model. The endpoint must be reachable
from the worker and deployment containers for a successful run. Do not replace it with an invented endpoint or reduce the native
requirements to make a preflight pass.

The internal orchestrator supplies the source CI endpoint. For a local run,
export `ALERTS_PERF_REMOTE_LLM_URL` explicitly; no private network address is
embedded in this public benchmark package. Other profiles keep their native
local/remote model settings unchanged.

For GB300, RTVI CV and RTVI VLM support containers use the explicitly configured
`PERF_VSS_RT_CV_SBSA_IMAGE` and `PERF_VSS_RT_VLM_SBSA_IMAGE` references from
`setup/configs/images.yaml`. Override those keys to select other approved SBSA
versions. The effective references are recorded in `support-images.json` and
report metadata. The supplied Alerts artifact is unchanged. This GB300-specific
selection does not change supporting-image selection for the other platforms.

## Validate one target

1. Verify the Alerts image and every supporting image actually deployed by the
   scenario have compatible manifests/runtime support for the host. The ARM
   artifact classifier is `arm64-igpu`; it does not prove image compatibility.
   `IMAGE_TAG` is used exactly as supplied. Supporting image references remain
   in `setup/configs/images.yaml`; no image tag is guessed or rewritten here.
   In particular, the native deployment selects SBSA tag defaults for Spark
   and GB300, while explicit `PERF_*_IMAGE` references take precedence. Review
   the rendered Compose images rather than assuming the artifact classifier
   changes supporting-service tags.
2. Reserve an idle target with Docker access, compatible NVIDIA runtime/driver,
   required credentials and dataset access. Follow the service README setup.
3. All six P0 configurations are enabled. Select one target at a time for the
   initial synthetic smoke tests before requesting the complete hardware matrix.
4. Confirm requested Alerts image identity, deployment readiness, completed
   latency measurements and source workload values. Inspect report failures,
   hardware metadata, diagnostics, upload, dashboard visibility and cleanup.
5. Repeat these checks on the remaining targets. Keep automatic promotion
   triggers disabled throughout the PoC.

For example, on a reserved Spark node, after setup described in the README
and exporting `ALERTS_PERF_REMOTE_LLM_URL`:

```bash
bash services/alert/perf/scripts/run_benchmark.sh \
  --config-id alerts-verification-compose-dgx-spark \
  --scenario synthetic_alerts
```

The local entrypoint runs the explicitly requested configuration regardless of
its matrix enable flag; that flag controls orchestrator scheduling.

Once validated, architecture filtering must keep AMD64 and ARM artifact runs
separate. Selecting all hardware runs only enabled matching configurations;
enabling these rows does not claim those platforms have passed evaluation.
No cross-platform latency thresholds are introduced.
