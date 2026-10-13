# Troubleshooting

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every requested run completed |
| 1 | At least one run raised; the rest continued. See `benchmark.log` |
| 2 | Bad config or bad flags, or the lock store (Elasticsearch) did not answer. Nothing ran |
| 3 | The CLI could not be found, run or configured; a `cli.service_urls` address does not answer; or critic-on runs are planned against a deployment with no RT-VLM. Nothing ran |
| 4 | The corpus gate failed. Nothing was measured |
| 5 | A query could not be decomposed, its saved plan no longer matches (`--resume`), or a swept path has no query routing to it |
| 6 | The sweep aborted deliberately; remaining runs skipped |
| 7 | Another benchmark holds this namespace |

6 and 1 are deliberately different. An abort means the remaining runs were
skipped on purpose and the service needs looking at before `--resume`, so a
wrapper retrying on exit 1 must not treat the two the same.

## Exit 7 — "another benchmark run holds this namespace"

The message names the holder: user, host, PID, when it started, and how long ago
it last checked in.

If it is live, wait. Two runs sharing a namespace destroy each other's
measurements, and the damage is invisible afterwards.

If you are certain it is dead and the TTL has not yet expired (the lock is taken
over automatically after 300 s of silence):

```bash
curl -XDELETE 'http://ELASTICSEARCH/vss-search-benchmark-lock/_doc/vss-search?refresh=true'
```

## Exit 4 — the corpus gate failed

The message says which pattern fell short. A kNN query against an empty index
returns almost instantly, so without this gate an unpopulated corpus reads as
excellent latency rather than a broken setup.

| Message | Cause |
|---|---|
| `mdx-embed-filtered-*: 0 docs < required 300` | Nothing ingested, or a cleaning deploy wiped the PVC. Run with `--ingest` |
| `mdx-behavior-*: 0 docs < required 50` | The behaviour pipeline did not run. The attribute and fusion paths read this index, so they would return nothing — fast — without it |
| `not queried (missing from es_index_patterns)` | `require_min_docs` names a pattern that `es_index_patterns` does not. Fix the config |
| `query failed (...)` | Elasticsearch is unreachable from here. Check `services.elasticsearch_url` |

## Ingest gates

These run in order; the first failure stops the sweep.

### "could not clear Elasticsearch before ingesting"

The purge failed. Common reasons:

- **`no index template covers mdx-...`** — the chart's templates are applied by
  the Elasticsearch init Job, not by logstash (`manage_template => false`).
  Without them a recreated index gets dynamic mappings: the nested vision
  embeddings the embed path needs become plain objects, and every query returns
  nothing, quickly, from an index that looks populated. Re-run the init Job, or
  deploy with `--clean`, before purging.
- **`purge ran but documents are present 30s later`** — something is still
  writing. Logstash commits its Kafka offsets in Kafka, so an earlier aborted
  ingest can have messages in flight that an Elasticsearch delete does nothing
  to stop. Wait for the backlog to drain, or find the other ingest.
- **`refusing to purge: pattern ... also matches the protected index`** —
  `es_index_patterns` is broad enough to match the run lock. Narrow it.

### "N/M upload(s) failed"

Any failure aborts, not only all of them. A sweep that lost one of five videos
to an HTTP 502 once passed this gate and was compared against a five-video
sweep, with nothing in either report saying the corpora differed. A partial
corpus is a different experiment, not a smaller one.

Set `corpus.ingest.require_all_uploads: false` only to measure a partial corpus
deliberately.

### "the agent returned the same sensor_id to N upload(s)"

The embed service deduplicated two uploads **by content**. Since every upload is
renamed uniquely, a shared id can only mean that.

This matters more here than anywhere, because the corpus *is* one file uploaded
repeatedly. A collapse leaves the config claiming five videos while the corpus
holds one, and no later check can tell.

