## Description: <br>
Manage VIOS video input/output and storage with the vss vios CLI — sensors, streams, uploads, snapshots, clip URLs, and timelines — plus the VIOS REST API for operations the CLI does not cover and NvStreamer for synthetic RTSP feeds. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and operators use this skill to manage video input/output and storage on a running VSS deployment — listing sensors, adding or deleting video files and RTSP streams, retrieving clips and snapshots, and provisioning sources into headless builds. <br>

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
- [VIOS REST API Reference](references/api-reference.md) <br>
- [Deploy VIOS Service](references/deploy-vios-service.md) <br>
- [Integrate VIOS Service](references/integrate-vios-service.md) <br>
- [NvStreamer API Reference](references/nvstreamer-api-reference.md) <br>
- [Provision VIOS Source](references/provision-vios-source.md) <br>
- [VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, API Calls, Configuration instructions] <br>
**Output Format:** [JSON and Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
3 evaluation tasks (2 positive, 1 negative) in isolated sandbox pods, evaluator version 1.5.6. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Checks whether the final answer is correct against the reference answer. <br>
- Discoverability: Checks whether the right skill was selected, decoys were avoided, and the workflow executed. <br>
- Effectiveness: Checks whether the skill helped complete the user's goal and followed the expected workflow (goal_accuracy 50% + behavior_check 50%). <br>
- Efficiency: Checks for wasted tool calls and token usage (skill_efficiency 50% + token_efficiency 50%). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Detects unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Verifies the expected skill was selected, decoys avoided, and workflow executed. <br>
- `accuracy`: Measures final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Measures whether the user's goal was achieved. <br>
- `behavior_check`: Verifies the expected workflow behavior was followed. <br>
- `skill_efficiency`: Measures tool-call productivity. <br>
- `token_efficiency`: Measures actual uncached prompt plus completion token usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 73.0% | 69.6% |
| Security | 100.0% → 100.0% (±0.0 pts) | 50.0% → 100.0% (+50.0 pts) |
| Correctness | 13.3% → 33.3% (+20.0 pts) | 66.7% → 40.0% (-26.7 pts) |
| Discoverability | 97.5% | 82.5% |
| Effectiveness | 18.3% → 42.5% (+24.2 pts) | 55.0% → 30.0% (-25.0 pts) |
| Efficiency | 91.5% | 95.5% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
