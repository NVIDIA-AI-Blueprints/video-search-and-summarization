# Evaluation V2: judge, capability probes and detector, ready to run (X18, X19, X5)

> **Status (2026-11-01): ready to run. Nothing has run.**
> - No model, embedding or Hugging Face call was made, no weights were downloaded, no package was installed, and nothing was written to a shared database or R2. Every part is tested offline with fakes (`tests/v2/test_v2_judges.py`, `test_v2_probe.py`, `test_v2_detector_env.py`).
> - **Approved, as relayed by the evaluation lead on 1 Nov:**
>   - the probe session: at most 12 calls and 0.3 M tokens;
>   - the judge: `openai/gpt-oss-20b` as primary and `mistralai/mistral-large-4-1t-a52b` for sensitivity, at most 2,000 calls and about 3 M tokens together.
>
>   The lead runs these. No other agent makes the calls.
> - **Proposals** awaiting the owner, not decisions: the continuity-judge budget, the detector (OWLv2 base), the environment install, and the weights download. None is in `.docs/decisions.md` yet.

## 1. Approvals, in order

| # | Step | Gate | Exact action | Limits and cost | State |
|---|---|---|---|---|---|
| 1 | Catalog key on the harness host | X18 (owner) | Put `NVIDIA_API_KEY` in the host's environment, for example in the profile's `.env.local`. It is never committed or printed. The Brev key gets 403 on the catalog. | Terms depend on the key: see section 5 | **Needed** before steps 2 and 3 can reach the catalog |
| 2 | Capability probes | Owner (relayed) | `python -m evalv2.probe --live --max-calls 11 --max-tokens 300000 --approval FILE`, section 3 | 11 calls, worst case 294,202 tokens (9 on Switchyard, 2 on the catalog) | Approved; run by the lead |
| 3 | Judge: primary and sensitivity | X19, owner (relayed) | `options.judges` in a run config, run through a judge stage (section 2) | 1,500 + 500 calls, 2.25 M + 0.75 M tokens (the `budget` in `judges.json`) | Approved; first use is C1 |
| 4 | Continuity judge (V1's Nemotron, Switchyard) | Owner | `--with-continuity-judge` on the probe, then the `continuity` slot | 1 probe call (2,237 tokens), then at most 1,500 calls and 2.25 M tokens; $0 to the project on Switchyard | **Not approved** |
| 5 | Detector environment | X5 (owner) | `python scripts/v2_detector_env.py install --yes` | About 250 MB of packages and 1.1 GB on disk (estimate; the manifest records the real figures). CPU only. No weights | **Not approved** |
| 6 | Detector weights | X5 (owner) | `<env>/venv/bin/python scripts/v2_detector_weights.py download --approval FILE` | About 620 MB from Hugging Face (Apache-2.0) | **Not approved** |
| 7 | Detector run on RA1's frames | RA1 (owner) | The RA1 runner with `SubprocessDetector(runner_command(...), expected_weights_sha256=...)` | 10,026 frames, about 25 CPU-hours on the evaluation laptop (section 4.4) | Part of RA1 |

## 2. The judge (X19)

### 2.1 Configuration (`evalv2/tasks/judges.json`)

| Id | Role | Model | Endpoint | Settings | Judges | Parse retries | Hard caps per judge stage |
|---|---|---|---|---|---|---|---|
| `catalog-gpt-oss-20b-v1` | primary | `openai/gpt-oss-20b` | `nvidia-api-catalog` | temperature 0, `max_tokens` 1,500 | incident, fact, unsupported, contradiction | 0 | 1,500 requests, 2,250,000 tokens |
| `catalog-mistral-large-4-v1` | sensitivity | `mistralai/mistral-large-4-1t-a52b` | `nvidia-api-catalog` | temperature 0, `max_tokens` 1,500 | incident, fact, unsupported, contradiction | 0 | 500 requests, 750,000 tokens |
| `switchyard-nemotron-3-nano-v1` | continuity | `nvidia/nemotron-3-nano-30b-a3b` | `switchyard` | V1's `JUDGE_REQUEST_SETTINGS` | incident only | 2 (as V1) | 1,500 requests, 2,250,000 tokens |

- **Endpoint and key:** the existing OpenAI-compatible adapter's `nvidia-api-catalog` endpoint. The base URL defaults to `https://integrate.api.nvidia.com/v1` (or `NVIDIA_API_BASE_URL`), and the key is `NVIDIA_API_KEY`. The key is sent as a bearer header only, and never stored, logged or put in the registry.
- **Approved budget:** the primary's and the sensitivity judge's caps sum to it, 2,000 calls and 3 M tokens. Loading the registry refuses caps that sum past it.
- **Request caps:** every request is capped twice.
  - Output: `max_tokens` 1,500, V1's limit, reasoning included.
  - Input: 65,536 bytes of message text. A longer input is unscored and never sent.
  - Before sending, each attempt is reserved on the `judge` ledger at its worst case: input bytes + 256 template tokens + 1,500.
- **Stage caps:** a judge stage asks one judge, through its own ledger (`judge_stage.judge_ledger`), so the judge's caps above always apply. A run config may only tighten them, with `caps` entries on the `judge` ledger.
- **Why these judges:**
  - The subjects are Cosmos and Nemotron, and Claude makes the references (methodology v2.0 §7). Switchyard lists only NVIDIA families (RB1), so the judge must come from the catalog.
  - `gpt-oss-20b` is a small, open-weight OpenAI model. Mistral Large 4 is a second family, distinct from the primary, which makes the sensitivity check meaningful.
  - V1's judge is kept only to compare with V1. It is the same family as the Omni and chat subjects.
- **Continuity:** `judge-prompt-v1` stays the incident-field prompt for every judge (methodology v2.0 §4 A), so the pass rate is comparable with V1.

### 2.2 Family check (`check_judge_family`, tested)

| Judge | Against each subject, then all at once: `cosmos-3-nano-reasoner`, `cosmos-3-super-reasoner`, `nemotron-3-nano-omni-30b-a3b-reasoning`, `nemotron-3-ultra`, `nemotron-3-ultra-550b-a55b`; generators `anthropic/claude`, `claude-opus-4`, `anthropic/claude-sonnet-4-5` |
|---|---|
| `openai/gpt-oss-20b` (family `gpt`) | **passes** |
| `mistralai/mistral-large-4-1t-a52b` (family `mistral`) | **passes** |
| `nvidia/nemotron-3-nano-30b-a3b`, `nvidia/nemotron-nano-3-30b-a3b`, `nvidia/llama-3.1-nemotron-ultra-253b-v1`, `nvidia/nemotron-3-super-120b-a12b` | **refused** (nemotron) |
| `nvidia/cosmos-reason2-8b`, `nvidia/cosmos-3-super-reasoner` | **refused** (cosmos) |
| `anthropic/claude-opus-4`, `claude-sonnet-4-5` | **refused** (claude) |

Selections are refused in these cases:
- a run's subject shares the judge's family (for example a `gpt-oss-120b` subject);
- the sensitivity judge shares the primary's family;
- V1's judge is put in any slot but `continuity`, or asked to judge chat checks.

The family rule reads model ids (`model_family`), so it cannot see indirect correlation, such as models trained on each other's synthetic data. Report that as a limitation.

### 2.3 Selecting a judge by config

A task names its judges in its options; `python -m evalv2 preflight` checks them.
- The ids must be registered and their roles must match their slots.
- Each judge must judge the task's kinds and pass the family rules.
- The run must cap the `judge` ledger.

```json
"tasks": {
  "incident_objects": {"options": {"judges": {"primary": "catalog-gpt-oss-20b-v1",
                                              "sensitivity": "catalog-mistral-large-4-v1"}}},
  "chat": {"options": {"judges": {"primary": "catalog-gpt-oss-20b-v1"}}}
},
"caps": [{"ledger": "judge", "max_attempts": 1500, "max_tokens": 2250000}]
```

- **Kinds:** `incident_objects` and `pipeline` use the `incident` kind (`judge-prompt-v1`), passed as `score_video(..., judge=judges["incident"])`. The chat scorer takes `judges` with `fact`, `unsupported` and `contradiction` (`score_answer(..., judges=...)`).
- **Offline:** `task_judges(config, task, cache_dir=D)` returns `CachedJudge`s, which never call anything.
- **In an approved judge stage:** `task_judges(..., ask=judge_stage.live_factory(store=..., ledger=judge_ledger(spec, run_caps, log), policy=POLICIES["retry-v2"], subject=..., task=...))` returns `RecordingJudge`s.
  - The cache directory must be private: a raw reply may quote ground-truth text.
  - No CLI starts a judge stage yet: it is part of the C1 executor.

### 2.4 Pinned requests and cache identity

- **Request:** the body is V1's: `model`, then a system message holding the prompt file and a user message `EXPECTED: …\nPREDICTED: …`, then the settings, in that order.
  - V1's own `eval_gt._judge_once` body is byte-equal to the continuity judge's.
  - `tests/v2/fixtures/judge-requests-v1.json` pins all nine judge-and-kind bodies, keys in order.
  - A fake transport shows the adapter sends exactly those bytes.
- **Cache identity:** the model, the prompt file's SHA-256, the endpoint, the request settings, the user template (`judge-user-v1`), the parse rule (`v1-extract-score`, V1's) and the parse retries. A test shows that changing any of them misses every cached verdict, and so does switching to the sensitivity or continuity judge.
- **Raw replies:** each is kept in the cache entry for audit.
- **Unscored, never a score:**
  - a failed call;
  - a reply cut off at `max_tokens`;
  - an input over the limit;
  - a reply without a score in [0, 1];
  - a call refused by the caps (it is never sent).

  A V2 judge never re-asks; the continuity judge re-asks twice, as V1 did.

