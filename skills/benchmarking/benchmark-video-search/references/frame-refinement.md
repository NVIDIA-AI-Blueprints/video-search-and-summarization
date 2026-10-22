# Attribute frame refinement

The runner accepts `--frame-scoring auto|source|indexed` and
`--frame-lookup-transport auto|single|msearch`. Both default to `auto`; the
configured CLI resolves compatibility before inference and checkpoint identity
validation. The effective profile, concrete index identities, and scoring version
participate in the resume fingerprint. Changed profiles require a new run.

No implicit timestamp bounds are introduced. Queries without the bounds required
by frame refinement report skipped refinement; they do not benchmark that path.
A legitimate no-match is not a failure. Attempted refinement errors or changed
execution profiles mark the query unanswered under `--tolerate-unanswered`; the
original candidate pool and diagnostics remain in the query evidence and are not
scored as an empty ranking. Production retrieval retains its best-effort pool.

Old artifacts remain loadable. Missing diagnostics indicate unknown coverage,
not complete refinement. Offline scoring does not probe a deployment.

## Compatibility and evidence

This implementation adapts `attribute-search-latency.patch` (SHA-256
`514d7837a6392577701fe63e3f1de1a3e00d6d60453ff943485a0ac7ca42aa81`).
Automatic scoring uses nested indexed cosine when every resolved frame index has
compatible float vectors. Otherwise it uses verified retained source vectors.
Explicit incompatible modes fail. Forced indexed mode also rejects empty frame
partitions; live-routing preflight inspects both possible source partitions, so
a missing unused partition can reject that explicitly forced mode. Use automatic
mode when such partitions are expected. Custom clients without mapping inspection use
the legacy source path with compatibility marked unverified.

The read-only `vss search frame-capabilities` command reports the selected modes,
server version, concrete index UUIDs, dimensions, mapping hashes, and reasons.
Scoring stays `(cosine + 1) / 2`. Invalid dimensions, nonfinite values, and zero
vectors fail validation. The highest-scoring matching object supplies both score
and bounding box, including duplicate object IDs. A valid shifted score of zero
is preserved in attribute results and fusion instead of being replaced by the
behavior score. The scoring version records this correction.

Each top-level search shares a budget of 16 frame searches across attributes and
fusion. Automatic transport uses single searches below 16 unique lookup keys;
otherwise it uses batches of at most 16 when the backend supports multi-search.
An unsupported capability probe permits single-search fallback; authentication,
timeout, malformed-response, and execution errors remain failures. Failed
execution is never retried using a different scoring mode or transport.

Source listings and timeline metadata are reused within an invocation. Failed
metadata reads remain retryable, and clip timelines and screenshot envelopes
have separate caches. Cancellation drains sibling searches and releases the
request context, including streaming consumers.

## Local verification and measurements

Verification used an isolated Elasticsearch 9.4.4 container, with no deployment
access, ingestion, or data migration. The integration fixture accepts an explicit
loopback port and owns only UUID-prefixed test indices. Run it with:

```bash
VSS_FRAME_TEST_URL=http://127.0.0.1:<fixture-port> \
  libs/vss/.venv/bin/python -m pytest -q -o addopts= \
  libs/vss/core/tests/integration/search_core/test_frame_refinement_elasticsearch.py
```

Set `VSS_FRAME_TEST_PERF=1` to include measurements. The fixture checks analytical
positive, negative, and orthogonal cosine, winning-object boxes, duplicate IDs,
missing vectors, mixed mappings, index recreation, transport parity, and
automatic transport selection. Scores agree across supported paths within
`1e-5`; the 1,617-candidate measurement retains every candidate.

The table reports five warm latency samples per configuration with 1,152-vector
dimensions, after metadata and script warmup. Each cell is median seconds / data
requests. These compare the new source compatibility path with the indexed paths;
they are not a measurement of the complete original pipeline.

| Unique lookups | Source / single | Indexed / single | Indexed / multi-search |
| ---: | ---: | ---: | ---: |
| 1 | 0.0054 / 1 | 0.0048 / 1 | 0.0079 / 1 |
| 16 | 0.0784 / 16 | 0.0554 / 16 | 0.0238 / 1 |
| 256 | 0.8449 / 256 | 0.8578 / 256 | 0.2485 / 16 |
| 1,617 | 5.3521 / 1,617 | 4.6080 / 1,617 | 1.1904 / 102 |

At 1,617 lookups, source/single versus indexed/multi-search sent approximately
22.73 versus 10.46 MB of JSON, received 0.77 versus 1.19 MB of JSON, and peaked
at 3.74 versus 2.56 MB of traced Python allocations. Nested winning-object
responses increase response size. Memory was measured in a separate iteration,
without tracing overhead in latency samples.

[The measurement receipt](frame-refinement-measurements.json) preserves all
samples, maxima, request counts, and byte measurements. JSON sizes exclude HTTP
headers, capability reads, and VST; Python allocation peaks exclude Elasticsearch,
decoder, and model memory. Warm stage timing excludes cold capability probes,
LLM calls, ingestion, and the rest of the search pipeline. These results support
the automatic transport threshold; they establish no dataset-quality or
end-to-end deployment-latency improvement.

The final regression pass covered 1,340 core/CLI tests, 500 benchmark tests,
17 real Elasticsearch checks, and 60 deployment/preprocessing checks. The
measurement test passed separately. Four existing external reference-parity
tests remain skipped because `ci-vss-oss/run_eval.py` is unavailable; local
Drive/PAS scoring fixtures pass.
