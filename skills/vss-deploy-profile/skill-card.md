## Description: <br>
Use to select, configure, deploy, verify, debug, or tear down a VSS profile (base, search, lvs, warehouse, edge). <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
Developers and engineers deploying, configuring, verifying, debugging, or tearing down NVIDIA Video Search and Summarization (VSS) compose-based profiles on GPU-equipped hosts. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [VSS Prerequisites](https://docs.nvidia.com/vss/3.2.0/prerequisites.html) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [Profile reference: base](references/base.md) <br>
- [Profile reference: search](references/search.md) <br>
- [Profile reference: lvs](references/lvs-profile.md) <br>
- [Profile reference: warehouse](references/warehouse.md) <br>
- [Profile reference: alerts](references/alerts.md) <br>
- [Profile reference: edge](references/edge.md) <br>
- [Troubleshooting](references/troubleshooting.md) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, Configuration instructions] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- `claude-code` <br>
- `codex` <br>



## Evaluation Tasks: <br>
Evaluated against 5 evaluation tasks in astra-sandbox environment using NVSkills-Eval external profile with 2 attempts per task and a 50% pass threshold. <br>

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
| Security | 8 | 100% (+0%) | 85% (-15%) |
| Correctness | 8 | 93% (+68%) | 87% (+52%) |
| Discoverability | 8 | 92% (+58%) | 84% (+30%) |
| Effectiveness | 8 | 65% (+59%) | 64% (+58%) |
| Efficiency | 8 | 75% (+40%) | 77% (+27%) |

## Skill Version(s): <br>
3.2.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