Known cause: the VIOS webhook racing the agent's own embed call for the same
`sensorId`. Confirm the deployment has it disabled (`webhooks.enabled: false`,
zero endpoints). The symptom in the log is an HTTP 502 whose body contains
`"code":"DuplicateAsset"` — the runner names that condition explicitly and waits
30 s before retrying, rather than retrying quickly, so the embed service's
in-flight state for the previous upload has time to clear.

### "sensor_field_mismatch"

A gated index is filling up but a term query on its configured sensor field
matches none of the uploaded sensor ids. The field name is wrong for that
mapping. Check `corpus.ingest.readiness.gate_indices`.

The two gated indices genuinely disagree, and this is the usual cause of the
message: `mdx-raw-*` documents carry `sensorId`, `mdx-embed-filtered-*` carry
`sensor.id`. Using one for both counts zero forever on the other.

Reported after a 180 s grace period rather than at the full timeout, because a
field that does not exist answers 0 for every video forever and would otherwise
burn four hours to produce a generic stall.

### "ingest did not complete (timeout)"

Every uploaded video must hold its own document count in every gated index,
and then the counts must hold still for 300 s. The message names the videos
that fell short and by how much — `copy-3: mdx-raw-*=27,000/54,000` is one
copy at half.

One copy short while the others finished is the interesting case: that stream's
perception stalled, not the pipeline. Check the RT-CV logs for its `sensorId`.

Ingest costs roughly the video's own duration, because the CV pipeline runs at
real time. Uploading in parallel does not change that — it shortens the
transfer, not the indexing, and whether five streams run side by side is up to
the deployment's capacity.

Raise `corpus.ingest.readiness.timeout_sec` rather than shortening
`stable_for_sec` — the quiet window is long because ingestion is bursty, and a
short window cannot tell a gap between videos from completion. That is exactly
how a sweep once started on 6% of its corpus with the gate reporting success.

Do **not** reach for `min_ratio` to get past this. It lowers the bar for what
counts as a finished video, so the sweep runs against less than the config
describes and the report still claims five videos.

### Every video is complete but the count looks too low

With `expected_docs_per_video` unset, the copies set the bar between
themselves: whichever gets furthest defines "complete" for the rest. If they
all stalled at the same point, they all agree, and only the quiet window stands
between that and a pass.

`readiness.expected_source` in `corpus_state.json` says which applied. Set
`expected_docs_per_video` once you know the real yields — it is the difference
between a bar that existed beforehand and one inferred from the result.

### "mdx-... is uneven across identical videos"

The copies are the same file, so they should produce the same counts. A copy
well above the others had its stream registered twice. The gate still passes —
it checks for *at least* the expected count — but the corpus now holds more
than the config describes, and no later count can separate the two passes.
Purge and re-ingest.

### "the corpus is not exclusively this sweep's"

Embed documents carry a sensor id this sweep never uploaded. Usually a Kafka
backlog from an earlier aborted ingest draining into the fresh index, or a
second ingest against the same cluster. Searches would score against data the
report does not describe.

The same gate fails when Elasticsearch did not answer the exclusivity query at
all ("could not check ... for documents from other ingests"): an unanswered
check is not a clean one. Re-run once ES is healthy, or set
`corpus.ingest.require_exclusive_corpus: false`.

## Exit 6 — the sweep aborted

### "the health gate ... got no usable answer"

One request at concurrency 1 did not come back within the gate's deadline.

This is almost always a wedged VLM. It is silent by design: no pod restarts,
nothing is logged, and `/v1/health/ready` keeps answering 200 throughout. Check
progress, not readiness:

```bash
kubectl logs -n vss-search deploy/vss-rtvi-vlm --tail=50 | grep -c 'percent complete is 0.00'
nvidia-smi --query-gpu=index,utilization.gpu,power.draw,memory.used --format=csv
```

Two signatures seen so far, both with queries stuck at 0.00%:

- 100% utilisation at near-idle power — a wedged CUDA context.
- Memory collapsed to ~2 GiB — the model is gone from the GPU.

**Read `wedge_diagnostics/` before restarting anything.** The wedge survives for
hours, and restarting first is why the root call is still unknown after several
occurrences. The runner captures the pod list, events, `kubectl top`, a describe
and a log tail per pod, and `nvidia-smi` from the VLM pod, automatically, on
this path.

