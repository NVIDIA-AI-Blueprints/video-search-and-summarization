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

## What stays open

1. **Open items:** 33 of the 225 work items are open (Nano 10, Super 11, Ultra 12). Rate-limit retries during the concurrent first chat run consumed the approved 225-call cap. Per-model results cover 63–65 of 75 requests each, and the denominators are stated.
2. **Optional regression check (needs approval):** the 75 requests to Ultra through the `agent-route-v2` shape. That is 75 calls, at most 112,500 tokens reserved, on Switchyard (no charge to the project). It would confirm empirically that `max_completion_tokens` and the agent path change nothing.
3. **Provisional status:** RB2 is `ai_validated`, not human-verified, so dashboard scores stay provisional until its 30-record human review.
