# Persistent raw SDK execution

`flows.raw_sdk.RawSdkRunner` is an opt-in helper for serial raw-search
experiments on an existing gallery. The standard `run_eval_flows.py` CLI
backend remains unchanged. This helper does not ingest, clear indices, persist
jobs, invoke a critic, perform reranking, or route queries.

Install the CLI/core from this checkout and configure its deployment as in
[AGENTS.md](../../../../AGENTS.md). The helper reads that recorded deployment;
requests accept no endpoints. Run with that installation's Python and keep the
CLI/core and benchmark code at the same revision.

From `skills/benchmarking/benchmark-video-search/scripts`, a minimal caller is:

```python
import asyncio
import json
import time

from flows.raw_sdk import RawSdkRunner


async def main():
    requests = [
        ("embed", {"query": "a forklift moving", "source_type": "video_file", "top_k": 10}),
        ("attribute", {"attributes": ["red jacket"], "source_type": "video_file", "top_k": 10}),
    ]
    async with RawSdkRunner(tuning={"no_merge_adjacent": True}) as runner:
        for path, payload in requests:
            started = time.perf_counter()
            output = await runner.search(path, payload)
            elapsed = time.perf_counter() - started
            print(json.dumps({
                "execution_backend": "persistent-raw-sdk",
                "latency_scope": "ready-sdk-call",
                "request_s": elapsed,
                "output": output,
            }))


asyncio.run(main())
```

For evaluation, preserve the existing request construction, timestamp/source
scope, candidate budget, top-k, merging preference, and scoring. Attribute
requests can use `candidate_limit` and `max_hits_per_source`; the latter
requires the former, as in the CLI. Tuning uses the same supported fields as
CLI `SearchTuning`, including `no_merge_adjacent`. Critic settings and unknown
tuning fields are rejected. Corrected indexed cosine is used by every runtime
and has no per-request scoring override.

For live routing, make a fresh LLM decomposition before each query, translate
it using the existing routing contract, then pass its selected path and request
to `search`. The helper does not consume saved routes or make an LLM call.
Object/tag paths are outside this helper's supported raw-eval scope.

## Runnable clip evaluation

For a self-contained clip dataset with `gt/queries_gt.json`,
`additional_gt/clips_gt.json`, and an existing complete ingestion manifest, run
this command from the repository root with its installed Python environment:

```bash
python skills/benchmarking/benchmark-video-search/scripts/run_raw_clip_eval.py \
  --dataset-root /path/to/subset \
  --ingestion-manifest /path/to/ingestion.json \
  --output-dir /path/to/new-results \
  --execution-backend sdk \
  --llm-url "$LLM_URL"
```

`LLM_URL` is the explicit existing LLM endpoint. The command never invents it.
The three modes default to `embed-only attribute-only live-routed`; live routing
always invokes the current checkout's LLM prompt afresh, with no saved-route
fallback. It reuses the existing raw clip mAP@10 scorer for event, PAS, and
all-query summaries. No ingestion, deployment, critic, or reranker is triggered.

- `--execution-backend cli` is the default and launches the matching environment's
  `vss` command for every query. `sdk` keeps one serial session alive.
- `--query-ids cohort.json` selects a small cohort; it reads IDs only and always
  loads canonical query text and GT from the dataset. Omitting it evaluates all
  queries. Existing cohort JSON with a `queries` list is accepted.
- `--execution-backend sdk --compare-cli` checks every SDK result against a paired
  CLI call, alternating execution order and sharing the fresh LLM plan only
  within that pair. Complete result data must match; otherwise the run fails.
- SDK `--embedding-cache-policy query` is the command default: clear embeddings
  between queries while retaining within-query deduplication, isolating startup
  and connection reuse from repeated-text cache effects. `native` retains the
  SDK's existing bounded caches. The helper's standalone default stays `native`.
- Defaults reproduce the raw comparison: top-k 10, cosine floor -1, no adjacent
  merging, and attribute-only exact candidate limit 100000 with one hit per
  source. Fusion uses its existing candidate behavior. All settings are recorded.
