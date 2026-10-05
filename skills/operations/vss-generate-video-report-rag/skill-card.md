## Description: <br>
Generates VSS video summary reports with LVS HITL and optional Enterprise RAG document grounding. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers who need to generate video summary reports enhanced with Enterprise RAG knowledge context using the VSS LVS pipeline, including guided human-in-the-loop parameter collection for scenario, events, and objects of interest. <br>

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
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [VSS Blueprint on build.nvidia.com](https://build.nvidia.com/nvidia/video-search-and-summarization) <br>
- [Video Search and Summarization GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, API Calls] <br>
**Output Format:** [Markdown with embedded JSON API responses] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Reports may include PDF download links; processing takes 3–5 minutes for a ~3.5 minute video] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
3 evaluation tasks (2 positive, 1 negative) from skill-evaluator-dataset-snapshot, evaluated in isolated k8s-sandbox pods. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether the skill is safe to use: checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Whether the answer is correct against the reference answer. <br>
- Discoverability: Whether the right skill was loaded when needed: skill selection, decoy avoidance, and workflow execution. <br>
- Effectiveness: Whether the skill helped complete the task: goal completion (50%) and expected workflow adherence (50%). <br>
- Efficiency: Whether the skill avoided wasted tool calls and token usage: tool-call productivity (50%) and token efficiency (50%). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity (routing is scored under Discoverability). <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 72.1% | 68.5% |
| Security | 100.0% → 83.3% (-16.7 points) | 100.0% → 83.3% (-16.7 points) |
| Correctness | 0.0% → 86.7% (+86.7 points) | 60.0% → 73.3% (+13.3 points) |
| Discoverability | 50.0% | 47.5% |
| Effectiveness | 16.4% → 55.8% (+39.4 points) | 46.9% → 39.4% (-7.5 points) |
| Efficiency | 84.9% | 98.9% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
