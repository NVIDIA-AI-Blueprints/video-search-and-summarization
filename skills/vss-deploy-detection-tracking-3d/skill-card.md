## Description: <br>
Deploy and operate the RTVI-CV-3D stack (also known as MV3DT, Multi-View 3D Tracking, or RTVI-CV-MV3DT) — per-camera DeepStream perception plus BEV Fusion over multiple calibrated cameras. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
Developers and engineers use this skill to deploy and operate multi-view 3D tracking (RTVI-CV-3D / MV3DT) pipelines for multi-camera video analytics on sample data, custom videos, or live RTSP streams without the full warehouse agent / LLM / VLM stack. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [Video Search and Summarization GitHub](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [Deploy RTVI-CV-3D Stack](references/deploy-rtvi-cv-3d-stack.md) <br>
- [Calibration Workflow](references/calibration-workflow.md) <br>
- [Configure Cameras](references/configure-cameras.md) <br>
- [Verify and View](references/verify-and-view.md) <br>
- [Troubleshooting](references/troubleshooting.md) <br>
- [Teardown](references/teardown.md) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, Configuration instructions] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- `claude-code` <br>
- `codex` <br>



## Evaluation Tasks: <br>
Evaluated against 6 evaluation tasks (all positive skill-activation cases) with 2 attempts per task using the NVSkills-Eval `external` profile. <br>

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
| Security | 8 | 92% (+0%) | 100% (+0%) |
| Correctness | 8 | 87% (-2%) | 84% (+16%) |
| Discoverability | 8 | 55% (-5%) | 72% (+13%) |
| Effectiveness | 8 | 81% (-5%) | 64% (+18%) |
| Efficiency | 8 | 37% (-8%) | 60% (+13%) |

## Skill Version(s): <br>
3.2.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
