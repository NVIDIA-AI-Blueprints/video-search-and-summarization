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
references there. Deployment remains owned by the source runner; latency evaluation now uses
the locally ported source implementation described below. Native accuracy and
Helm modes are not selectable in the performance scenarios.

NVDataset installation/download follows the native run_eval.sh flow in its own
helper venv (private NVIDIA package index). That helper is separate from the
locked host reporting/configuration environment under perf/setup/.

## Local latency evaluators

Bridge source: alert-bridge-evaluator at 231cfc7b2a4e0ce83cca49f14fd6a6ecf626eeb5.
SURF source: evaluation/trigger_eval_svc_v2 at 7f6e4b02b226797d0d91c8ed07fa60d76b650aab.

The bridge service calculation modules, settings, protobuf schemas and optional
startup helpers are retained with package-relative import adaptations. Latency
router methods are selected into latency_workflow.py without changing their bodies.
SURF stream API/timing methods and payload construction are selected without
changing their bodies; a local context replaces trigger-framework inheritance.
These files preserve source notices; this provenance does not assign a new license.
Confirm the applicable source distribution permissions before publishing the port.

The shell runner now invokes the local Python client in place of evaluator service
deployment/API polling. It is therefore adapted orchestration, not an unchanged
copy of the original runner. Accuracy and Helm scenarios remain unsupported.
The native report suite is retained, while canonical reports still use the shared
reporting adapter. Jenkins continues to own publication.

Source Prometheus, producer, and report-suite tests are retained under
`perf/tests/native_evaluator/` with namespace adaptations. The producer test
factory also retains its supplied configuration so its existing configuration
assertion tests the producer rather than an empty fake fixture.

The HTTP client dependency uses `requests>=2.32.5,<3` rather than the source
service's yanked 2.32.0 pin. The port preserves the requests-based client APIs;
its resolved version is recorded in setup/uv.lock.