- `--dry-run` prints the input plan with no backend calls or writes. Use a new
  output directory for a real run; the command will not overwrite an old run.

Artifacts include input hashes/backend versions in `identity.json`, a separately
labelled Python-entry-to-ready setup time, per-query captures and fresh routes,
progress, and `summary.json` with clip mAP@10 and retrieval/routing/end-to-end
latency. CLI process exit is separate from response availability. A run is fully
successful only with `complete.json`; any exception leaves `failed.json`, not a
misleading complete summary. Registered gallery identities are checked against
the ingestion manifest before and after the run. SDK errors propagate and there
is no retry or fallback to another backend.

The generic `run_eval_flows.py` command uses a different annotation/scoring
contract and remains unchanged. Use `run_raw_clip_eval.py` for this raw clip
experiment; the generic command does not accept `--execution-backend`.

### Experimental person-state selection

`--live-routing-profile person-state-photo` selects the person-image expert for
an isolated person's appearance, optionally combined with ordinary standing,
sitting, lying, walking or running states. Other subjects, actions and external scene/relations use
embedding. It makes one fresh LLM extraction per query using a pinned prompt
with eleven inline examples, temperature zero, thinking off and a 32768-token
completion cap. Invalid or truncated responses fail without a route fallback.
Inflections of the five ordinary states are normalized without accepting
compound actions. Whole camera-view phrases mistakenly placed in the model's scene list remain
person appearance. Phrases with an external object or place still use embedding;
viewpoint words remain in the complete retrieval text. Bare clothing and
accessory descriptions imply a person wearing them; an explicit non-person
subject or external relation still uses embedding.

Recognized action-only phrases such as `a person walking` use embedding even
when the model selects a person subject. This check requires the complete
source wording to fit a conservative action grammar. Unknown appearance words
(for example, `tattoos` or `kimono`), numbers and non-English descriptions keep
the existing expert decision. A missing dictionary entry never proves that a
query has no appearance requirement.

Embedding receives the exact original text. Attribute search receives
`A photo of ` plus the original text stripped of surrounding whitespace and
terminal periods, followed by one period. The model's proposed caption and
metadata never enter retrieval. This profile selects no fusion path and uses
the fixed gallery scope; it does not change the agent or CLI defaults.
Measured gains and limitations are in [person-state-evidence.md](person-state-evidence.md).

For an already configured deployment and an 80-clip gallery with one embedding
window per clip:

```bash
python skills/benchmarking/benchmark-video-search/scripts/run_raw_clip_eval.py \
  --dataset-root /path/to/subset \
  --ingestion-manifest /path/to/work/ingestion.json \
  --deployment-manifest /path/to/work/deployment.json \
  --output-dir /path/to/new-person-state-results \
  --execution-backend sdk --modes live-routed \
  --live-routing-profile person-state-photo --top-k-values 80
```

The manifest supplies the recorded LLM endpoint and model. At depth 80 the
scorer's AP@80 denominator is all relevant clips, so it is full AP for this
gallery. AP@10 can be independently rescored from the same captured rankings.
For another gallery, choose sufficient depth for complete retrieval; a limited
ranking must not be presented as full mAP. Person search can return fewer
sources when no person evidence is indexed. Missing relevant sources contribute
zero; no tail clips are fabricated. This is an experimental policy on a
previously examined regression cohort, not an unseen-dataset accuracy claim.

## Lifecycle and errors

Use one async context and one event loop for the whole serial sequence.
Do not call `asyncio.run` per query or share the runner across worker threads.
Concurrent searches and calls from another event loop are rejected. Context
exit cancels unfinished child queries and closes owned clients. Invalid input,
missing deployment services, and backend exceptions propagate; record them as
failed captures rather than converting them to empty successful searches.
There are no extra retry loops or raw REST fallbacks.

The generic flow runner currently uses a thread pool even at concurrency one.
This helper is deliberately not registered as a synchronous `--query-flow`:
adding it there requires an explicit serial async execution path and teardown.
Its coverage probes also need to preserve the raw-only policy. Importing the
helper does not change the default CLI backend or the existing probe behavior.

