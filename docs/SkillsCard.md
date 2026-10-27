## Description

The Video Search and Summarization (VSS) Blueprint Skills package provides 16 reusable agent skills that guide users through VSS deployment, configuration, video ingestion, calibration, storage, analytics, search, summarization, reporting, and alert management using supported VSS services and APIs.

Fifteen skills in this package are intended for commercial or non-commercial use after all applicable release approvals are complete. vss-search-archive is for demonstration purposes and is not intended for production use.

## Dataset Owner:

NVIDIA Corporation

## Dataset Creation/Last Modified Date:

Jul 15, 2026 — evaluation artifacts last modified in registered source revision [2800540bd82adc2594af9a6a4bec3e654cab1cb8](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/commit/2800540bd82adc2594af9a6a4bec3e654cab1cb8).

## Versioning:

Unique Version Name: VSS Blueprint Skills 3.3

Previous Version(s): [VSS Blueprint Skills 3.2 (v3.2.0)](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/tree/v3.2.0/skills)

Relationship to Previous Version(s): Extension — Skills 3.3 builds on the v3.2.0 package, retaining the 16-skill catalog while updating skill instructions, evaluation artifacts, compatibility guidance, and detached signatures for the VSS 3.3 release.

### License/Terms of Use

[Apache License 2.0](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/LICENSE.md)

## Intended Usage:

The package is intended for developers, solution architects, operators, and coding-agent users who deploy or operate NVIDIA VSS workflows. The skills provide task-specific instructions for configuring VSS profiles, managing video inputs and storage, invoking supported APIs, querying analytics, and producing searches, summaries, reports, and alerts.

### Compatible Agents/Recommended Deployment Environments

- Claude Code

- Codex

- Cursor

