# Benchmark contract

```text
configured checkout CLI + local corpus
  → validate inputs and configured CLI / preflight VIOS
  → check deployed VSS version API against metadata.yml; stop on failure
  → measure corpus / write corpus CSV / project volume
  → check public ES health / configured warmup (excluded from metrics)
  → one thread per concurrent worker
      → launch vss vios add --type video PATH --name UNIQUE_FILENAME
      → VIOS stores media and emits camera_streaming
      → deployed webhooks register CV / Embed / optional VLM
      → model outputs → Kafka → Logstash → Elasticsearch
      → CLI returns VIOS identity and media availability
      → worker reads ES until its raw frames + embed chunks meet readiness
      → next video assigned to that worker
  → summarize point → optional CLI deletion outside its timing window
  → next point with a fresh UUID
  → write CSVs, raw records and run metadata (including version-check evidence)
  → generate summaries and three charts / validate artifacts / review interpretation
```

CLI execution and inference can overlap; the CLI does not wait for ES.
Each worker processes the whole class sequentially. A point has concurrency × files
uploads, with no replacement load after each worker's class finishes. Staggering is
capped; it affects achieved overlap and is recorded. No process is launched per frame
or chunk. No source-file copies are made: --name supplies the unique stored filename.

The caller's CLI uses the vss_core VIOS client; no Agent upload or completion
handler is invoked. The separate version preflight calls the public VSS version API,
currently hosted by the Agent application. Notification JSON and deployed service schemas must already
agree. The benchmark changes neither webhooks nor microservice configuration.

Only client workload settings may vary: corpus/class/subset, concurrency, warmup,
stagger, ES request/poll/readiness timeouts, cleanup, and independent repeated runs.
No Helm, replicas, stream/batch caps, model changes, network shaping, or telemetry.
See [configuration](configuration.md) and [completion](completion-and-accounting.md).
