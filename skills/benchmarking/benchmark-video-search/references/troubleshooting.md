# Troubleshooting

Ingestion failures, CLI exit codes, and the gaps this benchmark does not cover.

## Ingestion

`--ingest-flow` defaults to **`vst-direct`**.

### `vst-direct` (default)

The runner uploads to VIOS, which fans the video out to perception services
through webhooks. There is no `/complete` call or `chunks_processed` count.
After upload, the runner waits for VST registration and probes the search index;
registration alone does not prove that embedding or indexing finished. Check
`webhooks.enabled` and the RT-Embed model mapping if the index probe fails.

### `agent-3step` (explicit alternative)

```
POST {agent}/api/v1/videos                     → {"url": ...}
POST {url}          (bytes, nvstreamer-* hdrs) → {"sensorId": ...}
POST {agent}/api/v1/videos/{sensor}/complete   → {"chunks_processed": N}
```

Step 2 goes **browser → VST directly**, bypassing the agent. Step 3 is where
indexing happens (RTVI-CV stream add and RTVI-Embed embedding generation, run
in parallel). Each phase is timed separately, so you can see whether time went
to transfer or to processing.

`/complete` is intermittently flaky (502 on the first call, 200 on a retry), so
it retries with backoff — `--complete-retries`, default 3.

### `legacy-put`

`PUT /api/v1/videos-for-search/{name}` — one request. **Deprecated upstream**,
and its removal is gated on this repo migrating away from it. Kept so a
baseline can still be captured while it exists. It runs the same post-upload
pipeline internally, so it is not more reliable — just less observable.

### After ingest

Neither a successful `vst-direct` upload nor a 200 from the optional
`agent-3step` `/complete` call proves readiness. The runner polls VST for
registration; all ingestion strategies also require embedding coverage and
configured CLI readiness checks, with attribute readiness for dependent scenarios.
Querying early returns empty results that look like a retrieval regression.

### On a shared deployment

```bash
--skip-existing    # do not re-upload what VST already lists
--clear --confirm-delete  # deletes ALL sources; only on explicit request
```

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Could not find a vss CLI` | none of the four sources resolved | follow the error's remedy: pass `--vss-repo-root` for a current checkout or install `vss` from that checkout |
| `vss exited 4 ... does not expose` | CLI pointed at the agent port | let the script configure it, or `--vss-base-url http://host:7777` |
| `vss exited 1 ... ModuleNotFoundError` | stale virtualenv | rebuild it, or use `--vss-repo-root` |
| `vss exited 5` | nothing ingested | drop `--skip-ingest` |
| `/complete` 502 | known flakiness | retried automatically; raise `--complete-retries` |
| `Duplicate Camera id` | RTVI-CV already has that stream | does not prove indexing completion; inspect post-ingest coverage and readiness |
| Everything scores 0.0 | possibly empty or unscoped search indices | check Step 3's Elasticsearch index counts and the index-probe result; a VST listing alone is insufficient |
| Header says `fallback_path` | routing is active; that is the path for unrouted queries | look at `planned_paths` |

---

## Known gaps

- **`openclaw` query flow** — the full new UI flow end to end: chat → OpenClaw
  agent → skill → CLI. It would measure OpenClaw's skill selection and routing
  in addition to CLI retrieval. This runner calls the deployment LLM for query
  decomposition but bypasses OpenClaw. An end-to-end run needs a NemoClaw
  sandbox and a decision about whether CI takes that dependency.
  Not called "agent" because the NAT agent behind `POST /api/v1/search` runs
  its own decomposition — a different decision-maker. That path was removed
  from this script; the legacy `run_eval.py` in the separate `ci-vss-oss`
  repository still queries it and is not available in this checkout.
- **Fixed-route replay differs from live routing** — dataset-provided
  decompositions are used when live decomposition is off or unavailable, while
  `--decompositions` explicitly replaces the live decomposer. Report which
  mode ran.
- **Stage latency depends on the deployed VSS version** — this PR adds the
  `timings` block; a deployment without that code omits the section.
- **Path coverage depends on the queries and their decompositions** — a set of
  action-only queries can exercise only `embed`, however many queries it has.
  Check the `Search paths:` line before reading per-path results.

See `flows/routing.py` for the routing rule and `flows/ingest.py` for the ingest
contracts.

## Every hit is `unverified` and critic-filtered metrics read `NA`

The critic never ran, or ran and could not fetch the clip. Retrieval is
unaffected and looks healthy, which is what makes this hard to spot.

Verification is best-effort by design — `_verify_results` never fails a search.
A disabled critic adds a `Visual verification disabled: ...` search message;
failed clip verification instead leaves hits `unverified`. Check the causes in
order.

**1. The actual VST clip URL is not reachable from RT-VLM.**

The CLI builds the critic with `media_mode="video_url"` and
`video_url_scope="internal"`. VST supplies the `videoUrl` in the search result;
the CLI's configured base URL only controls how the CLI reaches the services.
`vss configure --base-url http://localhost:7777` is valid when the CLI runs on
the deployment host and does not itself make the clip URL loopback.

Inspect a returned hit's `videoUrl`, then test that **exact URL** from the
RT-VLM container (for example with `docker exec vss-rtvi-vlm curl ...`). If it
cannot be fetched, correct the VST ingress or clip-URL configuration used by
that deployment; changing the CLI base URL alone may not help. Do not assume
the Docker bridge gateway or the host LAN IP is the right address.

**2. RT-VLM is not in the CLI's config.** The critic stack is built only when
the deployment exposes one — `vss configure show` must list `rt_vlm` with both a
URL and a non-empty model list, and it must answer an availability probe. Any of
those missing returns no critic, silently.

**3. The critic ran but returned no verdict.** Distinguish this from the above:
here the response carries a `search_message` such as *"Visual verification ran
but produced no verdict for any hit"*, rather than no verification block at all.
That is a different failure — the critic was reached and produced nothing — and
is not explained by the clip-URL routing above.

---

## Clip-level scoring (clip-relevance / drive)

**`--scoring drive` raises "expected each of N gallery clips exactly once".** The
dense retrieval did not return every gallery clip exactly once per query. Causes:
the gallery was not retrieved with `--full-gallery`; a clip indexes to more than
one window (drive needs one window per clip — use `clip-relevance`, which dedups,
or fix the chunking); or the gallery indexes to more than the CLI's 1000-result
cap. This is a precondition failure, not a low score.

**A clip-level run reports low coverage / `mAP_scope: observed-ranking`.** The
retrieval did not return the whole gallery. Re-run retrieve with `--full-gallery`,
and if the gallery exceeds the 1000-result cap, split it or accept the labeled
partial coverage.

**"unexpected sensor ..." or "not in the gallery GT".** The retrieval artifact's
hits could not be attributed to gallery clips. The search result's `sensor_id`
must match the ingestion manifest's `sensor_id` — verify the gallery was ingested
with that manifest and that the deployment returns matching sensor ids. Scoring a
gallery artifact with `segment-overlap` (or vice versa) fails loudly by design;
match `--scoring` to how the artifact was retrieved.


## Upstream monitoring deferred

The staged runner does not activate the upstream background ingestion watcher
or derive direct Elasticsearch/RT-CV polling endpoints. Diagnose readiness
through the configured `vss search readiness` command and its saved evidence.
Sparse clips can legitimately have no detections; record those gaps without
requiring one detection per clip. Missing required attribute services must not
be treated as a successful attribute evaluation.

A failed live decomposition is recorded and remains a failed query. It does
not trigger the upstream embedding fallback or failure-percentage policy.
