## Description: <br>
Use this skill when producing a VSS analysis report — Mode A per-clip VLM, Mode B incident-range via video-analytics. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
Developers and engineers use this skill to generate structured video analysis reports from recorded clips (via VLM) or incident-range analytics on the NVIDIA Video Search and Summarization platform. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [NVIDIA AI Blueprint: Video Search and Summarization (GitHub)](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [Video Analysis Report Template](assets/video-analysis-report.md) <br>
- [Incident Range Report Template](assets/incident-range-report.md) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, Markdown report] <br>
**Output Format:** [Markdown] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [Mode A produces a Video Analysis Report; Mode B produces an Incident Range Report] <br>

## Evaluation Agents Used: <br>
- `claude-code` <br>
- `codex` <br>



## Evaluation Tasks: <br>
Evaluated against 3 evaluation tasks (3 positive skill-activation cases) using NVSkills-Eval external profile in an astra-sandbox environment. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Checks whether skill-assisted execution avoids unsafe behavior such as secret leakage, destructive commands, or unauthorized access. <br>
- Correctness: Checks whether the agent follows the expected workflow and produces the correct final output. <br>
- Discoverability: Checks whether the agent loads the skill when relevant and avoids using it when irrelevant. <br>
- Effectiveness: Checks whether the agent performs measurably better with the skill than without it. <br>
- Efficiency: Checks whether the agent uses fewer tokens and avoids redundant work. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Verifies that the agent loaded the expected skill and workflow. <br>
- `skill_efficiency`: Checks routing quality, decoy avoidance, and redundant tool usage. <br>
- `accuracy`: Grades final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Checks whether the overall user task completed successfully. <br>
- `behavior_check`: Verifies expected behavior steps, including safety expectations. <br>
- `token_efficiency`: Compares token usage with and without the skill. <br>



## Evaluation Results: <br>
| Dimension | Num | `claude-code` | `codex` |
|---|---:|---:|---:|
| Security | 3 | 100% (+0%) | 100% (+0%) |
| Correctness | 3 | 55% (+46%) | 32% (+18%) |
| Discoverability | 3 | 11% (+3%) | 8% (-10%) |
| Effectiveness | 3 | 63% (+58%) | 34% (+29%) |
| Efficiency | 3 | 26% (-0%) | 28% (-10%) |

## Skill Version(s): <br>
3.2.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
