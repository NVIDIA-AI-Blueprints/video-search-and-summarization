# Running the eval

> **Status of this document.** The resources-server path **has now been run end to
> end against a live VSS deployment** (2026-08-18): the policy called the tool,
> VSS answered from the video, an independent judge graded the answer against a
> rubric, and the run returned **reward 1.0** with a judge rationale citing the
> answer's content. Statements are marked ✅ verified or ⚠️ not yet verified. Do
> not promote an unverified line to verified without a run.

Gym is a **two-phase** system. Nothing about it maps onto a single Compose
`command:`, which is why the runner service in `references/delta.md` declares
none:

1. `gym env start` brings up long-lived servers — a model server and a resources
   server — and a head server that brokers them.
2. `gym eval run` drives a rollout against those servers and writes results.

## Phase 0 — stage the server into a Gym checkout

✅ `gym env start` resolves each server's directory **relative to the working
directory**, and dataset paths in the config resolve from the Gym checkout root.
Copy this skill's server in, so both agree:

```bash
# Obtain the vss_ask_video resources server from the Gym repository, then:
cp -r <path-to>/vss_ask_video <gym-checkout>/resources_servers/vss_ask_video
cd <gym-checkout>
```

The server lives with Gym rather than in this repository: it implements Gym's
interface and imports Gym, which VSS never runs.

Everything below runs from `<gym-checkout>`. The config is then
`resources_servers/vss_ask_video/configs/vss_ask_video.yaml`, and its
`jsonl_fpath` of `resources_servers/vss_ask_video/data/example.jsonl` resolves.

## Phase 1 — start the servers

```bash
export VSS_JUDGE_API_KEY=<credential for the judge endpoint in the config>

gym env start \
  --config resources_servers/vss_ask_video/configs/vss_ask_video.yaml \
  --model-type inference_provider \
  --model "<model id served by the deployment>" \
  --model-url "http://localhost:<published-llm-port>/v1" \
  --model-api-key "<any non-empty string for a local NIM>" \
  +skip_venv_if_present=true
```

✅ **`--model-type inference_provider`, not `openai_model`.** `openai_model` posts
to `/v1/responses`; a VSS NIM serves `/v1/chat/completions` and returns
chat-completions usage fields, which fails schema validation on
`NeMoGymResponse`. `inference_provider` speaks the endpoint the deployment
actually has.

✅ **The server refuses to start without judge settings.** `judge_base_url`,
`judge_model` and `judge_api_key` are all required and validated non-empty, so a
missing credential fails at startup rather than as an opaque HTTP error mid-run.

✅ **Point the policy at the deployment's own LLM.** A VSS deployment already
serves an OpenAI-compatible endpoint; using it avoids an external provider key
entirely and scores against the model the deployment actually runs. Read the
published port from the NIM container rather than assuming one:

```bash
docker port <llm-container> | head -1        # e.g. 8000/tcp -> 0.0.0.0:30081
curl -sf http://localhost:<port>/v1/models   # confirm the model id
```

✅ **`+skip_venv_if_present=true` with a pre-built venv.** Gym's own dependency
set and a full Harbor install cannot always co-resolve; reusing one venv that
already satisfies both avoids a fresh resolution that may be unsatisfiable.
Symlink each server's `.venv` at that environment before starting.

✅ **Ops hygiene, learned the hard way.** The head server binds **port 11000**;
only one `gym env start` may run at a time, and the port must be free before
relaunch. Do **not** `pkill -f 'gym env start'` — the pattern matches the
launching shell and kills it. Stopping Gym does not disturb the VSS containers;
that was checked.

✅ **Confirm the servers registered** before collecting. Startup prints each
server with its resolved port; a resources server that failed to import will
still show a head server on 11000, so check the server list rather than the port.

## Phase 2 — collect

```bash
gym eval run --no-serve \
  --agent <agent name> \
  --input <input>.jsonl \
  --output <rollouts>.jsonl \
  --limit null --num-repeats null --concurrency 1
```

✅ **Verified for the resources-server path** (2026-08-18): this exact command
shape collected a rollout with a genuine reward against a live VSS deployment.

✅ **`/aggregate_metrics` 404 at the end is cosmetic.** The rollout file is
written *before* that step, so the command may exit non-zero with valid output.
Check for the output file rather than trusting the exit code.

