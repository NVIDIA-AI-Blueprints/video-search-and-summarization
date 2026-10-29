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

The service runtime must make `libs/vss-perf-reporting/src` importable and
install the dependencies declared in `pyproject.toml`.

## Jenkins-owned publication

Benchmark entrypoints only write canonical reports. The common Jenkins pipeline
binds MinIO credentials after the benchmark exits and invokes the shared CLI:

```bash
uv run --project libs/vss-perf-reporting --locked \
  python -m vss_perf_reporting publish \
  --report-dir perf-results/<run>
```

The CLI recursively finds and validates canonical JSON, ignores native
diagnostic JSON, uploads each result as `<metadata.service>/<filename>`, and
writes a neighboring `<filename>.upload.json` receipt containing the object
URI and SHA-256 checksum. It reads `MINIO_ENDPOINT`, `MINIO_BUCKET`,
`MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY` from the environment; credentials
must not be passed as command-line arguments.

`MINIO_ENDPOINT` is required for publication. The committed `uv.lock` makes the
publisher independent of system Python packages such as `pip`.
Generating and validating local reports does not require MinIO credentials.
Only publication requires the endpoint, bucket, access key, and secret key.


`publish_result_file` remains available for trusted local tooling, but service
benchmark scripts must leave CI publication and credential ownership to
Jenkins.

## Tests

From the repository root:

```bash
PYTHONPATH=libs/vss-perf-reporting/src python -m pytest \
  libs/vss-perf-reporting/tests
```
