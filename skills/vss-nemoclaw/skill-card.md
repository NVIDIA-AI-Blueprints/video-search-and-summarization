## Description: <br>
Use to create and operate a NemoClaw express sandbox for VSS using Docker, OpenShell, SSH, config-file, and NemoClaw CLI commands. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner: NVIDIA <br>

### License/Terms of Use: <br>
Apache 2.0 <br>
## Use Case: <br>
Developers and engineers creating and operating a fast NemoClaw/OpenClaw sandbox from a local or cached `nemoclaw-sandbox:local` image, configuring model providers, setting up the VSS OpenClaw plugin, recovering the OpenClaw runtime, and exposing the dashboard. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: The workflow executes host Docker/OpenShell commands and uses provider API credentials. <br>
Mitigation: Review commands before execution, keep `.env` files ignored, and never commit live API keys. <br>

## Reference(s): <br>
- [VSS GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>
- [NemoClaw Express Sandbox Skill](SKILL.md) <br>

## Skill Output: <br>
**Output Type(s):** [Shell commands, Configuration instructions] <br>
**Output Format:** [Markdown with inline bash code blocks] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [None] <br>

## Skill Version(s): <br>
3.2.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
