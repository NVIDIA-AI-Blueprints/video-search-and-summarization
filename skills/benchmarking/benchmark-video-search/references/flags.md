# Flags

The flags that change what a run means, and the ones that do not.

## Querying

`--query-flow cli` is the only backend today.

Runs `vss search run <path>` as a subprocess per query. Before the first query
the script resolves the CLI, runs `vss --version` to catch a broken install,
and points `~/.vss/config.json` at this deployment.

Live decomposition routes each query by default. Dataset-provided
decompositions are used when live decomposition is disabled or unavailable;
`--decompositions` explicitly replays a stored set. Without any decomposition,
every query uses `--search-path` (default `embed`).
`--no-decompose` disables only the live LLM; it still replays stored routes.
For a true single-path baseline, use `--fixed-search-path`, which ignores
dataset routes and cannot be combined with `--decompositions`.

Any non-zero exit **aborts the run**. There is no exit code meaning "this query
failed but the rest are fine" — continuing would just manufacture zeros that
read as a retrieval collapse.

### Merging

Upstream merges contiguous same-sensor windows by default and reports the mean
of their scores. That changes the number of scored segments — the denominator
of precision — so this script passes `--no-merge-adjacent` by default to stay
comparable with the REST baseline. `--merge-adjacent` opts back in. Check `config.merge_adjacent` before comparing
precision or mAP between runs; merging changes the scored intervals and may
also change the retrieval candidate pool.

## Pipeline stages

A run is three stages — **ingest → retrieve → score** — connected by on-disk
artifacts. `--stage {run,ingest,retrieve,score}` runs one and stops; `run` (the
default) chains the same stages and adds durable recovery metadata.

The load-bearing artifact is the **retrieval artifact**: the raw hits plus the
run context, written by `--stage retrieve` (or a default run given
`--retrieval-out`). `--stage score --retrieval-in <artifact>` re-scores it
**without touching the deployment** — the same retrieval can be re-scored under
a different `--scoring` model or different flags. This is how you iterate on
metrics without re-querying.

`--ingest-out` / `--ingest-in` carry the ingest manifest between a standalone
ingest and a later retrieve. `--endpoint` is required for every stage except
`--stage score`, which is fully offline.

## Interrupted campaigns and resume

Each retrieval saves completed queries atomically to
`<retrieval-path>.checkpoint/`. Each query has its own file, so saving progress
does not repeatedly rewrite the entire gallery ranking. The checkpoint header
records the cohort and a fingerprint of the source code, effective retrieval and
critic settings, decomposition prompt/model, deployed model identifiers,
annotation files, ingestion identity, and supplied preprocessing provenance.
A fatal CLI failure or keyboard interruption also writes an **aborted retrieval
artifact** containing the completed rankings and the failure. Resume it before
scoring; an aborted artifact is not a completed benchmark.

Use `--resume-from <checkpoint-directory-or-retrieval-artifact>` with the same
inputs and settings. Answered queries are reused; missing, failed, or corrupt
query checkpoints run again. A corrupt header or changed campaign identity
blocks reuse. A fresh run cannot overwrite an existing checkpoint; choose a new
run name or output path for another round. Outputs and evidence use atomic
publication, so a failed write preserves the previous complete document.

Resume requires a readable persistent ingestion identity: the gallery's
`--ingestion-manifest`, or the same `--ingest-in` produced by a standalone legacy
`--stage ingest --ingest-out` run. Without it, the checkpoint remains inspectable
but the runner cannot verify unchanged indexed data and refuses reuse. Keep the
underlying deployment unchanged; the identity file cannot detect external
re-ingestion that somebody performs without updating that file.

For gallery scenarios, select exactly the scenario being resumed. `--stage run`
resumes only with `--skip-ingest`; `--stage retrieve` already leaves ingestion
unchanged. Cleanup or re-ingestion cannot accompany resume.