### 2.5 Volume against the approved budget

| Use | Judged fields (upper bound, before cache hits) | Fits 2,000 calls? |
|---|---|---|
| C1a Track A pilot: 25 dev clips × 3 subjects × 2 repeats | 1,026 (171 judged fields per dev repeat, `experiments/judge-caps.json`) | Yes, primary |
| Sensitivity: Omni's C1a outputs plus about 100 human-calibration items | about 440 | Yes, sensitivity |
| C1b Track B pilot: 2 subjects × 2 repeats × 3 stages | up to 2,052 (stages share most texts, so the cache removes many) | Not inside this budget |
| C3, all tasks (55 test clips, chat) | about 9,000–11,000 | Needs a new budget at the freeze |

An empty predicted description scores 0 without a call (V1), and repeated pairs are cached, so real counts are lower. At 1,500 tokens per call on average, the token cap and the request cap bind at about the same point.

## 3. Capability probes (`python -m evalv2.probe`)

RB1 left these unverified: strict `json_schema` on the video endpoints, base64 video input, and effective frame sampling. The probes settle them, and check that each judge answers with the expected JSON.

### 3.1 The calls

Two dev clips are chosen by id: `RoadAccidents017` (8.1 s, 1,680,716 bytes) and `Burglary093` (25.4 s, 4,937,483 bytes).
- A test-half clip is refused.
- A clip must match the dataset manifest's size and SHA-256 before anything is sent.
- The inline limit is 8 MB raw, far below a gateway's request limit (Cloudflare refuses bodies over 100 MB on its default plans; Switchyard's LiteLLM and vLLM limits are unknown). A size refusal would itself be a finding.

