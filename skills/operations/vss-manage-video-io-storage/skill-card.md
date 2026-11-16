## Description: <br>
Use to drive `vss vios` for sensor list, timelines, clips, snapshots, and add/delete of video or stream sources, and the VIOS REST API only for what that CLI does not cover (sensor info/status/settings, storage and recorder status, WebRTC, the RTSP proxy, network scan, device settings, bytes to disk, the NvStreamer API), when the caller names a REST endpoint, or to debug VIOS. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and operators managing video input/output and storage in a deployed VSS stack use this skill to list sensors, upload video files, add or delete RTSP streams, retrieve clip and snapshot URLs and recording timelines, serve synthetic RTSP via NvStreamer, and provision sources into Agent-backed or headless deployments for fan-out to perception consumers. <br>

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
- [Video Search and Summarization (GitHub)](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, API Calls, JSON] <br>
**Output Format:** [JSON (CLI stdout) and Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
3 evaluation tasks (2 positive, 1 negative), each executed in an isolated sandbox pod with 1 attempt per task. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Whether the skill is safe to use (scored from the `security` signal). <br>
- Correctness: Whether the final answer is correct (scored from the `accuracy` signal). <br>
- Discoverability: Whether the right skill was loaded when needed (scored from the `skill_execution` signal). <br>
- Effectiveness: Whether the skill helped complete the task (50% `goal_accuracy` + 50% `behavior_check`). <br>
- Efficiency: Whether it avoided wasted tool calls and token usage (50% `skill_efficiency` + 50% `token_efficiency`). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool-call productivity (legacy wire id; routing is scored under Discoverability). <br>
- `accuracy`: Final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Whether the user's goal was achieved. <br>
- `behavior_check`: Whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Actual uncached prompt plus completion usage. <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 73.2% — baseline ran, but no comparable score was available; uplift unavailable | 70.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 66.7% → 100.0% (+33.3 points) | 50.0% → 100.0% (+50.0 points) |
| Correctness | 6.7% → 33.3% (+26.6 points) | 33.3% → 40.0% (+6.7 points) |
| Discoverability | 97.5% — baseline ran, but no comparable score was available; uplift unavailable | 92.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 23.3% → 46.7% (+23.4 points) | 48.3% → 26.7% (-21.6 points) |
| Efficiency | 88.5% — baseline ran, but no comparable score was available; uplift unavailable | 93.4% — baseline ran, but no comparable score was available; uplift unavailable |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