```bash
# Add the same endpoint, model, cohort and other settings as the original run.
--stage retrieve --gallery-gt /path/to/release --ingestion-manifest ingestion.json \
    --scenarios live-routed --retrieval-out round1 \
    --resume-from round1.live-routed.retrieval.json.checkpoint
```

An exact gallery list is validated before cleanup/upload, including media in
multiple directories. Relative `video`/`video_path` values in merged gallery JSON
are resolved against that JSON's parent directory. Only declared clips are
uploaded; unrelated neighboring files are excluded. A failed upload or index
validation leaves the previous successful ingestion manifest intact. Index
readiness probes use a cosine floor of `-1` and disable critic verification.

## Clip-level scoring (clip-relevance / drive)

`--scoring` selects the metric model. `segment-overlap` (default) scores
timestamp-overlap against segment ground truth. `clip-relevance` and `drive`
score a **clip gallery** (the Drive release format) instead of segments.

Both clip models need `--gallery-gt <release>` (a dir with `gt/queries_gt.json`

+ `additional_gt/clips_gt.json`, or a merged `{gallery, queries}` json) and
`--ingestion-manifest` (the clip→sensor map recorded at ingest). Retrieve for
them with `--full-gallery` (dense: every clip, cosine floor −1, no merge), then
score from the artifact. `drive` additionally needs `--drive-scorer-dir` (the
directory holding the original `evaluate_domain_test.py`, which it loads for
byte-level metric parity). `drive` requires every gallery clip returned exactly
once; `clip-relevance` tolerates partial coverage and labels it
(`mAP_scope: observed-ranking`).

`--pas-frame-supported` (clip-relevance only) rewrites PAS queries'
`relevant_clip_ids` from the released positive tracks whose representative frame
supports the resolved attributes (PhysicalAI v1.1.2); it needs the release-
directory layout (`additional_gt/tracks_pas_attributes.json`). PAS queries
with no supported positive are excluded and listed in a `cohort` block. See
`references/clip-scoring.md` for the metric definitions and the PAS derivation.

---

## Flags not covered here

This document explains the flags that change what a run *means*. The rest are
tuning and escape hatches -- retry budgets, readiness timeouts, explicit service
URLs, preflight skips -- and are described by `--help`:

```
--min-cosine-similarity  --source-type          --decompositions
--upload-timestamp       --complete-backoff     --complete-retries
--readiness-timeout      --skip-readiness-wait  --skip-vss-preflight
--skip-vss-configure     --vst-port             --vst-url
--vss-origin-port        --vss-base-url
```

Reach for them when a default is wrong for your deployment, not routinely: each
one moves a run further from the baseline everything else is compared against.

## Cookbook

```bash
# Everything: ingest + routed CLI queries
--dataset warehouse

# Query only
--dataset warehouse --skip-download --skip-ingest

# Use a local dataset instead of DSS
--data-dir ~/Desktop/vss-eval-datasets --dataset warehouse --skip-download

# Isolate this dataset by deleting foreign sources (explicit and destructive)
--dataset warehouse --only-dataset --confirm-delete

# Fresh index, wipes everyone's data (destructive)
--dataset warehouse --clear --confirm-delete

# Default/shared box — do not touch others' data
--dataset warehouse --skip-existing

# One fixed path instead of routing (baseline only -- not an eval)
--fixed-search-path --search-path fusion --attribute "person wearing a hardhat"

# Replay per-query routes stored in an extended dataset, without a live LLM
--no-decompose

# Ask the critic the reconstructed question, as runs before 3.8.0 did
--no-original-query

# The older agent-mediated ingest, when deployed VIOS webhooks are off
--ingest-flow agent-3step

# Longer wait for webhook-driven perception to populate the index
--index-probe-attempts 20 --index-probe-backoff 30

# Name the results file, for comparing two runs deliberately
--dataset warehouse --skip-download --name embed-baseline

# See what would run, touching nothing
--dry-run

# Retrieve once, score later -- and re-score without re-querying
--stage retrieve --skip-ingest --retrieval-out run.retrieval.json
--stage score --retrieval-in run.retrieval.json

# Clip-level scoring against a gallery (dense retrieve + clip/drive metrics)
--stage retrieve --gallery-gt /path/to/release --full-gallery --skip-ingest --retrieval-out gal.retrieval.json
--stage score --retrieval-in gal.retrieval.json --scoring clip-relevance --gallery-gt /path/to/release --ingestion-manifest ingestion.json
--stage score --retrieval-in gal.retrieval.json --scoring drive --gallery-gt /path/to/release --ingestion-manifest ingestion.json --drive-scorer-dir /path/to/drive/scripts

# Clip-relevance with frame-supported PAS ground truth (PhysicalAI v1.1.2)
--stage score --retrieval-in gal.retrieval.json --scoring clip-relevance --gallery-gt /path/to/release --ingestion-manifest ingestion.json --pas-frame-supported
```

