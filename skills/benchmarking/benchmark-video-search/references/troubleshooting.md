# Troubleshooting

Ingestion failures, CLI exit codes, and the gaps this benchmark does not cover.

## Ingestion

`--ingest-flow`, default **`agent-3step`**.

### `agent-3step` (default)

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

A 200 from `/complete` is **not** readiness — indexing continues afterwards.
The script polls VST until every source registers before querying, because
querying early returns empty results that look exactly like a retrieval
regression.

### On a shared deployment

```bash
--skip-existing    # do not re-upload what VST already lists
--clear            # ⚠️  deletes ALL videos on the endpoint, including others'
```

---


## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Could not find a vss CLI` | none of the four sources resolved | the error lists each one and why; see [Getting a `vss` CLI](#getting-a-vss-cli) |
| `vss exited 4 ... does not expose` | CLI pointed at the agent port | let the script configure it, or `--vss-base-url http://host:7777` |
| `vss exited 1 ... ModuleNotFoundError` | stale virtualenv | rebuild it, or use `--vss-repo-root` |
| `vss exited 5` | nothing ingested | drop `--skip-ingest` |
| `/complete` 502 | known flakiness | retried automatically; raise `--complete-retries` |
| `Duplicate Camera id` | RTVI-CV already has that stream | treated as done — embeddings still generate |
| Everything scores 0.0 | usually an empty index | check VST lists your sources |
| Header says `fallback_path` | routing is active; that is the path for unrouted queries | look at `planned_paths` |

---

## Known gaps

- **`openclaw` query flow** — the full new UI flow end to end: chat → OpenClaw
  agent → skill → CLI. It would measure **routing** quality (does the agent pick
  the right path and attributes?) rather than the **retrieval** quality
  everything here measures with the routing supplied. Needs a NemoClaw sandbox,
  an LLM in the loop, and a decision about whether CI takes that dependency.
  Not called "agent" because the NAT agent behind `POST /api/v1/search` runs
  its own decomposition — a different decision-maker. That path was removed
  from this script; `run_eval.py` still queries it.
- **`vst-direct` / webhook ingest** — the UI has moved past `agent-3step`;
  the contract is not documented yet, so the registry slot is deliberately empty.
- **The route is supplied, never measured** — decompositions come from the
  dataset, so every number here is retrieval quality *given a correct route*.
  Whether the agent would pick that route is the `openclaw` gap above. How a
  dataset's decompositions were produced therefore changes how its results
  should be read; that belongs with the dataset, not here.
- **Stage latency needs an unmerged branch** — `feat/search-core-output` in the
  product repo adds the `timings` block. Without it the section is absent.
- **Path coverage depends entirely on the dataset** — the script routes what it
  is given, so a dataset whose queries are all action descriptions exercises
  only `embed`, however many queries it has. Check the `Search paths:` line in
  the summary before reading a per-path number as meaningful.

The reasoning behind each of these lives in the commit history and in comments
at the relevant code — `flows/routing.py` for the routing rule, `flows/ingest.py`

## Every hit is `unverified` and critic-filtered metrics read `NA`

The critic never ran, or ran and could not fetch the clip. Retrieval is
unaffected and looks healthy, which is what makes this hard to spot.

Verification is best-effort by design — `_verify_results` never fails a search —
so a broken clip URL and "no critic configured" are indistinguishable in the
output. Check the causes in order.

**1. The clip URL is not routable from inside the containers.** This is the
common one when the benchmark runs *on* the deployment host.

The CLI builds the critic with `media_mode="video_url"` and
`video_url_scope="external"`, so RT-VLM is handed a VST clip URL and fetches it
itself. That URL is built from whatever `vss configure --base-url` recorded. Set
it to `http://localhost:7777` and RT-VLM — a container — resolves `localhost` to
itself, the fetch fails, and every hit stays `unverified`.

```bash
vss configure show | grep -i base_url
docker exec vss-rtvi-vlm curl -sf -o /dev/null -w '%{http_code}\n' \
    http://<HOST_LAN_IP>:7777/vst/api/v1/sensor/version
```

Fix by pointing the CLI at an address the containers can reach, even though you
are on the host:

```bash
vss configure --base-url http://<HOST_LAN_IP>:7777
```

`http://172.17.0.1:7777` (the docker bridge gateway) works as a fallback when
the host IP is not reachable from the container network.

Running the benchmark from another machine hides this entirely: nothing but a
routable address reaches the deployment in the first place.

**2. RT-VLM is not in the CLI's config.** The critic stack is built only when
the deployment exposes one — `vss configure show` must list `rt_vlm` with both a
URL and a non-empty model list, and it must answer an availability probe. Any of
those missing returns no critic, silently.

**3. The critic ran but returned no verdict.** Distinguish this from the above:
here the response carries a `search_message` such as *"Visual verification ran
but produced no verdict for any hit"*, rather than no verification block at all.
That is a different failure — the critic was reached and produced nothing — and
is not explained by the clip-URL routing above.
