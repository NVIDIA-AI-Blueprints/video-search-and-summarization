# Evaluation V2: does #459 invalidate the C1c dashboard pilot? (assessed 2026-11-03)

- **Pilot:** `v2-c1c-dashboard-pilot`, run 2026-11-02 from `eval/v2-dashboard-runner` at `68aa883e8`, against main at `d432095ea`.
- **Change assessed:** main at `f8c357e8b`, which includes teammate PR #459 (`9b2d09925`, "dashboard chart requests are read by the VSS agent's own LLM").
- **Other changes since the pilot:** they touch the dashboard either not at all or only through docs. #453 (`eeaafaf55`) only adds a privacy-blur removal migration, with no dashboard SQL. #460–#471 change box, tracking and UI features outside the dashboard.

## What #459 changed, and the evidence

| Aspect | Before (pilot) | After #459 | Effect on the evaluation |
|---|---|---|---|
| Where the request is read | Console → laptop `vlm-gateway` → model | Console → agent `POST /api/v1/incidents/chart-requests` → agent's LLM | Transport only |
| System prompt | `contracts/prompts/chart/system.txt`, SHA-256 `c6cf07800e90541b…` | **Same bytes**; the golden now references it by hash | None |
| User message | The typed request (trimmed, ≤ 300 characters) | The same | None |
| Model | Console `CHART_MODEL` default `nvidia/nemotron-3-ultra` | Agent `chart_generator.llm_name` = `${LLM_MODEL_TYPE}_llm`; the VM runs `openai_llm` with `LLM_NAME` `nvidia/nemotron-3-ultra` (`.docs/status.md` §6) | None for Ultra |
| Temperature | 0 | 0.0 (`openai_llm` entry) | None |
| Output cap | `max_tokens` 200 | `llm.bind(max_tokens=200)`, which LangChain posts as `max_completion_tokens` 200 (checked offline by building the request) | Same cap, newer parameter name |
| Thinking | `enable_thinking: false` | `${LLM_ENABLE_THINKING:-false}` | None (default) |
| Reply extraction | First `{` to last `}`, else null (`parseReply`) | The same rule in the agent (`json_object_in`); additionally a non-object becomes null | None: a non-object always failed the allowlist check |
| Validation and figures | Console `parseChartSpec` allowlist, figures from the database | Unchanged | None |
| Timeout | Console 45 s | Agent 40 s on the model call | None: the slowest pilot reply was 11.4 s (Ultra p95 4.3 s) |
| Errors | Gateway error → 502 | 501 (no chart LLM configured) or 502 | Not exercised by the pilot (no reply failed) |

## Classification

- **Nemotron 3 Ultra, the production chart model: A, no evaluation impact.** The model receives the same prompt bytes, the same user message and the same decoding settings. Its reply is extracted and validated by the same rules. **The 2026-11-02 pilot remains the valid development evaluation of the current dashboard.**
- **Nemotron 3 Nano and Super arms: A for the request, informational for production.** The request they received is the one Ultra receives today. Since #459, production uses only the agent's `LLM_NAME`, so these arms are model comparisons, not alternative production configurations.
- **Harness: compatibility update needed (done in `eval/v2-dashboard-runner`).** The golden is now the console's two messages to the agent.
  - The harness keeps the pilot's `console-gateway-v1` request shape for the sealed record and resumes.
  - It adds `agent-route-v2`, which is the agent's upstream body. Tests pin it to the agent's own config and route constants.
  - New runs set `tasks.dashboard.options.request_shape: "agent-route-v2"`.

**Decision: no rerun.** A regression run on the agent route would need new inference. All 225 approved dashboard calls are used, so it is proposed, not run: §3.

## Follow-up runs (owner-approved 2026-11-03) and final development results

**What ran:**
- **Recovery:** `c1c-dashboard-recovery`, the 33 open items on the pilot's `console-gateway-v1` shape. 33 calls of the approved 45; 33/33 OK.
- **Ultra regression:** `c1c-dashboard-ultra-regression`, all 75 requests on `agent-route-v2`. 75 calls of the approved 75; 75/75 OK.
- **Scoring:** both are scored with `evalv2-dashboard-score-v1`, with figures from the SQL harness.
- **Records kept:** the original pilot record is unchanged. The recovery and pilot share no item.

**Final development results (pilot + recovery):**
- Every model now has 75/75 assigned requests answered: 0 unanswered, 0 failed, 0 unattempted.
- So accuracy over answered requests equals overall success over assigned requests.
- "Correct" means the chart figures equal the reference chart, or a correct refusal.
- 95% intervals: record bootstrap, 10,000 resamples, seed 20261027.

| Model | Correct / assigned | 95% CI | Refused a valid request | Invalid spec (no chart) | False accepts | Wrong figures |
|---|---|---|---|---|---|---|
| Nemotron 3 Ultra (production) | **0.87** (65/75) | 0.79–0.93 | 9 | 0 | 0 | 1 |
| Nemotron 3 Super 120B | **0.96** (72/75) | 0.91–1.00 | 1 | 2 | 0 | 0 |
| Nemotron 3 Nano 30B | **0.84** (63/75) | 0.75–0.92 | 0 | 2 | 5 | 5 |

**Paired differences** (same 75 requests):
- Super − Ultra: +0.09 [0.01, 0.19]. Super alone was correct on 9 requests, Ultra alone on 2.
- Super − Nano: +0.12 [0.03, 0.21].
- Ultra − Nano: +0.03 [−0.08, 0.13].

**Ultra regression on the current agent route:**
- The agent route scored 0.88 (66/75), against 0.87 (65/75) for the pilot shape.
- 74/75 items have the same outcome and the same spec or refusal.
- The one change is a borderline request, which flipped from a false refusal to an exact match.
- Conclusion: no material difference. The pilot + recovery result stands for the production route.
- The runs are reported side by side, not pooled.

**Why Ultra trails Super: severity synonyms.**
- The chart prompt lists severity only as `low`/`medium`/`high`. It also says to reply `{"unsupported": true}` if any part of a request "cannot be expressed with exactly these values".
- Ultra applies this literally to "minor", "moderate" and "severe":
  - **with a severity synonym** (29 supported requests): Ultra 7 false refusals, Super 1, Nano 0;
  - **without a synonym** (31 supported requests): Ultra 2 false refusals, Super 0, Nano 0.
- Ultra's other two refusals are degenerate groupings: "by incident type" when already filtered to one type, and "monthly counts" over one week.
- The references map synonyms to bands (minor→low, and so on). That is an interpretation, and the AI-only reference review checks whether a refusal is also acceptable.
- A prompt line mapping synonyms would likely remove most of these refusals. That would be a production prompt change, decided separately and not made here.

## What stays open

1. ~~Open items~~: done (recovery, above).
2. ~~Regression check~~: done (above).
3. **Provisional status:** RB2 is `ai_validated`. The human review is replaced by an AI-only review (owner decision, 2026-11-03), so the scores stay provisional and Human-unverified.
4. **Decision for the owner:** whether to add a severity-synonym line to the chart prompt. It is a production change, and it must land before the C2 freeze if it is to be evaluated in C3.