`--concurrency` defaults to 1 and should stay there for any run whose latency
numbers you intend to read: concurrent queries contend for the same VLM and
embedding services, so every per-stage figure inflates. Raise it only when you
want wall-clock and are reporting retrieval quality alone — CLI queries take
~8 s each, so a 673-query set is hours at 1.

---

## Gallery comparisons

With `--gallery-gt`, `run` and `retrieve` default to three independent scenarios:
`embed-only`, `attribute-only`, and `live-routed`. They share one ingestion and
write `<base>.<scenario>.retrieval.json`; `run` also writes scored results and
`<base>.comparison.json`. Select fewer with `--scenarios embed-only attribute-only`.
Explicit legacy `--search-path`, `--fixed-search-path`, `--no-decompose`, and
`--full-gallery` retain a single run and conflict with `--scenarios`.

Fixed baselines encode the original query. For attribute extraction analysis,
use `--attribute-input decomposed --decompositions <artifact-or-evidence-directory>`.
This forces attribute retrieval, rejects missing/empty/malformed attributes, and
ignores decomposition-derived source/time filters. `--query-domain pas` selects
PAS queries while retaining the gallery. Released PAS requirements appear only
in evidence, never as retrieval inputs.

`--decomposition-out <directory>` saves each submitted prompt/model/settings,
raw response before parsing, parsed fields, timing, errors, and selected CLI
arguments. These files survive later failures. Both this directory and a saved
retrieval artifact can be replayed using `--decompositions`; replay makes no LLM
call and reports replay timing separately. Entries use `query_id` when present;
legacy text keys are accepted only for unique query text. Corrupt directory files
are reported individually while valid entries remain replayable. Invalid typed
fields remain query failures, never an implicit route or original-query fallback.
Routing consistency measures agreement
with the saved decomposition and is separate from semantic correctness.

Fixed gallery baselines use exact indexed-vector scans with
`--candidate-limit 100000` by default, embed cosine floor `-1` and attribute
shifted-score floor `0`. The candidate budget counts indexed vector documents
before deduplication; overflow fails rather than returning a truncated ranking.
`--top-k` remains a metric cutoff. Indexed coverage and annotation-gallery
coverage have different denominators. Live-routed retains the selected path's
normal retrieval behavior and fusion semantics.

Gallery scenarios default to critic off. Enable with `--critic` and optionally
`--critic-eval-count`, `--critic-frame-budget`, `--critic-input-width`,
`--critic-input-height`, and `--critic-diagnostic-dir`. Explicit critic runs
verify five candidates by default. All raw candidates remain in the retrieval
artifact for offline scoring; the critic does not fetch replacement candidates.

Preprocessing and run identity are recorded in artifact provenance. Supply effective
ingestion preprocessing/model settings using `--preprocessing-provenance settings.json`.
Changes to preprocessing require re-ingestion. See [validation limits](focused-validation.md)
for the opt-in Cosmos file controls and remaining deployment checks.

### Explicit ingestion trigger strategy

