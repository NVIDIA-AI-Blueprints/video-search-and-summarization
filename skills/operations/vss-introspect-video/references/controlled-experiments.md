# Controlled workflow experiments

Read `config/experiment.json` to identify this image. The control preserves the
runtime at df70ce35d; its manifest is provenance, not a policy override.

Build each experiment from its exact commit, not a moving branch tag. Record the
image digest and installed skill/helper hashes. Do not replace the image's
introspection skill with an uploaded skill from another experiment. An
environment-specific ask-video routing override may remain, provided it delegates
to the installed batched workflow and does not override that workflow's schema.

Keep models, endpoints, reasoning settings, summary memory, dataset snapshot,
normal/final call budgets, concurrency, timeouts and retry policy identical.
Use fresh per-attempt directories and isolated writable memory. Answers, prior
run results, human annotations and ground truth are evaluator-only inputs.

Smoke-test one question before launching a cohort. Preserve execution failures
and retry costs separately from wrong answers. Unit tests use mocked inference;
they do not establish backend compatibility or benchmark improvement.

Report per-question answers and paired gains/regressions, ledger closure and its
support, exact prompts and raw replies, requested windows/sampling, actual frame
telemetry when available, calls, latency, usage, errors and final-check outcomes.
Never infer actual delivered frame coverage from a requested window alone.

The existing normal-call path estimates a 48-frame limit. It does not guarantee
that an FPS-driven backend submits at most 48 images. Preserve image-limit errors;
do not silently change the model, route, frame rate or budget to hide a failure.