- Other agents that support the [Agent Skills specification](https://agentskills.io/specification)

The skills were evaluated with Claude Code and Codex in the NVIDIA Astra sandbox environment. Compatibility with other agents has not yet been formally evaluated.

### Requirements/Dependencies

- [NVIDIA VSS 3.3 documentation](https://docs.nvidia.com/vss/latest/index.html) and the VSS services required by the selected workflow

- A supported NVIDIA GPU topology, driver, and NVIDIA Container Toolkit for deployment-oriented skills

- Docker and Docker Compose for container deployment workflows

- curl and jq for API-driven workflows

- Network access and valid NVIDIA NGC/API Catalog credentials when downloading protected artifacts

- The registered source revision: [2800540bd82adc2594af9a6a4bec3e654cab1cb8](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/commit/2800540bd82adc2594af9a6a4bec3e654cab1cb8)

Repository registration is tracked under [NSPECT-NAC8-FZUO](https://nspect.nvidia.com/review?id=NSPECT-NAC8-FZUO), release Skills 3.3, branch dev/3.3.0.

### Release Management

- GitHub — planned Aug 29, 2026 via [dev/3.3.0](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/tree/dev/3.3.0/skills)

- NVIDIA NGC — planned Aug 29, 2026 as part of the registered Skills 3.3 external release

- Build.NVIDIA.com — [VSS product page](https://build.nvidia.com/nvidia/video-search-and-summarization); not currently listed as a Skills 3.3 release location in [nSpect](https://nspect.nvidia.com/review?id=NSPECT-NAC8-FZUO)

[nSpect](https://nspect.nvidia.com/review?id=NSPECT-NAC8-FZUO) lists GitHub and NVIDIA NGC as the release locations and uses the date above as the Skills 3.3 release date. Add the final published links to the nSpect AI Transparency Card when publication is complete.


### Deployment Geography for Use

Global: Asia-Pacific (APAC); Europe, Middle East, and Africa (EMEA); Latin America (LATAM); and North America (NAM), subject to applicable export, product, privacy, and service-availability restrictions.

## Known Technical Limitations

- Most skills require a compatible VSS 3.3 deployment and cannot complete live operations when the required services, endpoints, models, or credentials are unavailable.

- vss-search-archive is demonstration-only and is not intended for production use.

- Deployment skills assume supported Linux, GPU, driver, container-runtime, storage, and network configurations; unsupported topologies can require manual adaptation.

- API-oriented skills depend on the schemas and behavior of the VSS version they target and can require updates when endpoints or response formats change.

- Agent routing is not perfect. The published evaluation set includes both missed activations and false or incomplete task routing, so users should explicitly invoke the intended skill when automatic discovery is unreliable.

- The skills provide operational instructions; they do not replace service-side authentication, authorization, rate limiting, input validation, monitoring, or release-policy enforcement.

## Known Risks and Mitigations

- **Operational-state changes:** Deployment, configuration, upload, deletion, and alert-management workflows can change local or remote state. The skills use preflight checks, scoped commands, dry runs where available, and explicit confirmation before destructive or externally visible actions.

- **Credential exposure:** VSS and NGC workflows can require credentials. The skills direct agents to environment variables or approved credential stores and prohibit embedding secrets in commands, files, or responses.

- **Prompt or content manipulation:** User input, video metadata, API responses, and retrieved content may be untrusted. Agents must treat them as data, preserve instruction boundaries, and require human review for consequential actions.

- **Dependency and service drift:** Container tags, APIs, packages, and deployment requirements can change. The skills pin or derive versions where possible and require readiness checks before use.

- **Incorrect analysis or guidance:** Generated searches, summaries, reports, calibration outputs, or operational recommendations may be incomplete or inaccurate. Users must review results before relying on them for safety, compliance, or production decisions.

- **Artifact integrity:** Each of the 16 skill directories contains an NVIDIA OpenSSF Model Signing/OMS signature. The repository verification workflow reported 16 passing signatures and 0 failures for the registered revision.

## Dataset Characterization

### Data Collection Method

Synthetic — The evaluation corpus was created specifically for this skill release from manually authored prompts, expected-skill mappings, ground-truth descriptions, and operational test scenarios. It does not contain web-crawled, sensor-derived, or production user data.

### Labeling Method

Manually-Labeled — NVIDIA skill authors defined and reviewed the expected skill activation, ground truth, expected behavior or script, trigger expectations, and runtime checks.

### Dataset Format

UTF-8 JSON. Each skill contains a canonical evals/evals.json manifest; supporting JSON scenario files describe environment resources, queries, checks, and expected outcomes for NVSkills-Eval execution in the NVIDIA Astra sandbox.

### Dataset Quantification

- 16 canonical manifests covering 37 task definitions.

- 48 JSON files total: 16 canonical manifests and 32 supporting scenario files.

- Canonical task records use these fields as applicable: id, question, expected_skill, ground_truth, expected_behavior, expected_script, and should_trigger.

- Total evaluation JSON storage at the registered revision is 222,479 bytes (approximately 217 KiB); canonical manifests account for 37,988 bytes (approximately 37 KiB).


## References

- [VSS source repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization)

- [VSS documentation](https://docs.nvidia.com/vss/latest/index.html)

- [VSS prerequisites and validated GPU configurations](https://docs.nvidia.com/vss/latest/prerequisites.html)

- [VSS Skills catalog](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/tree/dev/3.3.0/skills)

- [NVIDIA Skill Card template](https://gitlab-master.nvidia.com/api-catalog/examples/-/blob/main/nv-skillscard/SkillsCard.md)

- [nSpect registration NSPECT-NAC8-FZUO](https://nspect.nvidia.com/review?id=NSPECT-NAC8-FZUO)

The package contains these individually documented skills:

- [vss-ask-video](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-ask-video/skill-card.md)

- [vss-deploy-dense-captioning](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-deploy-dense-captioning/skill-card.md)

- [vss-deploy-detection-tracking-2d](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-deploy-detection-tracking-2d/skill-card.md)

- [vss-deploy-detection-tracking-3d](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-deploy-detection-tracking-3d/skill-card.md)

- [vss-deploy-profile](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-deploy-profile/skill-card.md)

- [vss-deploy-video-embedding](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-deploy-video-embedding/skill-card.md)

- [vss-generate-video-calibration](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-generate-video-calibration/skill-card.md)

- [vss-generate-video-report](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-generate-video-report/skill-card.md)

- [vss-generate-video-report-rag](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-generate-video-report-rag/skill-card.md)

- [vss-manage-alerts](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-manage-alerts/skill-card.md)

- [vss-manage-video-io-storage](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-manage-video-io-storage/skill-card.md)

- [vss-query-analytics](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-query-analytics/skill-card.md)

- [vss-search-archive](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-search-archive/skill-card.md)

- [vss-setup-behavior-analytics](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-setup-behavior-analytics/skill-card.md)

- [vss-setup-video-analytics-api](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-setup-video-analytics-api/skill-card.md)

- [vss-summarize-video](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/dev/3.3.0/skills/vss-summarize-video/skill-card.md)

## Skill Output

**Output Type(s):** Analysis, Application Programming Interface (API) calls, code, configuration, and files

**Output Format:** Markdown guidance, JSON/API responses, shell commands, configuration files, and generated reports or artifacts, depending on the invoked skill

**Output Parameters: Task-specific inputs and outputs defined by the selected workflow; outputs may include scalar values, structured JSON, files, or operational state changes**

**Other Properties Related to Output:** Operational outputs can depend on live VSS service state and must be reviewed before consequential use.

## Fail Operation

- Human-in-the-loop confirmation before destructive, privileged, or externally visible actions

- Policy enforcement through repository instructions, workflow validation, PLC gates, and service-side controls

- Scoped shutdown or rollback instructions for deployment and long-running service workflows

- Clear stop conditions when prerequisites, permissions, credentials, or required services are unavailable

## Evaluation Agent(s)

- Claude Code (claude-code)

- Codex (codex)

## Evaluation Task(s)

The 16 skills were evaluated with 37 task cases in the NVIDIA Astra sandbox using the NVSkills-Eval external profile. The cases cover positive and negative skill activation, routing behavior, and representative operational workflows. Individual task counts and evidence are recorded in each linked Skill Card.

## Evaluation Metric(s)

- Security

- Correctness

- Discoverability

- Effectiveness

- Efficiency

- Supporting signals: skill execution, routing quality, accuracy, goal accuracy, behavior checks, and token efficiency

## Evaluation Result(s)

Across the individual Skill Cards, the recorded score ranges are:

| **Metric**      | **Claude Code** | **Codex** |
|-----------------|-----------------|-----------|
| Security        | 25–100%         | 62–100%   |
| Correctness     | 50–100%         | 27–92%    |
| Discoverability | 0–100%          | 0–89%     |
| Effectiveness   | 36–100%         | 30–85%    |
| Efficiency      | 26–94%          | 28–83%    |

These ranges summarize small, skill-specific evaluation sets and should not be interpreted as a statistically representative production benchmark. Per-skill results and baseline deltas are available in the linked cards.

## Testing Completed

- Completed — Product-security-oriented evaluation cases, including secret leakage, destructive actions, and unauthorized-access checks

  Completed — Detached signature verification for all 16 skill directories: 16 pass, 0 fail

  Not completed — Agent red-teaming beyond the recorded NVSkills-Eval cases

  Not completed — Independent network security assessment of the deployed VSS services

## Skill Version

Release bundle: Skills 3.3

The package intentionally preserves the semantic versions declared by each signed skill: 10 skills at 3.2.0 and 6 skills at 3.2.1. Each signing identifier below is the SHA-256 subject digest recorded in the corresponding skill.oms.sig file.

| **Skill** | **Version** | **Signing identifier (SHA-256)** |
|----|----|----|
| vss-ask-video | 3.2.0 | da85415f3bc7eb857de96472252e114aee3c0fb88a749566d60322c590371c67 |
| vss-deploy-dense-captioning | 3.2.1 | 9cd43e0deaaf6e2c7e7bd9cd08029e6436122300b4afa29747c3c007aac39d2a |
| vss-deploy-detection-tracking-2d | 3.2.1 | 52d762e96b6148c4d12453518eeecf24287aeb0d168310e834da735aace994b1 |
| vss-deploy-detection-tracking-3d | 3.2.1 | 8502b056192d3195ebdbe8f169ac7edddd607fabd074e995f53da5d2645e662a |
| vss-deploy-profile | 3.2.0 | b864d4a5aaa3d60393f8e980cf25b4b88801f890369608f7546e891f4e4e6c91 |
| vss-deploy-video-embedding | 3.2.1 | a287518d8a9ff645a14cfbf370d3156fb13502ea755b5250b1396278b58e7efa |
| vss-generate-video-calibration | 3.2.1 | dbef91568bea780128bfa330ccd46b80b3fa36615c01dfdc9be90e7754e32dec |
| vss-generate-video-report | 3.2.0 | 472cc94cd00ced80f72de040fa1ad1d9d314beb580fa03a99f3527f5bd7fd452 |
| vss-generate-video-report-rag | 3.2.0 | 84299bd8d5fa03c0feac2030a447d67a3e027ef3e7c3428095adbe6f41551f05 |
| vss-manage-alerts | 3.2.0 | 9cb3be8df712f85bdbabaca880e0c4ef36aac7f6b2120d21e70d2537240a29bc |
| vss-manage-video-io-storage | 3.2.0 | 415e984592125eddc596162eab37fe2eb251f85eb3029175b23f047bb77d8f26 |
| vss-query-analytics | 3.2.0 | 3c2c81b27bb16001f7e044a98315d08a31115abc27362f50cc995479f0e2c182 |
| vss-search-archive | 3.2.0 | f9abd8dfd15a599287be0b098adb9990345552b5c3cd0b1e7598963fa4640fd1 |
| vss-setup-behavior-analytics | 3.2.0 | d918ff0eda4d07b331fe8de172417cedbea05a9d8e284adc69f22a7ceef2aa7c |
| vss-setup-video-analytics-api | 3.2.0 | 4ccafd9b1deb3758558df894c7c3aa7220bfbffc19667fde084287b30208cff0 |
| vss-summarize-video | 3.2.1 | 933a0acf6d6471287c70a96b63e38235b12002be1610042a4d2e9a7543e86c99 |

Changing a signed skill or its individual Skill Card requires regeneration and verification of that skill's signature.

## Ethical Considerations

NVIDIA believes Trustworthy AI is a shared responsibility and has established policies and practices to enable development for a wide array of AI applications. Developers should work with their internal teams to ensure this Skills package and its evaluation data meet the requirements of the relevant industry and use case and address foreseeable misuse.

Please report quality issues, risks, security vulnerabilities, or NVIDIA AI concerns [here](https://www.nvidia.com/en-us/support/submit-security-vulnerability/).
