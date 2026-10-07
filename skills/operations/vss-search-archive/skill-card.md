## Description: <br>
Use this skill when a user wants to search archived VSS video or ingest or delete a source for search. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers operating NVIDIA VSS deployments who need to search archived video by natural-language or similarity query, or ingest and delete sources for search. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [Not Specified] <br>
**Credential Type(s):** [None identified] <br>

Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [CLI Usage](references/cli_usage.md) <br>
- [Result Verification](references/result_verification.md) <br>
- [Source Lifecycle](references/source_lifecycle.md) <br>
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands] <br>
**Output Format:** [Markdown with structured search results and inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
11 evaluation tasks (9 positive, 2 negative) from skill-evaluator-dataset-snapshot/1, each run in an isolated sandbox pod. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether the skill avoids unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Final-answer correctness against the reference answer. <br>
- Discoverability: Whether the expected skill was selected and activated when needed. <br>
- Effectiveness: Whether the skill helped complete the user's goal and followed expected workflow behavior. <br>
- Efficiency: Tool-call productivity and token efficiency, avoiding wasted skill and tool usage. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `skill_efficiency`: Tool-call productivity (routing scored under Discoverability). <br>
- `token_efficiency`: Actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 68.7% | 61.6% |
| Security | 95.5% → 100.0% (+4.5 points) | 81.8% → 90.9% (+9.1 points) |
| Correctness | 12.7% → 52.7% (+40.0 points) | 20.0% → 30.9% (+10.9 points) |
| Discoverability | 74.4% | 70.6% |
| Effectiveness | 25.8% → 29.7% (+3.9 points) | 22.9% → 26.5% (+3.6 points) |
| Efficiency | 86.4% | 89.2% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