The default `--ingest-flow vst-direct` requires `--webhooks-confirmed` before
any cleanup or upload. Set it only after checking the deployed VIOS add webhooks,
RTVI model names and required perception services. Otherwise select
`--ingest-flow agent-3step`; it triggers processing explicitly. Both strategies
run post-ingestion embedding and attribute readiness checks. On Brev, preserve
service-returned upload URLs and verify they are reachable from the agent.

`--clear` validates deletion response bodies and the resulting VST inventory.
It is video cleanup, not an index/Kafka/analytics reset. Partial deletion, an
unreadable inventory or remaining registrations abort ingestion.

### Explicit inference controls and returned pools

`--llm-chat-template-kwargs '{"enable_thinking":false}'` passes a strict JSON
object to fresh decomposition calls and records it in request evidence and
provenance. Omission preserves the deployed model's defaults; explicit `{}` is
recorded. Non-object and nonfinite settings are rejected. Replay makes no fresh
inference call and does not apply new chat-template settings to saved decisions.

`--retrieval-pool-size N` caps the returned candidate pool before critic filtering.
It applies to run/retrieve and conflicts with `--full-gallery`. It is separate
from the indexed-document `--candidate-limit`, metric cutoffs and the critic's
verification count. Explicitly smaller top-K requests inside a decomposition
remain smaller. Gallery fixed baselines still score the bounded exact indexed
universe, but return only the requested pool; a limited returned ranking is not
a full-gallery mAP measurement. Existing offline scoring reports annotation
coverage separately. Live-routed search keeps the selected path's ordinary
ranking/fusion semantics and budgets.

`--ingestion-validation-contract contract.json` adds complete bounded model/vector
and decoded-frame checks after gallery ingestion. It requires local `ffprobe`,
index probes and an explicit embedding contract. See
[indexed-content validation](ingestion-content.md) for schema, failure evidence
and standalone-ingest receipts. No per-clip detection requirement is introduced.

The [archive port assessment](archive-port-assessment.md) records which Round8e
changes were incorporated and why checkpoint compatibility and clip diversity
remain separate follow-up work.


## Integration policy for upstream ingestion monitoring

This staged runner uses its existing configured `vss search readiness` gate.
The upstream background watcher, direct Elasticsearch/RT-CV polling controls
(`--ingest-require`, `--ingest-quiet-s`, `--ingest-absent-grace-s`,
`--ingest-strict`, and `--legacy-index-probe`) are not active runner options.
Their integration is deferred; do not use them to weaken readiness checks.

Fresh uploads use `video_file` sources. `--source-type rtsp` therefore requires
`--skip-ingest` and evaluates already registered sources. Reusing an index
requires the requested dataset sources to be registered; inspect the recorded
reused-index inventory and report other indexed sources separately. No cleanup
is implied by `--skip-ingest`, standalone retrieval, or offline scoring.

Decomposition failures remain explicit failed queries. There is no silent
original-query embedding fallback and no `--max-decompose-failed-pct` control.
Saved decompositions are replayed without an inference request.


## Attribute frame scoring and transport

`--frame-scoring auto|source|indexed` and
`--frame-lookup-transport auto|single|msearch` default to automatic compatibility
selection. Use `vss search frame-capabilities --source-type video_file --raw` to
inspect the configured backend without inference. Applicable retrieval plans
capture this profile before resume validation; a changed profile requires a new
run. Missing time bounds skip frame refinement rather than inventing a scope.
Attempted failures preserve candidates and diagnostics as unanswered-query
evidence. See [frame refinement](frame-refinement.md) for compatibility rules,
automatic batching, scoring corrections, and local measurement limits.


## Attribute gallery source diversity

`--max-hits-per-source N` is an opt-in file-gallery policy for the `attribute-only`
scenario. It requires exact scanning and `clip-relevance` or `drive` scoring;
other selected scenarios retain their normal behavior. The original complete
ranking is saved independently of the selected return/critic pool. Invalid
configurations fail before ingestion. Do not pass this retrieval option for
`--stage score`; saved policy evidence drives offline views. See
[source diversity](source-diversity.md) for complete-ranking requirements,
budgets, coverage, scorer compatibility, and interpretation.
