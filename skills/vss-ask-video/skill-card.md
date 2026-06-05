## Description: <br>
Use to ask the VSS agent's video_understanding tool a fresh visual question about a recorded clip. Not for prior tool output, search hits, or metadata-answerable questions. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
Developers and engineers building video analytics applications who need to ask visual questions about recorded video clips using the VSS agent's video understanding tool. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [NVIDIA AI Blueprint: Video Search and Summarization (GitHub)](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>


## Skill Output: <br>
**Output Type(s):** [API Calls, Analysis] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- `claude-code` <br>
- `codex` <br>



## Evaluation Tasks: <br>
Evaluated against 1 evaluation task (positive skill-activation case) with 2 attempts per task in astra-sandbox environment using NVSkills-Eval external profile. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: checks whether skill-assisted execution avoids unsafe behavior such as secret leakage, destructive commands, or unauthorized access. <br>
- Correctness: checks whether the agent follows the expected workflow and produces the correct final output. <br>
- Discoverability: checks whether the agent loads the skill when relevant and avoids using it when irrelevant. <br>
- Effectiveness: checks whether the agent performs measurably better with the skill than without it. <br>
- Efficiency: checks whether the agent uses fewer tokens and avoids redundant work. <br>

Underlying evaluation signals used in this run: <br>
- `security`: checks for unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: verifies that the agent loaded the expected skill and workflow. <br>
- `skill_efficiency`: checks routing quality, decoy avoidance, and redundant tool usage. <br>
- `accuracy`: grades final-answer correctness against the reference answer. <br>
- `goal_accuracy`: checks whether the overall user task completed successfully. <br>
- `behavior_check`: verifies expected behavior steps, including safety expectations. <br>
- `token_efficiency`: compares token usage with and without the skill. <br>



## Evaluation Results: <br>
| Dimension | Num | `claude-code` | `codex` |
|---|---:|---:|---:|
| Security | 2 | 100% (+0%) | 100% (+50%) |
| Correctness | 2 | 45% (+32%) | 72% (+41%) |
| Discoverability | 2 | 25% (+12%) | 97% (+44%) |
| Effectiveness | 2 | 26% (+16%) | 31% (+19%) |
| Efficiency | 2 | 25% (-1%) | 95% (+48%) |

## Skill Version(s): <br>
3.2.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
