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

## Severity-word change (#476): evaluation, 2026-11-03 (owner-approved)

**Change.** The console now normalises severity words in code before Ultra reads a request (`chart-severity-words-v2`).
- **Rule:** a word maps to a band only when every severity level the project's definitions tie it to lies in that one band. The definitions are:
  - the severity rubric, shared by the P1 prompt and the ground-truth Severity Guide;
  - the importer's `mapSeverity`;
  - the dashboard bands (low 1–2, medium 3, high 4–5).
- **Mapped:** minimal and very low → low; minor → low (the rubric uses "minor" only at levels 1–2); moderate → medium; severe and critical → high.
- **Not mapped:**
  - serious and significant, which span bands;
  - mild, which the definitions don't use;
  - major, extreme and catastrophic. They are one-band under the rule, but this change was limited to minor by instruction.
- **Unchanged:** the chart prompt bytes. The table's sha256 is `b644908a05154374…`.

**Runs.**
- All three: Nemotron 3 Ultra via Switchyard, sequential, branch commit `804f85172`.
- Usage: 123 of the 142 approved attempts and 114,022 of the 213,000 tokens. 0 retries, 0 invalid replies.

| Experiment | Config sha256 | Requests | Shape |
|---|---|---|---|
| `v2-c1c-dashboard-severity-words-rb2` | `6f6472126b08c461…` | the 75 RB2 dev requests | `agent-route-v3` (normalised) |
| `v2-c1c-dashboard-severity-supplement-v2` | `4c4d60c892a217f2…` | 24 rule-written dev requests (`dashboard-severity-supplement-v2`) | `agent-route-v2` (current) |
| `v2-c1c-dashboard-severity-supplement-v3` | `cd07a0aba5035423…` | the same 24 | `agent-route-v3` |

**Comparisons and records.**
- The RB2 baseline is the earlier `agent-route-v2` regression run: same requests, same route, normaliser off.
- The original pilot (`console-gateway-v1`) is kept unchanged and is not pooled.
- The decision metric is spec/refusal correctness. Figures follow from the spec, and the RB2 corpus snapshot includes reports of held-out clips, so it was not used for development.
- The normaliser was frozen before these runs.

| Set | v2 (current) | v3 (normalised) | Paired difference, 95% CI | McNemar exact p |
|---|---|---|---|---|
| RB2, all 75 | 0.88 (66) | **0.92 (69)** | +0.04 [−0.03, +0.11] | 0.45 |
| RB2, the 28 requests the normaliser changes | 0.75 (21) | **0.89 (25)** | +0.14 [0.00, +0.32] | 0.22 |
| RB2, the 47 unchanged (stability) | 0.96 (45) | 0.94 (44) | −0.02 | 1.0 |
| RB2, 15 that must be refused | 15/15 | 15/15 | 0 | — |
| Supplement, 12 valid | 0.75 (9) | **1.00 (12)** | +0.25 [0.00, +0.50] | 0.25 |
| Supplement, 12 that must be refused | 12/12 | 12/12 | 0 | — |

- **Errors, RB2:**
  - False refusals: 8 under v2, 6 under v3.
  - Wrong specs: 1 under v2, 0 under v3.
  - **False acceptances: 0 in both.**
  - Invalid or unparseable replies: 0 in both.
- **Errors, supplement:**
  - False refusals: 3 under v2, 0 under v3.
  - **False acceptances: 0 in both.** This includes "only critical (severity 5), excluding severity 4", which a band cannot express, and "minor incidents at the north gate".
- **"Minor" (new in v2):** the 8 RB2 requests that use it went from 5 correct under v2 to 8 under v3.
- **Regressions:**
  - One changed request flipped from correct to refused: "how many severe animal attacks involved people, animals or unknown entities?" became "high-severity …".
  - One unchanged request, sent byte-identically, also flipped in the same run.
  - Temperature 0 is not bit-deterministic on Switchyard, so a single flip cannot be attributed to the normaliser. It is reported as unexplained.
- **Refusals that remain (6, v3):**
  - 2 degenerate groupings: "by incident type" within one type, and monthly counts over one week.
  - 1 run-to-run flip on an identical request.
  - 3 that Ultra refuses for reasons other than severity: "property" for assets, a threat-level chart with a severity filter, and the regression above.

**Assessment.**
- Development correctness improves, and every must-refuse request is still refused.
- No schema failure and no new false acceptance appeared.
- The gain is consistent in direction but **not statistically significant** at n = 75 (CI includes 0).
- Results are PROVISIONAL: the references are AI-validated or rule-written, and Human-unverified.
- The owner decides on merging.

## What stays open

1. ~~Open items~~: done (recovery, above).
2. ~~Regression check~~: done (above).
3. **Provisional status:** RB2 is `ai_validated`. The human review is replaced by an AI-only review (owner decision, 2026-11-03), so the scores stay provisional and Human-unverified.
4. **Decision for the owner:** whether to merge #476 (deterministic severity-word normalisation; evaluated above). It is a production change, and it must land before the C2 freeze if it is to be evaluated in C3.
