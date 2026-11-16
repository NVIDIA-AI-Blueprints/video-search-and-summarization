## Description: <br>
Use this skill when operating VSS alert workflows — real-time monitoring, Alert-Bridge subscriptions, verification verdicts, on-demand verification, always-on operation, Slack notifications, incident queries, or camera onboarding; not for non-alert analytics. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and operators of NVIDIA Video Search and Summarization (VSS) deployments use this skill to operate the alerts profile: detect the deployed mode, manage Alert-Bridge real-time alert rules, review verification verdicts, run on-demand clip verification, manage always-on operation, set up Slack or dashboard incident notifications, query incidents, and onboard cameras. <br>

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
- [Video Search and Summarization Blueprint (GitHub)](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, API Calls, Analysis, Configuration instructions] <br>
**Output Format:** [Markdown with inline bash code blocks and JSON API responses] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
11 evaluation tasks (10 positive, 1 negative), 1 attempt per task, each run in an isolated sandbox pod (Skill Evaluator 1.5.6, `k8s-sandbox` environment). <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Is it safe to use? Scored from the `security` signal. <br>
- Correctness: Is the answer correct? Scored from the `accuracy` signal. <br>
- Discoverability: Was the right skill loaded when needed? Scored from the `skill_execution` signal. <br>
- Effectiveness: Did the skill help complete the task? Equal-weight mean of `goal_accuracy` and `behavior_check`. <br>
- Efficiency: Did it avoid wasted tool calls and token usage? 50% `skill_efficiency` plus 50% `token_efficiency`. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 89.1% — baseline ran, but no comparable score was available; uplift unavailable | 69.6% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 95.5% (-4.5 points) | 45.5% → 45.5% (±0.0 points) |
| Correctness | 9.1% → 96.4% (+87.3 points) | 36.4% → 83.6% (+47.2 points) |
| Discoverability | 98.5% — baseline ran, but no comparable score was available; uplift unavailable | 80.0% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 23.2% → 66.3% (+43.1 points) | 28.8% → 55.2% (+26.4 points) |
| Efficiency | 89.0% — baseline ran, but no comparable score was available; uplift unavailable | 83.7% — baseline ran, but no comparable score was available; uplift unavailable |

Overall benchmark verdict: FAIL — publication blocked (Tier 1 static validation failed; blocking finding: BANDIT B104 hardcoded bind to all interfaces in `scripts/alert-notify/server.py`). Tier 2: passed with observations. Tier 3: pass.

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
