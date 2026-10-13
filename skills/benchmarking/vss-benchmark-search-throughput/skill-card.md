## Description: <br>
Measure throughput, latency percentiles and cluster resource use of a deployed VSS search profile. Sweeps concurrency, `top_k` and the critic through the `vss` CLI from a thread pool, decomposes each query against the deployment's own LLM to fix its retrieval path before measuring, and records Prometheus and pod-log evidence for the measured window. Does not score retrieval accuracy. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>

## Use Case: <br>
Finding the concurrency at which search saturates, quantifying what the critic costs in latency and GPU time, comparing the event, attribute and fusion paths with the critic on and off under identical load, and capturing the resource profile of a release for capacity planning. <br>

## Release Date: <br>
2026 <br>

## References: <br>
- `references/flags.md` — every flag, and what turning it off costs <br>
- `references/reading-results.md` — the output columns and which are safe to quote <br>
- `references/troubleshooting.md` — gates that fail, and what each one means <br>