## Reward traps

**A reward of 0.0 is not evidence the model did badly.** Two failure modes return
0.0 without erroring, and both look identical to a genuine miss:

- ✅ **A path the server cannot read.** In the prototype, a relative results
  directory resolved differently inside a Ray actor than where the job wrote,
  giving `FileNotFoundError` inside an exception handler that returns
  `reward=0.0` and an empty output. **Use absolute paths for anything the server
  reads back.** *Note: this specific trap belongs to Gym's `harbor_agent`; it is
  recorded here because the shape — an exception handler that degrades to a zero
  reward — is the class of bug to look for in any server.*
- ⚠️ **A policy too weak for the harness.** The prototype recorded empty
  `response.output` with reward 0.0 under a 9B model, because a terminal-style
  agent needs a stronger policy. Unmeasured for this skill's server.

**Diagnostic:** reward 0.0 *together with* an empty `response.output` means the
server hit an exception handler, not that the answer was wrong. Grep the server
log for the error before concluding anything about model quality.

### ⛔ A broken judge can score 1.0 — check the rationale, not the number

✅ **Observed 2026-08-18, not hypothetical.** A run reported:

```
mean/reward: 1.0   mean/check_rubric_pass: 1.0   mean/verifier_ok: 1.0
```

while the judge had never run:

```
judge_rationale: [judge error] HTTPStatusError: Client error '400 Bad Request'
```

The prototype server treated an unreachable or unparseable judge as *no verdict*
and fell back to a keyword check, so **a judge outage produced a passing score**.
An expired key would have turned an entire eval green.

**Fixed in this skill's server, and flagging alone was not enough.** Gym's
`reward_profile` aggregates every numeric and boolean field independently and
does **not** filter on `verifier_ok`, so a flagged row still headlines
`mean/reward 1.0`. The correct mechanism is Gym's own: a failed judge *call*
raises `JudgeError`, `judge_failsafe` tags the row `_ng_failure_class="judge_failed"`,
and rollout collection routes it to `<output>_failures.jsonl` — excluded from the
aggregate and retryable on resume. Verified by control: with an invalid judge key
the run produced **zero rows in the main output**, a populated failures sidecar,
and **no aggregate metrics at all**.

The boundary matters (`nemo_gym/judge.py`): a failed **call** — transport,
timeout, auth, HTTP — raises; a **received-but-unparseable** response is a
legitimate wrong answer and is scored, not raised.

This is worse than the reward-0.0 traps above: a false zero gets investigated, a
false pass gets reported as success. Two rules follow:

- **Never accept an aggregate without inspecting `judge_rationale`.** A genuine
  verdict cites the answer's content; a fallback carries a `[judge error]` or
  `[unparseable]` marker.
- **A judge failure must be visible in the metrics.** Note that `verifier_ok` was
  `1.0` during the failure above, so it does not track judge health. Any server
  shipped from this skill must surface judge failure distinctly and must not
  silently substitute a weaker scorer — substituting scorers mid-run is exactly
  what makes two scores incomparable.

**Cause of that particular 400:** the judge payload sent `temperature`, which
reasoning models such as `gpt-5.6-sol` reject outright — it is not the token
field, which is the intuitive guess. `max_completion_tokens` without
`temperature` returns 200.

## Known upstream collision — the CI corpus path only

Driving VSS's **CI skill-eval corpus** through Gym needs `harbor_agent`, which
pins a January 2026 Harbor commit and imports `LocalDatasetConfig` — a symbol
absent from `harbor==0.20.0`, which is what `.github/skill-eval/run_leg.py:53`
pins. Running them together fails at request time:

```
POST /run -> 500
ImportError: cannot import name 'LocalDatasetConfig' from 'harbor.models.job.config'
```

✅ Confirmed still present on Gym's `r0.5.1` release branch. The dependency root
is Gym's `openai<=2.7.2` ceiling, which forbids the `litellm` that Harbor 0.20+
requires. Tracked upstream in
[NVIDIA-NeMo/Gym#2596](https://github.com/NVIDIA-NeMo/Gym/pull/2596).

**This does not affect the path this skill uses.** A resources server has no
Harbor dependency at all.
