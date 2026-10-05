## Description: <br>
Use this skill when a user wants to search archived VSS video or ingest or delete a source for search. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers who need to search archived video using natural-language or similarity queries, ingest video sources for search, or delete previously ingested sources on VSS deployments. <br>

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
- [vss search run reference](references/cli_usage.md) <br>
- [Search-result verification](references/result_verification.md) <br>
- [Search source lifecycle](references/source_lifecycle.md) <br>
- [NVIDIA Video Search and Summarization Demo](https://build.nvidia.com/nvidia/video-search-and-summarization) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, Analysis, Configuration instructions] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
11 evaluation tasks (9 positive, 2 negative) run in isolated sandbox pods against a k8s-sandbox environment. Dataset digest: sha256:d3d57e60e47f9a1f556d52df6af4589543e1834407e038b4dd114809b7176346. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether the skill is safe to use: checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Whether the final answer is correct against the reference answer. <br>
- Discoverability: Whether the right skill was loaded when needed: skill selection, decoy avoidance, and workflow execution. <br>
- Effectiveness: Whether the skill helped complete the user's goal, scored as equal-weight mean of goal completion and expected workflow adherence. <br>
- Efficiency: Whether the skill avoided wasted tool calls and token usage, scored as 50% tool-call productivity and 50% token efficiency. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `skill_efficiency`: Tool-call productivity (routing scored under Discoverability, not Efficiency). <br>
- `token_efficiency`: Actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 72.6% | 64.1% |
| Security | 86.4% → 100.0% (+13.6 pts) | 68.2% → 100.0% (+31.8 pts) |
| Correctness | 21.8% → 60.0% (+38.2 pts) | 32.7% → 29.1% (-3.6 pts) |
| Discoverability | 84.2% | 78.3% |
| Effectiveness | 27.1% → 35.3% (+8.2 pts) | 29.1% → 30.5% (+1.4 pts) |
| Efficiency | 83.6% | 82.5% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