| Probe | Per model | Clip and request | Calls | Worst case per call |
|---|---|---|---|---|
| `schema_inline` | Nano, Super, Omni (Switchyard) | Short clip inline (`data:video/mp4;base64,…`), `num_frames` 8, a strict schema whose keys the prompt never names | 1 each | 32,192 tokens (24,000 prompt + 8,192 output) |
| `frames_many` | Nano, Super, Omni | The same with `num_frames` 64 (the P1 setting) | 1 each | 32,192 |
| `long_clip` | Nano, Super, Omni | Long clip, `num_frames` 64 | 1 each | 32,192 |
| `judge` | gpt-oss-20b, Mistral Large 4 (catalog) | The production judge request (`judge-prompt-v1`) on a paraphrase pair | 1 each | 2,237 |
| Total | | | **11** | **294,202 tokens** (12 calls and 296,439 with `--with-continuity-judge`) |

- **Request settings:** every video request keeps P1's other settings (temperature 0) and uses only JSON-schema keywords the P1 contract uses.
- **No retries:** the `none` policy, so a call is one HTTP attempt.
- **Skips:** once a model's probe fails, its later probes are skipped. After a 401 or 403, the endpoint's later probes are skipped. A session can therefore send fewer calls than planned, never more.

### 3.2 Commands

