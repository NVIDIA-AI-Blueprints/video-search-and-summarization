## Description: <br>
Use this skill when operating VSS alert workflows — real-time monitoring, Alert-Bridge subscriptions, verification verdicts, on-demand verification, always-on operation, Slack notifications, incident queries, or camera onboarding. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and operators managing NVIDIA Video Search and Summarization (VSS) deployments who need to operate alert pipelines — configuring real-time monitoring rules, querying detected incidents, managing Slack notifications, inspecting verification verdicts, and onboarding cameras. <br>

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
- [Alert Notifications Reference](references/alert-notify.md) <br>
- [Alert Subscriptions Reference](references/alert-subscriptions.md) <br>
- [Always-On Alerting Reference](references/always-on.md) <br>
- [CV Verifier Prompts Reference](references/cv-verifier-prompts.md) <br>
- [Deploy Alerts Reference](references/deploy-alerts.md) <br>
- [Integrate Alerts Reference](references/integrate-alerts.md) <br>
- [On-Demand Verification Reference](references/on-demand-verification.md) <br>
- [Query Incidents Reference](references/query-incidents.md) <br>
- [Verification Reference](references/verification.md) <br>
- [VSS Blueprint Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [VSS GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, API Calls, Configuration instructions, Analysis] <br>
**Output Format:** [Markdown with inline bash code blocks and JSON API responses] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
11 evaluation tasks (10 positive, 1 negative) covering alert routing, rule CRUD, incident queries, mode-gated refusals, on-demand verification, Slack webhook status, always-on operation, and negative non-alert analytics routing. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Verifies final-answer correctness against the reference answer. <br>
- Discoverability: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- Effectiveness: Equal-weight mean of goal completion (goal_accuracy) and expected workflow adherence (behavior_check). <br>
- Efficiency: 50% tool-call productivity and 50% token efficiency. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `skill_efficiency`: Tool-call productivity (routing scored under Discoverability). <br>
- `token_efficiency`: Actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 88.6% | 74.8% |
| Security | 100.0% → 100.0% (±0.0 pts) | 54.6% → 59.1% (+4.5 pts) |
| Correctness | 9.1% → 89.1% (+80.0 pts) | 41.8% → 85.5% (+43.7 pts) |
| Discoverability | 99.8% | 85.5% |
| Effectiveness | 23.0% → 64.5% (+41.5 pts) | 34.7% → 60.2% (+25.5 pts) |
| Efficiency | 89.3% | 83.6% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
