## Description: <br>
Add agent-ready vision capabilities — dense captioning, detection, search, alerting, summarization — to an agent or application through a customizable, self-contained vision stack built on the NVIDIA VSS Blueprint. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers who need to compose, configure, and deploy a self-contained vision AI application stack — selecting capabilities such as dense captioning, detection, search, alerting, and summarization — on the NVIDIA VSS Blueprint. <br>

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
- [NVIDIA AI Blueprint: Video Search and Summarization](https://build.nvidia.com/nvidia/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [Composition Rules](references/composition.md) <br>
- [Deployment Lifecycle](references/deployment.md) <br>
- [Agent Harness](references/agent-harness.md) <br>
- [Prerequisites](references/prerequisites.md) <br>
- [Profile Sizing](references/sizing.md) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, Configuration instructions, Files] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
11 evaluation tasks (10 positive, 1 negative) run in isolated sandbox pods; evaluator version 1.5.6. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Final-answer correctness against the reference answer. <br>
- Discoverability: Whether the right skill was loaded when needed, decoys avoided, and workflow executed. <br>
- Effectiveness: Goal completion (50%) and expected workflow adherence (50%). <br>
- Efficiency: Tool-call productivity (50%) and token efficiency (50%). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `skill_efficiency`: Tool-call productivity (legacy wire id; routing scored under Discoverability). <br>
- `token_efficiency`: Actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Dimension | Claude Code | Codex |
|---|---:|---:|
| Overall | 67.8% | 61.5% |
| Security | 68.2% (-9.1 pp vs baseline) | 54.6% (+18.2 pp vs baseline) |
| Correctness | 69.1% (+47.3 pp vs baseline) | 70.9% (+10.9 pp vs baseline) |
| Discoverability | 99.1% | 78.5% |
| Effectiveness | 22.2% (+11.9 pp vs baseline) | 29.1% (+7.7 pp vs baseline) |
| Efficiency | 80.4% | 74.4% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