```bash
cd deploy/docker/developer-profiles/dev-profile-incident/eval
uv run --frozen python -m evalv2.probe        # dry run: every request as stored, its hash and worst case; no network
uv run --frozen --env-file <profile>/.env.local python -m evalv2.probe --live \
    --max-calls 11 --max-tokens 300000 --approval <data root>/v2/probes/approval.json \
    --data-root <data root>                     # live: the lead, with the approval below
```

- **Environment:** the live run needs `VLM_GATEWAY_BASE_URL` and `VLM_GATEWAY_API_KEY` (Switchyard) and `NVIDIA_API_KEY` (catalog). It refuses, naming only the missing variables, before anything is sent.
- **Data root:** `--data-root` is the private data root holding the verified clips (`EVALV2_DATA_ROOT`, else `eval/eval_data`).
- **Selecting probes:** `--only`, `--models` and `--judges` select a subset.
- **Approval:** a live run needs `--live`, `--max-calls` (1 to 12) and `--max-tokens` at or above the plan's worst case, plus an approval: a marker file, or `EVALV2_ALLOW_LIVE_PROBE=1`. A marker's caps bind, and its optional `kinds`, `models` and `judges` lists limit what may run:

```json
{"approval": "evalv2-probe-approval-v1", "approved_by": "<owner>", "approved_at": "2026-11-01",
 "max_calls": 12, "max_tokens": 300000, "note": "Evaluation V2 capability probes (RB1 follow-up)"}
```

### 3.3 Outputs and how to read them

- **Where:** `<data root>/v2/probes/` (private: the replies describe dataset clips).
  - `probes.jsonl` is append-only: a session line (approval, caps, plan, clips), one line per probe (request hash and artifact references, usage, timing, findings), and a summary.
  - The request and response artifacts live under `store/`. A stored request keeps an inline clip's size and SHA-256, not its bytes. Echoed base64 in an error is scrubbed.
  - The budget log is under `budget/<session>.jsonl`.
- **Hashes:** each dry-run `request_sha256` equals the hash of what the live session stores.
- **Inline video:** answered means accepted. An HTTP error or a non-completion means refused. A rate limit or timeout means unknown.
- **Strict schema:** the reply is exactly a JSON object valid against the probe schema, alone or after a `<think>` block. Prose or invalid JSON means the schema is not enforced. A truncated reply is inconclusive.
- **Frame sampling:** read from provider-reported prompt tokens, the method behind `analysis-schema.md` §7 and the 31 Oct chunked-captioning decision.
  - 64 frames against 8 on the short clip: more than 10% more tokens means `num_frames` is honoured, and the difference per frame is the frame cost. Within 2%, it is ignored.
  - The long clip against the short one at 64 frames: more means the sampling depends on clip length (a fixed frame rate). Equal means a fixed count.
  - The model's own frame count is recorded as a weak signal.
  - These thresholds only label the evidence; the raw tokens are reported.
  - An exact frame count needs a frame-numbered clip, a possible follow-up.
- **Judge:** answered, the expected `{"score", "reasoning"}` JSON alone (a fenced reply is scored but noted), the score of the paraphrase pair, and its reasoning tokens. Reasoning tokens near the 1,500 limit are a truncation risk (section 5).
- **After review:** a person copies the confirmed capabilities into `adapters/capabilities.json` (`verified: true`, with the session id). The probe never edits it.

## 4. The offline detector (X5)

### 4.1 Choice

The methodology names no detector ("an offline open-vocabulary detector", §3). **Proposed: OWLv2 base, `google/owlv2-base-patch16-ensemble`.**
- Apache-2.0, 155 M parameters (154,966,792), CPU-capable.
- Text-queried boxes for arbitrary item words.
- Supported in `transformers` (no custom code).
- It only proposes boxes: Claude picks among them or says none fits, and a blind validator checks.