Recovery:

```bash
kubectl rollout restart -n vss-search deploy/vss-rtvi-vlm deploy/vss-agent
```

Then `--resume` the results directory.

### "lost the single-run lock"

Another run overwrote this sweep's lock document, so both were measuring the
same namespace. The sweep stops before its next run. Find the other run (the
lock document names it) before resuming.

### "finished with N failures and no successful request"

Same underlying condition, found one run later. A run with no successful request
is the absence of a data point, not a slow one.

## Warnings that are not failures

### "The post-warmup barrier broke"

A worker did not reach the barrier in time, so the run never ran at full
concurrency and its throughput is measured over the whole run including warmup.
`measurement_window_clean` is `false`. Exclude the run or re-measure.

### "All N resource queries came back empty"

An unreachable Prometheus, a missing exporter and a mislabelled query all fail
the same silent way. The log distinguishes them: if Prometheus answered, the
exporters are the suspect.

The most common cause is the DCGM label trap. The exporter runs in its own
namespace, so its `namespace`, `pod` and `container` labels describe the
**exporter**. The workload holding the GPU is named by `exported_namespace`,
`exported_pod` and `exported_container`. Selecting on `namespace="vss-search"`
matches the exporter and returns nothing.

### "Run too short to difference the CPU counter"

Fewer than two scrapes landed inside the measured window. Its mean CPU is
unmeasurable, and the `rate()` series carries the previous run's work. Raise
`execution.min_run_duration_sec`.

### "CPU counter reset Nx during the window"

A container restarted mid-run. The restart is summed back in rather than
appearing as a large negative delta, so the figure is right — but the run
measured a service that restarted, which is worth knowing.

### "still busy after 180s" (cooldown)

The cluster did not drain within the ceiling. The next run's per-service CPU
will include some of this one's work. Raise
`execution.cooldown.max_wait_sec`.

A pod busy forever is a wedge, and the health gate plus `abort_on_empty_run`
handle wedges. The cooldown must not become a slower second version of them,
which is why it has a ceiling at all.

### "Prometheus gave no reading, so the full 180s was spent blind"

The cooldown could not tell idle from absent, so it waited out the ceiling.
Correct but slow. Check `services.prometheus_url` and
`execution.cooldown.query`.

## The CLI

### Exit 3 before anything ran: no usable `vss`, or no derivable origin

If it lists three ways it looked — `cli.vss_cmd`, `cli.vss_repo_root`, `vss`
on `PATH` — set `cli.vss_repo_root` to a checkout containing `libs/vss/cli`.

If instead it says it **cannot derive a CLI origin**, see the exit 4 section
below: set `cli.origin_url`.

If it found a CLI but `vss --version` exited non-zero, the usual cause is a
stale virtualenv under `libs/vss`. Run `uv sync --project <root>/libs/vss`.

### Exit 3: "cli.service_urls points ... at an address that does not answer"

ClusterIPs change on every clean redeploy. Stage 1 opens a TCP connection to
each overridden service and refuses to start if one fails, rather than letting
every request time out for hours. Refresh the addresses in `config.yaml`:

```bash
kubectl get svc -n vss-search -o wide
```

A discovered (non-overridden) service that does not answer is only warned
about. The results directory keeps `kubectl_services.txt`, the Services as
they were when the sweep started.

### Exit 3: "critic-on runs are planned, but the deployment exposes no rt_vlm"

The CLI skips the critic whenever its config has no `rt_vlm` route, so those
runs would measure critic off under a critic-on name. Deploy the RT-VLM, or
sweep with `--critic off`.

### The sweep stopped on its first request: "a state no retry fixes"

The CLI exited 2, 3, 4 or 5, which means respectively: a flag this CLI does
not know, a backend that is down, a CLI pointed at the wrong origin or a
required service absent, and an index that does not exist.

