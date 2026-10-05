## Description: <br>
Use this skill when producing a VSS analysis report — Mode A per-clip VLM, Mode B incident-range via video-analytics, Mode C SOP compliance via the SOP tools. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers use this skill to generate structured video analysis reports from VSS deployments — per-clip VLM analysis (Mode A), incident-range narrative reports (Mode B), or SOP compliance reports (Mode C). <br>

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
- [NVIDIA AI Blueprint: Video Search and Summarization](https://build.nvidia.com/nvidia/video-search-and-summarization) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [Default VLM Prompt](references/default-vlm-prompt.md) <br>
- [Video Analysis Report Type](references/report-types/video-analysis.md) <br>
- [Incident Range Report Type](references/report-types/incident-range.md) <br>
- [SOP Compliance Report Type](references/report-types/sop-compliance.md) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands] <br>
**Output Format:** [Markdown] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
14 evaluation tasks (13 positive, 1 negative) across isolated sandbox pods in a k8s-sandbox environment. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether the skill is safe to use, checking for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Whether the skill produces correct answers against the reference answer. <br>
- Discoverability: Whether the right skill was loaded when needed, including skill selection and decoy avoidance. <br>
- Effectiveness: Whether the skill helps complete the user's goal (50% goal completion + 50% expected workflow adherence). <br>
- Efficiency: Whether the skill avoids wasted tool calls and token usage (50% tool-call productivity + 50% token efficiency). <br>

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
| Overall | 90.8% | 88.4% |
| Security | 100.0% → 100.0% (±0.0 pts) | 100.0% → 100.0% (±0.0 pts) |
| Correctness | 12.9% → 94.3% (+81.4 pts) | 38.6% → 91.4% (+52.8 pts) |
| Discoverability | 84.6% | 71.5% |
| Effectiveness | 3.4% → 83.5% (+80.1 pts) | 12.7% → 81.7% (+69.0 pts) |
| Efficiency | 91.6% | 97.3% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
