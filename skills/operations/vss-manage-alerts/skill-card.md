## Description: <br>
Use this skill when operating VSS alert workflows — real-time monitoring, Alert-Bridge subscriptions, verification verdicts, on-demand verification, always-on operation, Slack notifications, incident queries, or camera onboarding. Not for non-alert analytics. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and operators of an NVIDIA Video Search and Summarization (VSS) alerts deployment use this skill to detect the deployed alert mode and drive the Alert Bridge: create, list and stop real-time alert rules, query incidents, inspect CV verification verdicts, run on-demand verification, check always-on alerting, manage Slack notifications, and onboard cameras. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [Yes] <br>
**Credential Type(s):** [API key] <br>

Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [Alert Notify](references/alert-notify.md) <br>
- [Alert Subscriptions](references/alert-subscriptions.md) <br>
- [Always-On Operation](references/always-on.md) <br>
- [CV Verifier Prompts & Verdicts](references/cv-verifier-prompts.md) <br>
- [Deployment Reference: Alert Microservice](references/deploy-alerts.md) <br>
- [Integration Reference: Alert Microservice](references/integrate-alerts.md) <br>
- [On-Demand Verification](references/on-demand-verification.md) <br>
- [Query Incidents](references/query-incidents.md) <br>
- [Verification Results & Verdicts](references/verification.md) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [VSS Blueprint on GitHub](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS on build.nvidia.com](https://build.nvidia.com/nvidia/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, API Calls, Analysis] <br>
**Output Format:** [Markdown with inline bash code blocks and JSON API responses] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-5-5`) <br>
- Codex (`openai/openai/gpt-6.1-sol`) <br>



## Evaluation Tasks: <br>
11 evaluation tasks (10 positive, 1 negative), 1 attempt per task, each run in its own isolated sandbox pod in the `k8s-sandbox` environment; each task was also attempted without the skill as a baseline. Evaluator version `1.6.9`, evaluation date 2026-11-16. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Is it safe to use? Scored from `security` (100%). <br>
- Correctness: Is the answer correct? Scored from `accuracy` (100%). <br>
- Discoverability: Was the right skill loaded when needed? Scored from `skill_execution` (100%). <br>
- Effectiveness: Did the skill help complete the task? Equal-weight mean of `goal_accuracy` (50%) and `behavior_check` (50%). <br>
- Efficiency: Did it avoid wasted tool calls and token usage? `skill_efficiency` (50%) plus `token_efficiency` (50%). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity (legacy wire id; routing is scored under Discoverability). <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion usage (50% of Efficiency). <br>



## Evaluation Results: <br>
**Overall verdict:** ⚠️ INCOMPLETE — Required evidence is missing

One or more required evaluation tiers did not complete, so this benchmark is not publication-complete.

### Results at a Glance

| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 83.0% — baseline ran, but no comparable score was available; uplift unavailable | 68.9% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 90.9% → 86.4% (-4.5 points) | 54.6% → 72.7% (+18.1 points) |
| Correctness | 25.5% → 92.7% (+67.2 points) | 10.9% → 52.7% (+41.8 points) |
| Discoverability | 97.2% — baseline ran, but no comparable score was available; uplift unavailable | 87.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 22.4% → 55.4% (+33.0 points) | 21.2% → 39.5% (+18.3 points) |
| Efficiency | 83.4% — baseline ran, but no comparable score was available; uplift unavailable | 91.8% — baseline ran, but no comparable score was available; uplift unavailable |

Baseline is the same task attempted without the target skill. Uplift is shown in percentage points. A partial dimension was calculated from only the available configured signals.

#### Comparison details

| Agent | Dimension | Basis | Baseline → With skill (uplift) | Explanation |
| --- | --- | --- | --- | --- |
| claude-code | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| claude-code | Efficiency | Token-only comparison | 98.1% → 89.6% (-8.5 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |
| codex | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| codex | Efficiency | Token-only comparison | 92.5% → 88.2% (-4.3 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |

### Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 43 finding(s) |
| Tier 2 | Semantic deduplication | **INCOMPLETE** | Tier 2 scan incomplete: a content cluster exceeds the LLM prompt character limit. |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 11 task(s) |

Incomplete coverage: Context Deduplication — Tier 2 scan incomplete: a content cluster exceeds the LLM prompt character limit.

## Skill Version(s): <br>
3.3.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
