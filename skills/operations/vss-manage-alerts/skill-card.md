## Description: <br>
Use this skill when operating VSS alert workflows — real-time monitoring, Alert-Bridge subscriptions, verification verdicts, on-demand verification, always-on operation, Slack notifications, incident queries, or camera onboarding. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers operating VSS alert pipelines for real-time video monitoring, Alert-Bridge subscription management, incident queries, verification verdicts, always-on alerting, Slack notifications, and camera onboarding. <br>

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
- [alert-notify.md](references/alert-notify.md) <br>
- [alert-subscriptions.md](references/alert-subscriptions.md) <br>
- [always-on.md](references/always-on.md) <br>
- [cv-verifier-prompts.md](references/cv-verifier-prompts.md) <br>
- [deploy-alerts.md](references/deploy-alerts.md) <br>
- [integrate-alerts.md](references/integrate-alerts.md) <br>
- [on-demand-verification.md](references/on-demand-verification.md) <br>
- [query-incidents.md](references/query-incidents.md) <br>
- [verification.md](references/verification.md) <br>
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, API Calls, Configuration instructions] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
11 evaluation tasks (10 positive, 1 negative) run in isolated sandbox pods with dataset digest sha256:2b5bf7deee2ba610968002c68c2365d6b4caef09ec9d3c359932dc32a2a099bb. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Checks final-answer correctness against the reference answer. <br>
- Discoverability: Checks whether the expected skill was selected and the workflow executed. <br>
- Effectiveness: Checks whether the user's goal was achieved and expected workflow behavior was followed (equal-weight mean of goal_accuracy and behavior_check). <br>
- Efficiency: Checks tool-call productivity and token efficiency (50% each). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Verifies absence of unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Verifies the expected skill was selected, decoys avoided, and the workflow executed. <br>
- `skill_efficiency`: Verifies tool-call productivity (routing scored under Discoverability). <br>
- `accuracy`: Verifies final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Verifies whether the user's goal was achieved. <br>
- `behavior_check`: Verifies whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Verifies actual uncached prompt plus completion usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | Not available | 73.6% |
| Security | Not available | 40.9% → 50.0% (+9.1 pts) |
| Correctness | Not available | 34.6% → 90.9% (+56.3 pts) |
| Discoverability | Not available | 82.0% |
| Effectiveness | Not available | 28.6% → 56.5% (+27.9 pts) |
| Efficiency | Not available | 88.5% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
