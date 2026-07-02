## Description: <br>
Benchmark a deployed LVS (Long Video Summarization) instance to measure latency and throughput limits, identify GPU and pipeline bottlenecks, and receive configuration recommendations. <br>

This skill is ready for commercial/non-commercial use. <br>

## Owner
NVIDIA <br>

### License/Terms of Use: <br>
Apache-2.0 <br>

## Use Case: <br>
Performance engineers and developers benchmarking LVS deployments on NVIDIA GPU hardware to characterize single-file summarization latency and concurrent file burst throughput before production deployment. <br>

### Deployment Geography for Use: <br>
Global <br>

## Known Risks and Mitigations: <br>
Risk: Benchmark workloads generate sustained GPU load — thermal throttling or hardware damage may occur on systems without adequate cooling. <br>
Mitigation: Monitor GPU temperatures during benchmarking; ensure adequate system cooling before running high-concurrency file_burst tests. <br>

Risk: Test videos are warehouse surveillance footage from the VSS sample dataset and may contain identifiable individuals or proprietary facility layouts. <br>
Mitigation: Use only in secure, air-gapped environments consistent with the data classification of the source footage. <br>

## Reference(s): <br>
- [NVIDIA VSS Documentation](https://docs.nvidia.com/vss/latest/index.html) <br>
- [Benchmark Modes Reference](references/benchmark-modes.md) <br>
- [Analyzing Results Reference](references/analyzing-results.md) <br>
- [GitHub Repository](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization) <br>

## Skill Output: <br>
**Output Type(s):** [Analysis, Shell commands, Reports] <br>
**Output Format:** [XLSX benchmark reports, JSON result files, and natural-language analysis with improvement suggestions] <br>
**Output Parameters:** [1D] <br>
**Other Properties Related to Output:** [XLSX files contain Summary, GPU_Info, and All Iterations sheets; JSON files follow the standard VSS benchmark result schema] <br>

## Evaluation Tasks: <br>
NVSkills-Eval 3-Tier Evaluation with external profile; Tier 1 static validation, Tier 2 deduplication. Tier 3 live agent evaluation requires a running LVS deployment with test videos. <br>

## Evaluation Metrics Used: <br>
Reported benchmark dimensions: <br>
- Correctness: Checks whether the benchmark steps execute without error and produce valid XLSX and JSON output files. <br>
- Effectiveness: Checks whether the reported latency and GPU-utilization metrics are internally consistent and reflect the configured workload (chunk size, resolution, concurrency). <br>
- Efficiency: Checks whether the benchmark completes in reasonable time relative to the video duration and number of iterations. <br>

## Skill Version(s): <br>
3.2.0 (source: frontmatter) <br>

## Ethical Considerations: <br>
NVIDIA believes Trustworthy AI is a shared responsibility and we have established policies and practices to enable development for a wide array of AI applications. When downloaded or used in accordance with our terms of service, developers should work with their internal team to ensure this skill meets requirements for the relevant industry and use case and addresses unforeseen product misuse. <br>

(For Release on NVIDIA Platforms Only) <br>
Please report quality, risk, security vulnerabilities or NVIDIA AI Concerns [here](https://app.intigriti.com/programs/nvidia/nvidiavdp/detail). <br>
