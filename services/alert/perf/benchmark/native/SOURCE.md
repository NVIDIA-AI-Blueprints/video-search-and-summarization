# Native evaluation provenance

Source: ci-vss-oss, branch `munjalp/alerts-eval-helm`, commit `617ccaf`.

The selected files are copied without changes. Their source-relative `eval/`
layout is reconstructed from `benchmark/native/eval/` (runner/progress code)
and `setup/native-eval/` (deployment, configuration and configurator helpers).
This includes the
native runner, latency profile, evaluator/trigger configuration, Compose recipe,
profile configurator and Compose progress helper. Helm code inside the native
runner is retained for parity but is not exposed by the performance adapter.

The adapter creates a private runtime copy and changes deployment paths and image
references there. Injection, evaluation timing, metric queries and calculations
remain owned by the source script and evaluator container. Native accuracy and
Helm modes are not selectable in the performance scenarios.

NVDataset installation/download follows the native run_eval.sh flow in its own
helper venv (private NVIDIA package index). That helper is separate from the
locked host reporting/configuration environment under perf/setup/.