| Alternative | Licence | Why not first |
|---|---|---|
| OWLv2 large (`owlv2-large-patch14-ensemble`) | Apache-2.0 | Better recall, but about 5× the compute (ViT-L/14 at 1,008 px: about 5.8 TFLOPs a frame against 1.1): far over a CPU budget |
| OWL-ViT v1 B/32 (`owlvit-base-patch32`) | Apache-2.0 | Much faster (768 px, 576 patches), but weaker on small objects, which CCTV items often are |
| Grounding DINO tiny or base (`IDEA-Research/grounding-dino-*`) | Apache-2.0 | Strong phrase grounding; slower on CPU and a heavier text-image fusion. The best second choice if OWLv2's proposals miss too often in RA1's first batch |
| OmDet-Turbo (`omlab/omdet-turbo-swin-tiny-hf`) | Apache-2.0 | Fast, but less proven on free-form phrases |
| Florence-2 (`microsoft/Florence-2-*`) | MIT | Generative (a small VLM), so its boxes depend on decoding and it is closer to the subjects' kind of model |
| YOLO-World (Ultralytics) | GPL-3.0 (original) and AGPL-3.0 (Ultralytics) | Licence |
| The production grounding VLM, or a GPU detector on the VM | — | Never makes references (methodology §3); a GPU job on the shared VM is a separate approval |

### 4.2 Environment (`scripts/v2_detector_env.py`)

- **Location:** `$EVALV2_DETECTOR_HOME`, default `~/.cache/evalv2-detector`, outside the repository. Delete it to throw it away.
- **Steps:**
  1. `uv venv --python 3.12`;
  2. `torch==2.14.0` from the PyTorch CPU index only, so no CUDA build can be picked;
  3. `scripts/v2_detector_requirements.txt` from PyPI: `transformers==5.17.0`, `tokenizers==0.23.2`, `huggingface-hub==1.32.0`, `safetensors==0.8.0`, `numpy==2.5.3`, `pillow==12.3.0`. These are eval's own `uv.lock` versions, and a test keeps them equal.
- **No torchvision:** transformers then uses OWLv2's PIL image processor. The class is recorded in the detector's identity.
- **Commands:** `plan` (the default) prints the commands and changes nothing. `install --yes` runs them, refuses over an existing environment, and writes `env-manifest.json`: the resolved packages (`uv pip freeze`), the requirements' SHA-256, versions, a CPU-build check and the size on disk. `check` re-checks offline.
- **Transitive packages** are pinned by the install's freeze record, not beforehand: a hash-locked install is a possible tightening.

### 4.3 Weights (`scripts/v2_detector_weights.py`), a separately approved step

- **Size:** `model.safetensors` is about 620 MB (155 M fp32 parameters), plus small JSON and text files. `pytorch_model.bin` and other formats are never fetched.
- **Refusals:** it refuses without an `evalv2-detector-weights-approval-v1` marker naming this repository, the approver and date, a revision (a commit, or `main`, resolved and recorded) and `max_download_mb`.
- **SHA-256 plan:**
  1. Read the Hub metadata at the revision: the commit, the licence (must be Apache-2.0), and each file's size and hash.
  2. Refuse before downloading when the files pass the ceiling, or when the marker's optional `expected_sha256` differs from the Hub's LFS SHA-256.
  3. Download, then re-hash: an LFS file must have the Hub's SHA-256 (and the marker's), a small file its git blob id.
  4. Only then write `evalv2-weights.lock.json`: every file's size and SHA-256, the commit, the licence and the approval's SHA-256.
- **At run time:** the runner re-hashes every locked file before each load and refuses any difference. Its replies carry `weights_sha256`, which each box reference records. `SubprocessDetector(expected_weights_sha256=...)` refuses a reply from any other weights.
- **Commands:** `--dry-run` prints the plan without importing `huggingface_hub`. `verify --weights-dir D` re-checks offline.

```json
{"approval": "evalv2-detector-weights-approval-v1", "repo_id": "google/owlv2-base-patch16-ensemble",
 "revision": "main", "approved_by": "<owner>", "approved_at": "<date>", "max_download_mb": 700}
```

### 4.4 Runner and CPU time

- **Runner:** `scripts/v2_detector.py --backend owlv2 --weights-dir D` speaks `evalv2-detector-v1` (`references/detector.py`).
  - It loads with `local_files_only`, runs on CPU with deterministic algorithms and a fixed thread count, and fits each query to OWLv2's 16 text tokens at a word boundary (the cut ones are reported).
  - It converts boxes from OWLv2's padded square to the agent's 0–1000 `[ymin, xmin, ymax, xmax]`, and drops boxes thinner than `MIN_SIDE`.
  - It suppresses overlaps within each query (IoU 0.5) and keeps the 5 best per query and at most 25 per frame, at score 0.1 or more.
  - These parameters are in the identity. `--backend stub` runs the same path with synthetic outputs, for tests.
