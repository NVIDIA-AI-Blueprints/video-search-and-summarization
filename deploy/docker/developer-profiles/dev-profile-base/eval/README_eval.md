# Developer Workflow - Dev Profile Base Evaluation Setup (E2E)

This directory contains evaluation resources for the Developer Workflow - Dev Profile Base report agent.

### 0. Deploy Blueprint

Deploy the developer workflow (dev-base) blueprint.


### 1. Download the `eval` Dataset

The eval dataset (ground truth files, reference reports, and videos) is hosted on the NVIDIA Dataset Service (DSS) under the dataset name `vss-devx-base`.

**a. Install the `nvdataset` CLI:**

```bash
pip install --extra-index-url https://urm.nvidia.com/artifactory/api/pypi/sw-ngc-data-platform-pypi/simple nvdataset
```

**b. Download the dataset:**

```bash
export NGC_API_KEY=<your-ngc-api-key>
export NVDATASET_TENANTID=0573334707593577
export NVDATASET_GROUPID=vss-bp-team
nvdataset download vss-devx-base <bp_dir>/deploy/docker/data-dir/agent_eval/dataset/vss-devx-base
```

> Note: `data-dir` is only available after the blueprint/workflow has been deployed.

**c. Verify that the files have been placed correctly:**

```bash
$ sudo apt install tree  # optional

# `cd` into the `<bp_dir>/deploy/docker` directory

$ tree data-dir/agent_eval
data-dir/agent_eval/
├── dataset
│   └── vss-devx-base
│       ├── dataset_single_turn.json
│       ├── dataset_multi_turn.json
│       ├── gt
│       │   ├── dev_base_001_report.json
│       │   ├── dev_base_002_report.json
│       │   ├── dev_base_003_report.json
│       │   └── dev_base_004_report.json
│       └── videos
│           ├── dev_base_001.mp4
│           ├── dev_base_002.mp4
│           ├── dev_base_003.mp4
│           ├── dev_base_004.mp4
│           ├── vss-sample-drone-bridge.mp4
│           ├── vss-sample-sim-traffic.mp4
│           └── vss-sample-warehouse-4min.mp4
└── results
```


### 2. Upload Videos

The eval videos are included in the `vss-devx-base` dataset downloaded above. Upload them to VST using the VSS Agent UI at http://<your-ip-address>:3000/ or via the blueprint configurator.


### 3. Run Eval

**a. Run evaluation:**

On the deployment machine, run the evaluation:

```bash
# `cd` into the `<bp_dir>/deploy/docker` directory

docker exec vss-agent nat eval \
    --config_file deploy/docker/developer-profiles/dev-profile-base/vss-agent/configs/config.yml \
    --override functions.video_report_gen.hitl_enabled false \
    --override workflow.postprocessing.enabled false
```

**b. Results**

- Accuracy Results: The detailed accuracy report is generated in JSON format and can be found at:

    * Workflow Output - `data-dir/agent_eval/results/workflow_output.json`
    * Report Eval - `data-dir/agent_eval/results/report_evaluator_output.json`
    * Trajectory Eval - `data-dir/agent_eval/results/trajectory_evaluator_output.json`
    * QA Eval - `data-dir/agent_eval/results/qa_evaluator_output.json`

- Run the following script to create a summary report in CSV format:

```bash
# `cd` into the `<bp_dir>/deploy/docker` directory

developer-profiles/dev-profile-base/eval/eval_output_json_to_csv.sh \
    data-dir/agent_eval/results/workflow_output.json \
    data-dir/agent_eval/results/summary.csv \
    data-dir/agent_eval/results/report_evaluator_output.json \
    data-dir/agent_eval/results/trajectory_evaluator_output.json \
    data-dir/agent_eval/results/qa_evaluator_output.json
```

Summarized results location: `data-dir/agent_eval/results/summary.csv`

- Latency Results: The detailed latency report is generated in JSON format at `data-dir/agent_eval/results/inference_optimization.json`.

The workflow latency is captured under `workflow_run_time_confidence_intervals`, a sample of which can be seen here:
```json
{
  "confidence_intervals": {
    "workflow_run_time_confidence_intervals": {
      "n": 2,
      "mean": 6.268884539604187,
      "ninetieth_interval": [
        5.858083780523035,
        6.679685298685339
      ],
      "ninety_fifth_interval": [
        5.779419805379836,
        6.7583492738285385
      ],
      "ninety_ninth_interval": [
        5.625588031766468,
        6.912181047441906
      ],
      "p90": 6.551418280601501,
      "p95": 6.586734998226166,
      "p99": 6.6149883723258975
    },
    ..
  }
}
```
