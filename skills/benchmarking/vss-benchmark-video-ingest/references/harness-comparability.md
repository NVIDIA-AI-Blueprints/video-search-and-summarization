# Comparing benchmark runs

Establish a baseline with this skill's CLI upload path and client-observed ES
completion rule. Matching column names do not make results from another ingestion
path or timing definition interchangeable.

Keep these inputs consistent when comparing runs:

- Corpus content, bytes, duration, frame rate and local storage medium.
- CLI installation and runtime, deployment routing, models and chunk duration.
- Worker concurrency, staggering, warmup and video count.
- Expected-count tolerance, ES polling and readiness timeout settings.
- Cleanup policy, settle period and timeout.
- Client resources, network path and background load.

CLI startup, local file reads, transfer, media waiting and ES observation all
contribute to latency. Polling delays completion observations; backend indexing
instants are not measured. Failed points do not establish capacity, and retained
or late-producing uploads can affect subsequent load even after a bounded cleanup
check. No internal telemetry is collected.
