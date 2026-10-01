# ACES Evaluation — `vss-benchmark-video-ingest`

NV-ACES eval dataset and run guidance for this skill. The point of these evals is to
measure whether an agent behaves *better* with the skill than without it — not whether
the skill's scripts run.

Reference: [NV-ACES: Agentic Continuous Evaluation of Skills](https://nvidia.atlassian.net/wiki/spaces/GAIT/pages/3503574496/NV-ACES+Agentic+Continuous+Evaluation+of+Skills)

## What is being evaluated

The failure modes this skill exists to prevent are all *judgment* failures that a
capable agent makes readily without it:

| Failure mode | Case that catches it |
|---|---|
| Reaching for `kubectl`, Helm, Prometheus, or Elasticsearch beyond readiness | `explicit-001` |
| Not recognizing an ingest-benchmark request unless it is named | `implicit-001` |
| Naming an internal bottleneck from client-side data alone | `contextual-001` |
| Agreeing to raise a server-side limit to make a sweep fit | `contextual-001` |
| Mixing latency measured at two different definitions of "finished" | `contextual-002` |
| Treating the skill as a tuning workflow | `negative-001` |
| Over-triggering on any VSS search request | `negative-002` |

`contextual-001` is the highest-value case. An agent without the skill will almost
always answer "the bottleneck is RT-CV / the GPU / embedding" and will usually agree to
raise the stream limit. Both are wrong, and neither is obviously wrong without the
Phase 1 boundary.

## Coverage

| Type | Cases |
|---|---|
| Explicit activation | `explicit-001` |
| Implicit activation | `implicit-001` |
| Contextual activation | `contextual-001`, `contextual-002` |
| Negative routing | `negative-001`, `negative-002` |

## Success criteria

A run passes when, per `evals/config.yml`, at least 60% of cases meet their
`expected_behavior` list. Beyond the pass rate, the reviewer should confirm:

- **Security:** no token value appears in any transcript; no credential is passed on a
  command line; no `kubectl`, `helm`, or direct backend mutation is proposed.
- **Correctness:** validation runs before uploads; Elasticsearch readiness is
  configured; the transfer projection is surfaced before a multi-GB run.
- **Discoverability:** the skill activates on `implicit-001` and does **not** activate
  on `negative-002`.
- **Effectiveness:** the with-skill answers to `contextual-001` and `contextual-002`
  differ materially from the baseline. If they do not, the skill is not earning its
  context.

A high pass rate with near-zero Skill Lift on the contextual cases is a failure signal,
not a success.

## Local requirements

- `astra-skill-eval` installed and on `PATH`
- `NVIDIA_INFERENCE_KEY` exported for live agent evals
- Network access to NVIDIA inference endpoints
- A supported local agent — `codex` is the default below; `claude-code` also works
- Python 3.10+

No VSS deployment, corpus, or GPU is needed. Every case is a reasoning and routing
case; none requires an actual benchmark run.

## Running the evals

Acceptance runs **must** include the baseline comparison. Do not use `--skip-baseline`
for acceptance unless a reviewer explicitly asks for a faster diagnostic run.

```bash
SKILL_REPO="$(git rev-parse --show-toplevel)" || exit 1
SKILL_SRC="$SKILL_REPO/skills/benchmarking/vss-benchmark-video-ingest"
test -f "$SKILL_SRC/SKILL.md" || exit 1
astra-skill-eval evaluate "$SKILL_SRC" --agent-eval -a codex --env-mode local
```

To keep generated artifacts out of the skill directory:

```bash
SKILL_REPO="$(git rev-parse --show-toplevel)" || exit 1
SKILL_SRC="$SKILL_REPO/skills/benchmarking/vss-benchmark-video-ingest"
test -f "$SKILL_SRC/SKILL.md" || exit 1
astra-skill-eval evaluate "$SKILL_SRC" --agent-eval -a codex --env-mode local \
  --results-dir /tmp/astra-skill-eval-results
```

## Reading the results

- **Pass rate** — cases meeting their `expected_behavior` list, against the 0.60
  threshold.
- **Skill Lift** — with-skill minus without-skill, per dimension. This is the number
  that matters. Watch `contextual-001` and `contextual-002` specifically.
- **Metric deltas** — Security, Correctness, Discoverability, Effectiveness,
  Efficiency. Security should be at or near 100% on both arms; a baseline security
  drop usually means the agent reached for `kubectl`.
- **Report output** — written under the results directory as HTML and JSON.

## Known environment issues

**Python cannot find a CA bundle.** Symptom: `SSLCertVerificationError` on the first
inference call.

```bash
export SSL_CERT_FILE="$(python3 -m certifi)"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
```

**The agent tries to run the benchmark for real.** These cases are reasoning cases and
name a fictitious endpoint. An agent that attempts a live run will fail validation at
the health check, which is acceptable — grade the reasoning, not the exit code.

**Long transcripts on `explicit-001`.** That case asks for a full walkthrough. If token
efficiency scores low there but Correctness is high, check whether the agent inlined
reference material that `SKILL.md` links to instead.

## Generated results

`astra-skill-eval` writes reports and JSON under `evals/results/`. They are useful
locally and **must not** be committed. Keep this ignore rule in the repository:

```gitignore
**/evals/results/
```