- **Frames RA1 needs:** every second of each clip for the generator pass, plus the validator's frames half a second later (`frames_needed`, from the tracked manifest and `split-v2`). One pass per frame covers all of a clip's item queries.
  - dev: 25 clips, 1,612 + 1,597 = **3,209 frames**;
  - test: 55 clips, 3,424 + 3,393 = **6,817 frames**;
  - all 80 clips: **10,026 frames**.

  The run-readiness doc's RA1 plan supersedes these if it plans different frames.
- **Measured speed:** a randomly initialised OWLv2 B/16 at 960 px took **9.0 s per frame** on the evaluation laptop (Intel i7-1255U, WSL2, 7 GB; 8.8–9.1 s at 6 or 12 threads, batch 1, four queries). Random weights have the real model's architecture and FLOPs, and no weights were downloaded.
- **CPU time:**
  - dev: **8.0 h**;
  - test: **17.0 h**;
  - all: **25.1 h**.

  A desktop or server CPU is several times faster. Each reply reports its per-frame time, so RA1's first batch re-measures it: `uv run --frozen python scripts/v2_detector_env.py estimate --seconds-per-frame S` recomputes the totals. Sending one clip per detector request means a stopped run loses at most one clip.

## 5. Costs and risks

- **Catalog terms are unknown for the key that will be used.** The listing (RB1) was free and needed no key. Inference terms (free developer credits, rate limits such as requests per minute, or paid use) depend on the account behind `NVIDIA_API_KEY`. Read them on the account page before C1.
  - The probe spends 2 catalog calls (worst case 4,474 tokens).
  - The approved judge budget is at most 2,000 calls and 3 M tokens.
- **Switchyard** charges the project $0 (`experiments/pricing.json`). Its limits are 60 requests per minute and 4 in parallel; the 2 Oct benchmark hit Omni's 30,000 tokens-per-minute quota. The probes are sequential.
- **Reasoning truncation:** `gpt-oss-20b` reasons before answering, at its default effort.
  - Replies cut at 1,500 tokens are unscored and counted.
  - If the probe or the pilot shows reasoning near the limit, a new judge version (for example `reasoning_effort: low`, or a larger limit) gets a new id and identity. The old cache is never reused.
- **Availability:** catalog models can be withdrawn or changed. Each response's artifact keeps what the endpoint returned, and a changed judge means a new judge version.
- **Inline video:** may be refused or size-limited on some model. The schema and frame findings for that model then wait for URL input (X16 decides the scratch store).
- **Frame sampling:** the token method is indirect, and the model's own count is weak. Both are reported as evidence, not as a measured frame count.
- **Detector:**
  - **Small or dark items:** OWLv2 misses small or dark CCTV items at times. It only proposes, and none fits is an answer.
  - **CPU time:** about 25 h on the laptop, with 7 GB of WSL memory (batch 1 needs an estimated 2 GB).
  - **transformers 5 API:** the first approved batch is the smoke test.
- **Correlation:** Claude makes references, and a non-NVIDIA, non-Claude model judges. Indirect training overlap between model families cannot be ruled out by ids.

## 6. Code map

| Path | What |
|---|---|
| `evalv2/tasks/judges.json`, `evalv2/tasks/judges.py` | Registry, family and budget rules, selection (`options.judges`), identity, V1's parse rule, `LiveJudge`, `task_judges` |
| `evalv2/requests/judge.py` | The judge request (V1's body) and its worst-case token bound |
| `evalv2/judge_stage.py` | A judge stage's adapter, its own capped ledger, and the `ask` factory |
| `evalv2/cli.py` | `preflight` also checks judge selections and the judge-ledger cap |
| `evalv2/tasks/judging.py` | Verdicts keep the raw reply in the cache |
| `evalv2/probe.py` | The probes: plan, dry run, approval and caps, live session, findings |
| `evalv2/references/detector.py` | `runner_command`, `frames_needed`, the weights-SHA pin on `SubprocessDetector` |
| `scripts/v2_detector.py`, `v2_detector_env.py`, `v2_detector_weights.py`, `v2_detector_requirements.txt` | Runner, environment, approved weights step, pinned packages |