## Timing and reproducibility

A ready SDK call avoids repeated interpreter/import/client startup. The first
process still pays that cost. Report process cold startup separately from SDK
request latency, and include routing LLM time separately for live queries.
The returned envelope carries the same search data and core timings, but it
has no CLI job/completion markers. Do not label SDK timings as CLI timings.

Persistent clients retain native bounded text-embedding caches. For an
attribution study, measure cache hits or control cross-query cache reuse
separately; do not credit repeated-text cache hits to import optimization.
The helper does not change cache policy, scoring, or retrieval semantics.
Record backend, source revision, dependency versions, scope, and cache policy.
Do not modify the deployment/configuration during a comparison.

Before quoting quality gains, compare complete result data and clip mAP@10
against matched CLI inputs. If routing is nondeterministic, use a fresh LLM
plan shared only within the immediate comparison pair so execution and routing
changes are measured independently.

## Live query wording

Live routing reads the current checkout's prompt. The appearance-only rewrite
experiment is reverted; this runner does not rewrite or join attributes itself.
Fixed embed/attribute baselines retain their original query inputs.

## Multi-k live evaluation

```bash
python skills/benchmarking/benchmark-video-search/scripts/run_raw_clip_eval.py \
  --dataset-root /path/to/subset \
  --ingestion-manifest /path/to/ingestion.json \
  --output-dir /path/to/new-results \
  --execution-backend sdk --modes live-routed \
  --top-k-values 5 10 20 --embedding-cache-policy query \
  --warmup-queries 3 --gpu-telemetry --llm-url "$LLM_URL"
```

Use `--query-ids cohort.json` for the pilot; omit it for all dataset queries.
`--top-k-values` and `--top-k` are mutually exclusive. For each query, exactly
one fresh LLM decision is shared in memory across actual top-5/10/20 searches.
The order cycles through all six permutations. Search inputs differ only in
requested k; current internal candidate policies vary naturally with k.
Cross-search embedding caches are cleared, including between k trials.

Scoring uses first-occurrence distinct clip rankings and
`AP@k = sum(P@r * relevant(r), r<=k) / min(R,k)`. There is no additional search
to fill duplicates or missing results. Single-k default10 keeps its existing
AP@10 keys. Multi-k captures live under `captures/live-routed/top-k-K/` and
summary results under `live-routed.by_top_k.K`.

`response_s` includes request preparation, validation, SDK search, and response
JSON serialization. Reported per-k end-to-end time adds the one measured
routing/plan duration, excluding other k trials. The same duration is reused
for comparison; actual campaign time and token usage do not count it three
times. Initialization, warmup and file/scoring overhead remain separate.

The run saves `queries.json`, prompt/source/version/input fingerprints,
`routes.jsonl`, per-query raw decompositions and per-k payloads/results.
Warmup artifacts are separate. `token-usage.json` counts actual LLM calls once,
separately for warmup and measured queries. Missing usage is unavailable, never
zero; cached/reasoning detail tokens are subsets and not added to parent totals.
These are routing tokens, not embedding tokenizer counts; no dollar cost is
estimated.

GPU telemetry uses one read-only nvidia-smi child at 200 ms by default, with
raw samples and monotonic query-phase markers. Driver utilization windows may
span approximately 1/6 to1second, so window summaries can overlap stages and
co-located services. They are not exclusive inference time. GPU failures are
reported without invalidating search metrics; no samples never means0utilization.

To validate a pilot, `--compare-cli` adds paired CLI controls using the same
fresh route; optionally limit them with `--compare-cli-query-ids ids.json`.
Controls are not included in primary latency summaries. Do not enable them in
the main performance run. Compare old AP@10 calculations on identical saved
captures rather than requiring independent live LLM runs to produce equal scores.

Offline route-reference scoring, automatic crop-gallery ranking, and native-
person ranking are experimental paths whose implementation files are not part
of this committed runner. They are intentionally not exposed by this guide.
