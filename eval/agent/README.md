# VSS Evaluation Agent

This directory contains a Model Context Protocol (MCP) server that streamlines the VSS accuracy evaluation workflow through AI-assisted development environments.

## Overview

The VSS Evaluation MCP Server integrates with AI coding assistants like Cursor to provide intelligent automation for evaluating Video Search and Summarization applications. The server guides users through the complete evaluation lifecycle:

- **Configuration Generation**: Create and customize BYOV (Bring Your Own Videos) and alert evaluation config files with validated settings
- **Guided Execution**: Step-by-step walkthrough of running evaluation harnesses using sample datasets included in the repository
- **Performance Analysis**: Automated log analysis, report generation, and interactive dashboard creation
- **Accuracy Assessment**: Evaluate VSS application accuracy across different configuration profiles, including VLM/LLM model selection, prompts, and pipeline parameters

By embedding evaluation workflows directly into the IDE, the MCP server enables rapid iteration and data-driven optimization of VSS deployments.

### Setup with Cursor

1. **Install Dependencies**:
   ```bash
   pip3 install mcp fastmcp pandas pyyaml
   ```

2. **Configure Cursor**:
   Go to Cursor settings -> MCP & Integrations -> New MCP Server

   Add the following to your Cursor MCP configuration:
   ```json
    {
    "mcpServers": {
        "vss-evaluation-agent": {
        "command": "python3",
        "args": ["/path/to/video-search-and-summarization/eval/agent/mcp_server.py"]
        }
    }
    }
   ```

3. **Restart Cursor** to load the MCP server. Check in Settings -> MCP & Integrations that the `vss-evaluation-agent` is loaded with tools.

### MCP Server Capabilities

The VSS Evaluation MCP Server provides the following tools:

#### Environment & Setup Tools (NEW!)
- **`get_codebase_selection_guide()`** - Get comprehensive guide on choosing between local codebase vs container image code
- **`check_compose_configuration(compose_path)`** - Check current compose.yaml configuration to see which codebase option is active
- **`suggest_codebase_option(use_case, needs_patches, is_developing, needs_reproducibility)`** - Get personalized recommendation for which codebase option to use

#### Configuration Management
- **`generate_config(config_content)`** - Generate a VSS testing configuration file from YAML content
- **`validate_model_path(model_path)`** - Validate that a custom model path exists and is accessible

#### Pipeline Execution
- **`run_vss_pipeline(path_to_repo)`** - Get instructions for running BYOV evaluation (now includes codebase check!)
- **`error_handling`** - Get the common VSS error cases and solutions

#### Log Analysis & Reporting
- **`list_available_runs(logs_directory)`** - List all available runs (log folders) sorted by creation time
- **`list_run_files(logs_directory, run_name)`** - List all available files for a specific run
- **`generate_performance_report(logs_directory)`** - Generate comprehensive performance reports from all run logs
- **`analyze_single_run(run_folder, logs_directory)`** - Generate detailed performance report for a single run

#### Dashboard Generation
- **`generate_html_dashboard(logs_directory, dashboard_template)`** - Generate interactive HTML dashboard from run logs

### Supported Log File Types

The MCP server automatically processes various log file types from a directory containing health eval log folders:
- **Health Summary**: `via_health_summary_*.json`
- **GPU Usage**: `via_gpu_usage_*.csv`
- **NVDEC Usage**: `via_nvdec_usage_*.csv`
- **Accuracy Logs**: `accuracy_*.log
- **Result CSVs**: `*.result.csv`
- **QA Results**: `qa_results_*.csv`

### Sample Queries for Cursor

#### Environment Setup (NEW!)
- "Should I use local code or container code?" -> Cursor will use get_codebase_selection_guide() to provide comprehensive guidance
- "Check my compose.yaml configuration" -> Cursor will use check_compose_configuration() to verify current setup
- "I'm debugging the pipeline, which code should I use?" -> Cursor will use suggest_codebase_option() with your requirements
- "I need to apply VLM patches, what's the best setup?" -> Cursor will recommend Option 1 (local code) with reasoning
- "Which codebase option for benchmarking?" -> Cursor will recommend Option 2 (container code) for reproducibility

#### Generating configs
- Generate a VSS config for CR1 and GPT-4o using the bridge and warehouse videos -> Cursor should generate the config at `/path-to-video-search-and-summarization/eval/byov/`.
- Generate a VSS config for CR1 using the video <custom video path> -> Cursor should ask for ground truth files, then generate the config at above path.
- Generate a VSS config for <custom model path> using the bridge and warehouse videos. -> Cursor should validate the model path, then generate the config for the custom model.
- Generate a VSS config for Cosmos-Reason2 models -> Cursor will create config with both Dec 9 and earlier checkpoints

#### Running the accuracy evaluation harness
- Show me how to run the BYOV accuracy harness -> Cursor will check codebase configuration and provide complete instructions
- I'm ready to run the evaluation -> Cursor will verify compose.yaml setup first, then give run instructions

#### Generating a performance report
- Make a performance report from the logs -> Cursor will read the logs at `logs/accuracy` and generate `performance_report.txt`.

#### Generating html dashboard
- Generate an html dashboard to visualize the results -> Cursor will generate an interactive HTMl dashboard from the run logs.

#### Analyzing results
- From the logs, which video has the highest accuracy?
- Which VLM model performed the best on average?