These abort deliberately. Every remaining cell would hit the same state, so
continuing would spend hours producing a wall of zeros and file them as
measurements. The failing command is in `<run_id>/request_records.jsonl` under
`argv`, with the CLI's own message in `stderr_excerpt`.

**Exit 4 almost always means the origin.** The CLI needs the unified
path-prefix origin, which routes `/vst`, `/elasticsearch` and `/rtvi-embed`.
Pointed at the agent's own port it finds one service of seven.

Port 7777 is where HAProxy listens on a *node*; in Kubernetes the same
prefixes sit behind the profile's Ingress at `global.externalHost`. Set
`cli.origin_url` to that host and verify it before a sweep:

```bash
curl -sf <origin>/elasticsearch/_cluster/health
curl -sf <origin>/vst/api/v1/sensor/version
```

Also check `--skip-vss-configure` was not left on: it copies
`~/.vss/config.json` as it stands, which may point at a different deployment.
The config the sweep actually used is `<results>/vss_config/config.json`.

**Exit 5 means nothing is ingested** — but the corpus gate runs before this,
so if the gate passed and a search still exits 5, the path being measured
queries an index the gate does not check. `attribute` and `object` read
`mdx-behavior-*`, which is deliberately not gated on.

### Exit 5 from the runner: a query could not be decomposed

The LLM was unreachable, or returned something that was not JSON. Every query
needs a path before the sweep starts, so this stops rather than dropping the
query.

On `--resume`, exit 5 can also mean a query's text in the config no longer
matches the saved `query_plans.json`. The finished runs measured the old text;
resume with the old config or start a new directory.

"no query resolves to path X" means decomposition routed the query you
expected on X somewhere else. See *A query routed somewhere unexpected* below.

If the message says it **cannot derive** an LLM endpoint, the agent URL is a
Kubernetes Service name and port 30081 is the NIM's NodePort — there is
nothing to derive from. Set `services.llm_url` to the LLM's own Service:

```bash
kubectl get svc -n vss-search | grep -iE 'nemotron|nim|llm'
```

Otherwise the endpoint was set but unreachable or not serving JSON. If it
serves more than one model, set `decomposition.model` to the one the agent
uses, or routing here will not match routing there.

### "This CLI has no `--original-query`"

The deployment's CLI predates the flag. The sweep continues without it; the
critic then judges hits against the decomposed arguments rather than the
question as asked, so confirmation rates will be lower than a newer CLI's.
Noted here because it makes critic figures incomparable across CLI versions.

### Throughput plateaus well below what the GPUs suggest

Compare `cli_spawn_cost_sec` against the median latency. Every query is a
Python interpreter start, and at concurrency 20 the load host runs twenty at
once — the plateau may be this machine, not the cluster. The runner warns when
startup exceeds 20% of the median.

## Other things that go wrong

### Ingest returns HTTP 404

`services.vss_agent_url` reaches something that does not route `/api/v1/videos`.
Under Helm it should be the profile's Ingress host, which mounts the agent at
`/api` — not the agent's own ClusterIP, and not a bare node IP.

The Ingress is also what makes ingest work at all from outside a pod: the
agent answers that POST with a VST upload URL built from its own
`vst_external_url`, so the bytes go through the Ingress regardless of how the
agent itself was reached.

### Latency is suspiciously flat and low

Check `corpus_state.json`. A kNN query against an empty or nearly-empty index
returns almost instantly. The corpus gate catches "empty", but a corpus that is
merely *small* passes it.

### A query routed somewhere unexpected

`query_plans.json` has the decomposition that produced it. The common case is
an absent `has_action`: the prompt marks it required and the model omits it
often enough to matter, and an absent one routes to `fusion` rather than
`attribute` — fusion still returns the embedding leg's candidates, where
`attribute` would silently drop every action-based match.

Group by `resolved_path` and say so in the report. Do not edit the query to
force the routing you expected without recording that you did.

### Pod logs are empty

`kubectl` is missing, or has no read access to the namespace. The streamer logs
a warning and the sweep continues; empty files are deleted rather than left to
look like a pod that said nothing.
