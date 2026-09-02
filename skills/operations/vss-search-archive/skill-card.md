## Description: <br>
Use this skill to run top-level VSS fusion search on archived video, or to ingest video files / RTSP streams for search. <br>

This skill is for demonstration purposes and not for production usage. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 OR MIT <br>
## Use Case: <br>
Developers and engineers who need to search archived video content using natural-language queries, ingest video files or RTSP streams for search indexing, and manage search-ingested video sources. <br>

### Deployment Geography for Use: <br>
Global <br>

## Requirements / Dependencies: <br>
**Requires API Key or External Credential:** [Environment-dependent; No for ordinary local search] <br>
**Credential Type(s):** [Deployment/operator credential when required] <br>

Do not include secrets in prompts/logs/output; use least-privilege credentials; rotate keys as appropriate. <br>

## Known Risks and Mitigations: <br>
Risk: Review before execution as proposals could introduce incorrect or misleading guidance into skills. <br>
Mitigation: Review and scan skill before deployment. <br>

## Reference(s): <br>
- [NVIDIA AI Blueprints: Video Search and Summarization](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [API Calls, Shell commands, Analysis] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- claude-code <br>
- codex <br>



## Evaluation Tasks: <br>
Evaluated against 1 evaluation task in the NVSkills-Eval external profile (astra-sandbox environment). <br>

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
| Security | 1 | 100% (+0%) | 100% (+0%) |
| Correctness | 1 | 100% (+75%) | 71% (+47%) |
| Discoverability | 1 | 88% (+62%) | 84% (+48%) |
| Effectiveness | 1 | 62% (+38%) | 50% (+26%) |
| Efficiency | 1 | 71% (+48%) | 83% (+47%) |

## Skill Version(s): <br>
Current skill: 3.4.0. Published benchmark below: 3.3.0; it must not be treated
as validation of the current operational workflow until rerun. <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
