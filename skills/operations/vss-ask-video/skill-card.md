## Description: <br>
Answers questions about previously analyzed or freshly scoped VSS video by routing through conversation context, agent memory, VSS structured memory, introspection, or direct VLM inspection via the `vss` CLI. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers use this skill to answer visual questions about VSS video streams, retrieve stored VSS memory jobs and records, run introspection queries against a deployed VSS stack, and perform direct VLM inspection of sensor windows, URLs, or local files. <br>

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
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
17 evaluation tasks (16 positive, 1 negative) from skill-evaluator-dataset-snapshot/1, each run in an isolated sandbox pod. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether the skill avoids unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Whether the final answer is correct against the reference answer. <br>
- Discoverability: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- Effectiveness: Whether the skill helped complete the user's goal (goal completion and expected workflow adherence). <br>
- Efficiency: Whether the skill avoided wasted tool calls and token usage. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected and the workflow executed. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `skill_efficiency`: Tool-call productivity (routing scored under Discoverability). <br>
- `token_efficiency`: Actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code | Codex |
|---|---:|---:|
| Overall | 87.4% | 86.1% |
| Security | 94.1% | 100.0% |
| Correctness | 94.1% | 78.8% |
| Discoverability | 80.1% | 94.4% |
| Effectiveness | 78.5% | 62.6% |
| Efficiency | 90.4% | 94.6% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
