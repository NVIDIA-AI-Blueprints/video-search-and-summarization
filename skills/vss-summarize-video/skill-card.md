## Description: <br>
Use when summarizing a recorded video through HITL-gated LVS, with an explicitly approved VLM fallback. Not for reports, archive search, or live RTSP captioning. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
Developers and engineers who need to produce narrative summaries with timestamped events from recorded video using NVIDIA Video Search and Summarization services. <br>

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
- [End-to-End Example](references/end-to-end-example.md) <br>
- [Video Summarization API](references/video-summarization-api.md) <br>
- [HITL Prompts](references/hitl-prompts.md) <br>
- [Video Summarization Debugging](references/video-summarization-debugging.md) <br>
- [Video Summarization Deployment](references/video-summarization-deployment.md) <br>
- [Video Summarization Environment Variables](references/video-summarization-environment-variables.md) <br>
- [Deploy LVS Service](references/deploy-lvs-service.md) <br>
- [Integrate LVS Service](references/integrate-lvs-service.md) <br>
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Analysis, API Calls, Shell commands] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- claude-code <br>
- codex <br>



## Evaluation Tasks: <br>
3-Tier NVSkills-Eval evaluation in the astra-sandbox environment using the external profile. Overall verdict: PASS. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Checks whether skill-assisted execution avoids unsafe behavior such as secret leakage, destructive commands, or unauthorized access. <br>
- Correctness: Checks whether the agent follows the expected workflow and produces the correct final output. <br>
- Discoverability: Checks whether the agent loads the skill when relevant and avoids using it when irrelevant. <br>
- Effectiveness: Checks whether the agent performs measurably better with the skill than without it. <br>
- Efficiency: Checks whether the agent uses fewer tokens and avoids redundant work. <br>



## Evaluation Results: <br>
| Dimension | Num | `claude-code` | `codex` |
|---|---:|---:|---:|
| Security | N/A | N/A | N/A |
| Correctness | N/A | N/A | N/A |
| Discoverability | N/A | N/A | N/A |
| Effectiveness | N/A | N/A | N/A |
| Efficiency | N/A | N/A | N/A |

## Skill Version(s): <br>
3.2.2 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
