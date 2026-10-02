## Description: <br>
Use to call the VIOS REST API (sensor list, timelines, clip extraction, snapshots, add/delete sensors and streams) and to provision a source into a headless (no-agent) build, which the deployment fans out to the perception consumers (RT-CV/RT-Embed/RT-VLM). <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and operators managing video input/output and storage on an NVIDIA VSS deployment use this skill to call the VIOS REST API for sensor management, stream operations, clip extraction, snapshots, file uploads, and headless source provisioning. <br>

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
- [Headless Source Provisioning](references/provision-vios-source.md) <br>
- [NvStreamer REST API Reference](references/nvstreamer-api-reference.md) <br>
- [VIOS Integration Contract](references/integrate-vios-service.md) <br>
- [VIOS Deployment Contract](references/deploy-vios-service.md) <br>
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [NVIDIA AI Blueprint: Video Search and Summarization](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>


## Skill Output: <br>
**Output Type(s):** [API Calls, Shell commands, Configuration instructions] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`) <br>
- Codex (`openai/openai/gpt-5.5`) <br>



## Evaluation Tasks: <br>
3 evaluation tasks (2 positive, 1 negative) executed in isolated sandbox pods, evaluated by Skill Evaluator v1.5.6. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Checks for unsafe operations, secret leakage, and unauthorized access. <br>
- Correctness: Checks final-answer correctness against the reference answer. <br>
- Discoverability: Checks whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- Effectiveness: Checks whether the user's goal was achieved and the expected workflow behavior was followed. <br>
- Efficiency: Checks tool-call productivity and token efficiency. <br>

Underlying evaluation signals used in this run: <br>
- `security`: Verifies absence of unsafe operations, secret leakage, and unauthorized access. <br>
- `accuracy`: Verifies final-answer correctness against the reference answer. <br>
- `skill_execution`: Verifies the expected skill was selected and the workflow executed. <br>
- `goal_accuracy`: Verifies the user's goal was achieved. <br>
- `behavior_check`: Verifies the expected workflow behavior was followed. <br>
- `skill_efficiency`: Verifies tool-call productivity. <br>
- `token_efficiency`: Verifies token usage efficiency (uncached prompt plus completion). <br>



## Evaluation Results: <br>
| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 83.0% | 68.9% |
| Security | 100.0% → 100.0% (±0.0 points) | 50.0% → 50.0% (±0.0 points) |
| Correctness | 13.3% → 73.3% (+60.0 points) | 66.7% → 73.3% (+6.6 points) |
| Discoverability | 100.0% | 85.0% |
| Effectiveness | 25.0% → 61.7% (+36.7 points) | 38.3% → 50.0% (+11.7 points) |
| Efficiency | 80.1% | 86.0% |

## Skill Version(s): <br>
3.3.0-rc0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
