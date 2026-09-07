# Entity Management Tests

Unit tests for the request/response entity layer under `src/schemas/`: request
validation, config-driven defaults, and the enrichment response model.

## Modules

| Module | Covers |
|--------|--------|
| `test_entity_management_simple.py` | Imports, `EntityValidator` / `EntityBuilder` construction, defaults loading, `AlertSeverity` / `AlertStatus` enums, `AlertInfo` / `EventInfo`. One case is skipped: `EntityBuilder.create_error_response` was removed in the API rewrite |
| `test_alert_request_simple.py` | `AlertRequestEntity`: minimal required fields, optional fields, config defaults vs. input override, missing fields, invalid enums, confidence range, JSON round-trip |
| `test_validator_simple.py` | Batch validation: valid and invalid requests, mixed batches, statistics, empty input, 20-request throughput |
| `test_enrichment.py` | `EnrichmentResponse` model and `EnrichmentProcessor`: disabled path, prompt handling, VLM call parameters, and API error paths (connection, timeout, server error) |
| `test_legacy_payload_compat.py` | Which shapes of VLM parameters a request may use, and which one wins when a client sends more than one |

## Running

From `services/alert/` — `conftest.py` there puts `src/` on `sys.path`, so no
`PYTHONPATH` is needed:

```bash
pytest test/unit/entity_management_tests -v

# one module
pytest test/unit/entity_management_tests/test_validator_simple.py -v
```

`run_all_tests.py` is a thin wrapper around the same pytest invocation, kept as
a stable entry point; any arguments are forwarded.

## Configuration under test

The defaults these tests exercise come from `alert_request_defaults.yaml`,
resolved as described in
[`src/schemas/config/README.md`](../../../src/schemas/config/README.md). Two
sections matter: `vlm_params` for VLM sampling and `request_defaults` for
optional top-level fields. A missing or unreadable file is a startup failure
rather than a fallback to built-in values; `test_config_loading` covers the
loading path against the shipped file, not that failure mode.

## Legacy payload shapes

Nesting VLM parameters under `vss_params` / `vssParams` is **compatibility
behavior for older clients**, not the current contract — the current form is a
top-level `vlm_params` (or `vlmParams`). `test_legacy_payload_compat.py` pins
that acceptance and the precedence between the two, so it does not disappear
silently. `test_alert_request_simple.py` and `test_validator_simple.py` also
happen to use the nested shape in their fixtures; write new tests against
`vlm_params`.

## Adding tests

Follow the `test_[component]_[scenario].py` naming, cover both the success and
the failure path, and add the module to the list in `run_all_tests.py` as well
as the table above.
