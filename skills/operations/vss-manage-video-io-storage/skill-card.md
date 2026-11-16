## Description: <br>
Use to drive `vss vios` for sensor list, timelines, clips, snapshots, and add/delete of video or stream sources, and the VIOS REST API only for what that CLI does not cover (sensor info/status/settings, storage and recorder status, WebRTC, the RTSP proxy, network scan, device settings, bytes to disk, the NvStreamer API), when the caller names a REST endpoint, or to debug VIOS. Also provisions a source into an Agent-backed or headless deployment, which the deployment fans out to the perception consumers (RT-CV/RT-Embed/RT-VLM). Not for VLM inference, semantic search. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>
## Use Case: <br>
Developers and operators of NVIDIA Video Search and Summarization (VSS) deployments use this skill to manage VIOS video input/output and storage through the `vss vios` CLI: listing sensors, adding or deleting video files and RTSP streams, reading timelines, getting clip and snapshot URLs, provisioning sources to perception consumers, and using direct VIOS or NvStreamer REST calls only where the CLI does not cover the operation or for debugging. <br>

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
- [VIOS REST API reference](references/api-reference.md) <br>
- [Provision a VIOS source](references/provision-vios-source.md) <br>
- [NvStreamer REST API reference](references/nvstreamer-api-reference.md) <br>
- [Integrate the VIOS service](references/integrate-vios-service.md) <br>
- [Deploy the VIOS service](references/deploy-vios-service.md) <br>
- [VSS repository agent guide (AGENTS.md)](../../../AGENTS.md) <br>
- [Leftover VST containers issue #151](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/issues/151) <br>
- [VSS documentation](https://docs.nvidia.com/vss/latest/index.html) <br>


## Skill Output: <br>
**Output Type(s):** [Shell commands, API Calls, Analysis] <br>
**Output Format:** [Markdown with inline bash code blocks and CLI JSON results] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Evaluation Agents Used: <br>
- Claude Code (`aws/anthropic/bedrock-claude-opus-5-5`) <br>
- Codex (`openai/openai/gpt-6.1-sol`) <br>



## Evaluation Tasks: <br>
5 evaluation tasks (4 positive, 1 negative); 1 attempt per task, each in its own isolated sandbox pod (`k8s-sandbox` environment). Dataset cases: video-io-storage-delete-stream, video-io-storage-not-configured, video-io-storage-nvstreamer-upload, video-io-storage-vios-routing, video-question-non-activation. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Security: Is it safe to use? Scored from `security` (100%). <br>
- Correctness: Is the answer correct? Scored from `accuracy` (100%). <br>
- Discoverability: Was the right skill loaded when needed? Scored from `skill_execution` (100%). <br>
- Effectiveness: Did the skill help complete the task? Scored from `goal_accuracy` (50%) + `behavior_check` (50%). <br>
- Efficiency: Did it avoid wasted tool calls and token usage? Scored from `skill_efficiency` (50%) + `token_efficiency` (50%). <br>

Underlying evaluation signals used in this run: <br>
- `security`: Security: unsafe operations, secret leakage, and unauthorized access. <br>
- `skill_execution`: Skill Execution: whether the expected skill was selected, decoys were avoided, and the workflow executed. <br>
- `skill_efficiency`: Tool Productivity: tool-call productivity (legacy wire id; routing is scored under Discoverability). <br>
- `accuracy`: Accuracy: final-answer correctness against the reference answer. <br>
- `goal_accuracy`: Goal Accuracy: whether the user's goal was achieved. <br>
- `behavior_check`: Behavior Check: whether the expected workflow behavior was followed. <br>
- `token_efficiency`: Token Efficiency: actual uncached prompt plus completion usage (50% of Efficiency). <br>



## Evaluation Results: <br>
> ⚠️ **Overall verdict: INCOMPLETE — Required evidence is missing**

One or more required evaluation tiers did not complete, so this benchmark is not publication-complete.

### Results at a Glance

| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 95.7% — baseline ran, but no comparable score was available; uplift unavailable | 62.6% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 100.0% → 100.0% (±0.0 points) |
| Correctness | 48.0% → 100.0% (+52.0 points) | 100.0% → 84.0% (-16.0 points) |
| Discoverability | 100.0% — baseline ran, but no comparable score was available; uplift unavailable | 30.0% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 30.0% → 90.0% (+60.0 points) | 75.0% → 55.0% (-20.0 points) |
| Efficiency | 88.6% — baseline ran, but no comparable score was available; uplift unavailable | 44.2% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

#### Comparison details

Shared-source comparisons use the same signals and weights on both conditions. They do not replace the full dimension scores above or change the publication verdict.

| Agent | Dimension | Basis | Baseline → With skill (uplift) | Explanation |
| --- | --- | --- | --- | --- |
| claude-code | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| claude-code | Efficiency | Token-only comparison | 98.8% → 93.9% (-4.9 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |
| codex | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| codex | Efficiency | Token-only comparison | 89.1% → 88.3% (-0.8 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |

### Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 52 finding(s) |
| Tier 2 | Semantic deduplication | **INCOMPLETE** | Tier 2 scan incomplete: a content cluster has more than 64 members. |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 5 task(s) |

### Incomplete Coverage

- **Context Deduplication:** Tier 2 scan incomplete: a content cluster has more than 64 members.

## Skill Version(s): <br>
3.3.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
