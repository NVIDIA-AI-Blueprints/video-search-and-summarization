# VSS performance reporting

`vss-perf-reporting` is the shared result contract for service-owned performance
benchmarks. It standardizes validation, file naming, atomic JSON writes, platform
metadata, and MinIO publication without owning a service's measurement logic.

## Result contract

Each benchmark invocation writes one canonical file:

```text
<service>_<config-id>_<YYYYMMDD_HHMMSS>.json
```

The JSON has four top-level fields:

```text
schema_version
metadata
summary
test_cases
```

`metadata` identifies the service, run, platform, configuration, benchmark, and
mode. `summary` contains the aggregate PASS/FAIL/PARTIAL status. Each entry in
`test_cases` represents one selected scenario or case. Service-specific numbers
belong inside that case's `metrics` object.

Native service output may be retained next to the canonical result as diagnostic
evidence, but consumers and uploads must use only the canonical result file.

## Service integration

Keep measurement and KPI decisions in the service's `perf/` package. Add a thin
adapter that maps its native output into `test_cases`, then call:

```python
from vss_perf_reporting import build_and_save, discover_platform

result_path = build_and_save(
    output_path="vss-perf-results",
    service="my-service",
    config_id="my-config",
    test_cases=test_cases,
    platform=discover_platform(),
)
```

Pass the returned `result_path` to `publish_result_file` when upload is enabled.
The uploaded object keeps the same filename under `<service>/`; publication does
not create a second timestamped copy.

The common Jenkins pipeline supplies `MINIO_ENDPOINT` in `host:port` form and
`MINIO_BUCKET` for publication. The shared reporting library reads both from
the environment. `MINIO_ENDPOINT` is required for uploads; local upload runs
must set it explicitly.

The service runtime must make `libs/vss-perf-reporting/src` importable and install
the dependencies declared in `pyproject.toml`.

## Tests

From the repository root:

```bash
PYTHONPATH=libs/vss-perf-reporting/src python -m pytest \
  libs/vss-perf-reporting/tests
```
