## Description: <br>
Benchmark the retrieval quality and latency of a deployed VSS search profile. Ingests a labelled dataset, runs queries through the `vss` CLI across the embed, attribute, fusion and object paths, and reports precision, recall, mAP, MRR, HIT@k and a per-stage latency breakdown. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>

## Use Case: <br>
Detecting retrieval regressions between builds, comparing the four search paths on identical ground truth, and locating latency in the search pipeline (embedding generation, Elasticsearch retrieval, result fusion, critic verification). <br>

## Release Date: <br>
2026 <br>

## References: <br>
- `references/dataset-format.md` — what a dataset file must contain <br>
- `references/reading-results.md` — metrics and the stage-latency breakdown <br>
- `references/troubleshooting.md` — ingestion failures and CLI exit codes <br>
