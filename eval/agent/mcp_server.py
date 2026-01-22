import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml
from analysis_tools import (
    generate_all_run_reports,
    generate_single_run_report,
    process_all_runs,
)
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server for VSS Evaluation Agent
mcp = FastMCP("vss-evaluation-agent")

# File patterns for different types of log data
LOG_FILE_PATTERNS = {
    "health_summary": "via_health_summary_*.json",
    "gpu_usage": "via_gpu_usage_*.csv",
    "nvdec_usage": "via_nvdec_usage_*.csv",
    "accuracy_log": "accuracy_*.log",
    "result_csv": "*.result.csv",
    "qa_results": "qa_results_*.csv",
}

# ============================================================================
# HELPER FUNCTIONS - Resource Discovery
# ============================================================================


def _get_mime_type(file_extension: str) -> str:
    """Get MIME type based on file extension."""
    mime_types = {
        ".json": "application/json",
        ".csv": "text/csv",
        ".log": "text/plain",
        ".html": "text/html",
    }
    return mime_types.get(file_extension.lower(), "application/octet-stream")


def _read_log_file(logs_directory: str, run_name: str, file_type: str) -> str:
    """Read a specific log file from a run directory."""
    run_path = Path(logs_directory) / run_name

    if not run_path.exists():
        return f"Run directory {run_name} not found in {logs_directory}"

    # Find the file matching the pattern
    pattern = LOG_FILE_PATTERNS.get(file_type)
    if not pattern:
        return f"Unknown file type: {file_type}"

    files = list(run_path.glob(pattern))
    if not files:
        return f"No {file_type} files found in run {run_name}"

    # Read the first matching file
    try:
        with open(files[0], "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading {files[0]}: {e}"


def _list_run_files(logs_directory: str, run_name: str) -> Dict[str, List[str]]:
    """List all available files for a specific run."""
    run_path = Path(logs_directory) / run_name

    if not run_path.exists():
        return {"error": f"Run directory {run_name} not found in {logs_directory}"}

    files = {}
    for file_type, pattern in LOG_FILE_PATTERNS.items():
        matching_files = list(run_path.glob(pattern))
        if matching_files:
            files[file_type] = [f.name for f in matching_files]

    return files


def _generate_config_helper(config_file_content: str, config_path: str) -> str:
    """Helper function to generate configuration file for VSS testing."""
    print(f"✅ Configuration will be written to: {config_path}")

    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            f.write(config_file_content)
    except Exception as e:
        return (
            f"Error writing config file: {e}, please check that the path is valid: {config_path}"
        )

    return f"Config generated at {config_path}"


def _validate_model_path_helper(model_path: str) -> bool:
    """Helper function to validate that a custom model path exists and is accessible."""
    # TODO: Implement model path validation logic
    return os.path.exists(model_path)


# ============================================================================
# HELPER FUNCTIONS - Execution Tools
# ============================================================================


def _run_vss_pipeline_helper(path_to_repo: str) -> str:
    """Helper function to run the full VSS BYOV harness pipeline."""
    # TODO: Implement BYOV pipeline execution logic
    try:
        tests_dir = os.path.join(path_to_repo, "tests")

        if not os.path.exists(tests_dir):
            return f"❌ Tests directory not found at: {tests_dir}"

        print(f"🚀 Starting container shell in {tests_dir}")
        print("📋 Running 'make byov' - you will be dropped into the container...")
        print("💡 Use 'exit' to return to the evaluation agent when done.")

        result = subprocess.run(["make", "byov"], cwd=tests_dir)

        if result.returncode == 0:
            return "✅ Container shell session completed successfully"
        else:
            return (
                f"❌ Container shell exited with return code {result.returncode}. "
                f"Please check your environment variables in tests/.env"
            )

    except FileNotFoundError:
        return "❌ 'make' command not found. Please ensure make is installed and in PATH"
    except KeyboardInterrupt:
        return "⚠️ Container shell session interrupted by user"
    except Exception as e:
        return f"❌ Error running container shell: {str(e)}"


# ============================================================================
# HELPER FUNCTIONS - Analysis Tools
# ============================================================================


def _list_available_runs_helper(logs_directory: str) -> List[Dict[str, Any]]:
    """List all available runs with basic information."""
    runs = []
    logs_path = Path(logs_directory)

    if not logs_path.exists():
        return runs

    for item in logs_path.iterdir():
        if item.is_dir():
            health_files = list(item.glob(LOG_FILE_PATTERNS["health_summary"]))
            if health_files:
                # Get creation time
                creation_time = datetime.fromtimestamp(item.stat().st_ctime)

                run_info = {
                    "name": item.name,
                    "path": str(item),
                    "created": creation_time.isoformat(),
                    "has_health_summary": len(health_files) > 0,
                }

                # Check for other file types
                for pattern_name, pattern in LOG_FILE_PATTERNS.items():
                    if pattern_name != "health_summary":
                        files = list(item.glob(pattern))
                        run_info[f"has_{pattern_name}"] = len(files) > 0

                runs.append(run_info)

    # Sort by creation time (newest first)
    runs.sort(key=lambda x: x["created"], reverse=True)
    return runs


def _list_run_files_helper(logs_directory: str, run_name: str) -> Dict[str, List[str]]:
    """List all available files for a specific run."""
    run_path = Path(logs_directory) / run_name

    if not run_path.exists():
        return {"error": f"Run directory {run_name} not found in {logs_directory}"}

    files = {}
    for file_type, pattern in LOG_FILE_PATTERNS.items():
        matching_files = list(run_path.glob(pattern))
        if matching_files:
            files[file_type] = [f.name for f in matching_files]

    return files


# ============================================================================
# MCP TOOLS - Environment & Setup Tools
# ============================================================================


@mcp.tool()
async def get_codebase_selection_guide() -> str:
    """Get guidance on choosing between local codebase vs container image code.
    
    Call this when:
    - User is setting up BYOV evaluation for the first time
    - User asks about compose.yaml configuration
    - User asks which code to use (local vs container)
    - User is having issues with code mounting or patches
    - Before running the VSS pipeline to ensure proper setup
    
    This provides a comprehensive guide with decision matrix, recommendations,
    and instructions for both options.
    """
    try:
        prompts_path = Path(__file__).parent / "prompts.yaml"
        with open(prompts_path, "r", encoding="utf-8") as f:
            prompts = yaml.safe_load(f)
        return prompts.get("codebase_selection_prompt", "Codebase selection prompt not found")
    except Exception as e:
        return f"Error loading prompts.yaml: {e}"


@mcp.tool()
async def check_compose_configuration(compose_path: str) -> Dict[str, Any]:
    """Check the current compose.yaml configuration to see which codebase option is active.
    
    Args:
        compose_path: Full path to compose.yaml file (e.g., /path/to/repo/eval/compose.yaml)
    
    Returns:
        Dictionary with configuration status:
        - using_local_code: bool - Whether local code mount is active
        - mount_line: str - The actual line from compose.yaml
        - recommendation: str - Recommendation based on current config
    """
    try:
        if not os.path.exists(compose_path):
            return {
                "error": f"compose.yaml not found at {compose_path}",
                "recommendation": "Please provide the correct path to compose.yaml"
            }
        
        with open(compose_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Look for the specific mount line
        mount_pattern = r'^\s*(#?)\s*-\s*"\$\{PATH_TO_REPO\}/src/vss-engine/:/opt/nvidia/via/via-engine"'
        
        result = {
            "compose_path": compose_path,
            "using_local_code": False,
            "mount_line": None,
            "line_number": None,
            "status": "unknown"
        }
        
        for i, line in enumerate(content.split('\n'), 1):
            if 'PATH_TO_REPO}/src/vss-engine/' in line and '/opt/nvidia/via/via-engine' in line:
                result["mount_line"] = line.strip()
                result["line_number"] = i
                
                # Check if line is commented
                stripped = line.strip()
                if stripped.startswith('#'):
                    result["using_local_code"] = False
                    result["status"] = "USING CONTAINER IMAGE CODE"
                    result["recommendation"] = (
                        "✓ Using container image code (Option 2)\n"
                        "  - Good for: Reproducible evaluations, benchmarking\n"
                        "  - Note: VLM pipeline patches must be applied inside container\n"
                        "  - To switch to local code: Uncomment this line in compose.yaml"
                    )
                else:
                    result["using_local_code"] = True
                    result["status"] = "USING LOCAL REPOSITORY CODE"
                    result["recommendation"] = (
                        "✓ Using local repository code (Option 1)\n"
                        "  - Good for: Development, debugging, easy patching\n"
                        "  - Note: VLM pipeline patches can be applied in local repo\n"
                        "  - To switch to container code: Comment out this line with #"
                    )
                break
        
        if result["mount_line"] is None:
            result["status"] = "MOUNT LINE NOT FOUND"
            result["recommendation"] = (
                "⚠️ Could not find the vss-engine mount line in compose.yaml.\n"
                "   Expected line around: volumes: section with PATH_TO_REPO/src/vss-engine"
            )
        
        return result
        
    except Exception as e:
        return {
            "error": f"Error reading compose.yaml: {e}",
            "recommendation": "Please check the file path and permissions"
        }


@mcp.tool()
async def suggest_codebase_option(
    use_case: str,
    needs_patches: bool = False,
    is_developing: bool = False,
    needs_reproducibility: bool = False
) -> str:
    """Suggest which codebase option (local vs container) to use based on user's needs.
    
    Args:
        use_case: Brief description of what user wants to do (e.g., "benchmark VLM models", "debug pipeline")
        needs_patches: Whether user needs to apply patches (e.g., VLM pipeline patch)
        is_developing: Whether user is actively developing/modifying VSS code
        needs_reproducibility: Whether reproducible results are critical
    
    Returns:
        Recommendation with reasoning
    """
    recommendation = {
        "use_case": use_case,
        "suggested_option": None,
        "reasoning": [],
        "configuration": "",
        "next_steps": []
    }
    
    # Decision logic
    score_local = 0
    score_container = 0
    
    # Factor 1: Patches needed
    if needs_patches:
        score_local += 2
        recommendation["reasoning"].append(
            "✓ Patches needed: Local code makes patches easier to manage and persistent"
        )
    else:
        score_container += 1
    
    # Factor 2: Development
    if is_developing:
        score_local += 3
        recommendation["reasoning"].append(
            "✓ Active development: Local code allows immediate testing of changes"
        )
    else:
        score_container += 1
    
    # Factor 3: Reproducibility
    if needs_reproducibility:
        score_container += 3
        recommendation["reasoning"].append(
            "✓ Reproducibility needed: Container code ensures consistent environment"
        )
    else:
        score_local += 1
    
    # Use case specific scoring
    use_case_lower = use_case.lower()
    if any(word in use_case_lower for word in ["debug", "test", "patch", "modify", "develop"]):
        score_local += 2
        recommendation["reasoning"].append(
            "✓ Use case suggests development/debugging needs"
        )
    
    if any(word in use_case_lower for word in ["benchmark", "compare", "evaluate", "production"]):
        score_container += 2
        recommendation["reasoning"].append(
            "✓ Use case suggests evaluation/benchmarking needs"
        )
    
    # Make decision
    if score_local > score_container:
        recommendation["suggested_option"] = "Option 1: Use Local Repository Code"
        recommendation["configuration"] = (
            "In compose.yaml, UNCOMMENT the line:\n"
            '  - "${PATH_TO_REPO}/src/vss-engine/:/opt/nvidia/via/via-engine"'
        )
        recommendation["next_steps"] = [
            "1. Edit eval/compose.yaml",
            "2. Uncomment the vss-engine mount line (remove # if present)",
            "3. Apply any patches to src/vss-engine/src/vlm_pipeline/vlm_pipeline.py in your local repo",
            "4. Run: make down && make up && make shell",
            "5. Your local changes will be reflected in the container"
        ]
    else:
        recommendation["suggested_option"] = "Option 2: Use Container Image Code"
        recommendation["configuration"] = (
            "In compose.yaml, COMMENT OUT the line:\n"
            '  #- "${PATH_TO_REPO}/src/vss-engine/:/opt/nvidia/via/via-engine"'
        )
        recommendation["next_steps"] = [
            "1. Edit eval/compose.yaml",
            "2. Comment out the vss-engine mount line (add # at start)",
            "3. Run: make down && make up && make shell",
            "4. If patches needed, apply them inside container with vim",
            "5. Note: Patches will be lost when container is removed"
        ]
    
    # Format output
    output = f"""
═══════════════════════════════════════════════════════════════════════════════
CODEBASE SELECTION RECOMMENDATION
═══════════════════════════════════════════════════════════════════════════════

Use Case: {use_case}

RECOMMENDATION: {recommendation["suggested_option"]}

REASONING:
{chr(10).join(recommendation["reasoning"])}

CONFIGURATION:
{recommendation["configuration"]}

NEXT STEPS:
{chr(10).join(recommendation["next_steps"])}

═══════════════════════════════════════════════════════════════════════════════

Need more details? Use the get_codebase_selection_guide tool for comprehensive guidance.
"""
    
    return output


# ============================================================================
# MCP TOOLS - Configuration Tools
# ============================================================================


@mcp.tool()
async def get_config_template() -> str:
    """Do this when asked to generate a config for the VSS pipeline evaluation harness.

    Returns the sample yaml config for the VSS pipeline BYOV evaluation harness,
    including possible fields and their descriptions. After calling this and receiving
    the template, collect the user's requirements and call the generate_config tool
    to generate the config.
    """
    try:
        prompts_path = Path(__file__).parent / "prompts.yaml"
        with open(prompts_path, "r", encoding="utf-8") as f:
            prompts = yaml.safe_load(f)
        return prompts.get("config_generation_prompt", "Config generation prompt not found")
    except Exception as e:
        return f"Error loading prompts.yaml: {e}"


@mcp.tool()
async def generate_config(config_content: str, config_path: str) -> str:
    """Generate a VSS testing configuration file.

    Args:
        config_content: The correctly formatted YAML configuration file contents for
            VSS testing, including the necessary fields from the user's requirements.
        config_path: Full path where to write the config file (e.g., /path/to/repo/eval/byov/byov_config.yaml)
    """
    return _generate_config_helper(config_content, config_path)


@mcp.tool()
async def validate_model_path(model_path: str) -> bool:
    """Validate that a custom model path exists and is accessible.

    Args:
        model_path: Path to the custom model to validate
    """
    return _validate_model_path_helper(model_path)


# ============================================================================
# MCP TOOLS - Execution Tools
# ============================================================================


@mcp.tool()
async def run_vss_pipeline(path_to_repo: str) -> str:
    """Get instructions for running the full BYOV harness pipeline.
    
    IMPORTANT: Before running, ensure the user has made the codebase selection decision!
    Use check_compose_configuration to verify, and get_codebase_selection_guide if they need help.

    Args:
        path_to_repo: Path to the repository for via-engine or video-search-and-summarization
    """
    compose_path = os.path.join(path_to_repo, "eval", "compose.yaml")
    
    instructions = """
═══════════════════════════════════════════════════════════════════════════════
VSS BYOV EVALUATION - RUN INSTRUCTIONS
═══════════════════════════════════════════════════════════════════════════════

⚠️  IMPORTANT: Before running, you must decide which codebase to use!

STEP 0: CODEBASE SELECTION (CRITICAL!)
───────────────────────────────────────────────────────────────────────────────
"""
    
    # Check compose configuration if possible
    if os.path.exists(compose_path):
        try:
            from typing import cast
            config_status = cast(Dict[str, Any], await check_compose_configuration(compose_path))
            instructions += f"""
Current Configuration Status:
{config_status.get('status', 'Unknown')}

{config_status.get('recommendation', '')}

"""
        except:
            instructions += """
Could not automatically check compose.yaml configuration.
Please use: check_compose_configuration(compose_path) to verify your setup.

"""
    else:
        instructions += f"""
Could not find compose.yaml at: {compose_path}
Please verify your repository path and use check_compose_configuration tool.

"""
    
    instructions += """
💡 Use these tools to help decide:
   - get_codebase_selection_guide() - Comprehensive guide
   - suggest_codebase_option() - Get personalized recommendation
   - check_compose_configuration() - Verify current setup

═══════════════════════════════════════════════════════════════════════════════

STEP 1: NAVIGATE TO EVAL DIRECTORY
───────────────────────────────────────────────────────────────────────────────
cd /path/to/video-search-and-summarization/eval

STEP 2: ENSURE CONFIGURATION IS READY
───────────────────────────────────────────────────────────────────────────────
✓ byov/byov_config.yaml is configured with your VLM models and videos
✓ Environment variables are set (use setup_byov_env.sh)
✓ compose.yaml is configured with correct codebase option
✓ VLM pipeline patch applied (if using local VLMs like Cosmos-Reason)

STEP 3: START SERVICES AND ENTER CONTAINER
───────────────────────────────────────────────────────────────────────────────
make up      # Start supporting services
make shell   # Enter the container with interactive bash

STEP 4: [IF NEEDED] APPLY VLM PIPELINE PATCH (Inside Container)
───────────────────────────────────────────────────────────────────────────────
If using local VLMs (nvila, cosmos-reason1/2, vila-1.5):

vim +1529 src/vlm_pipeline/vlm_pipeline.py

Add this line after "def stop(self, force=False):":
    force = True

Save and exit: Esc, :wq, Enter

NOTE: If using Option 1 (local code), you can edit this in your local repo instead!

STEP 5: RUN THE EVALUATION (Inside Container)
───────────────────────────────────────────────────────────────────────────────
PYTHONPATH=/:.:./eval/byov:./eval:./src python3 -m pytest --noconftest eval/byov/test_byov.py -v -s 2>&1 | tee out.log

STEP 6: WAIT FOR COMPLETION
───────────────────────────────────────────────────────────────────────────────
Evaluation will take 3-5 hours depending on videos and models configured.
Results will be saved in: VSS_test_results.xlsx

STEP 7: EXIT AND CLEANUP (When Done)
───────────────────────────────────────────────────────────────────────────────
exit         # Exit container
make down    # Stop all services

═══════════════════════════════════════════════════════════════════════════════
TROUBLESHOOTING
═══════════════════════════════════════════════════════════════════════════════

Issue: Tests hang or fail to cleanup
→ Solution: Make sure VLM pipeline patch is applied

Issue: Code changes not reflected
→ Solution: Check compose.yaml - are you using local code or container code?
   Use check_compose_configuration to verify.

Issue: Out of memory
→ Solution: Reduce number of videos or use fewer GPUs

For more issues: Use error_handling() tool

═══════════════════════════════════════════════════════════════════════════════
"""
    
    return instructions


# ============================================================================
# MCP TOOLS - Analysis Tools
# ============================================================================


@mcp.tool()
async def list_run_files(logs_directory: str, run_name: str) -> Dict[str, List[str]]:
    """List all available files for a specific run.

    Args:
        logs_directory: Path to the directory containing run logs (mandatory)
        run_name: Name of the specific run to list files for. You can use the
            list_available_runs tool to get the list of available runs.
    """
    return _list_run_files_helper(logs_directory, run_name)


@mcp.tool()
async def list_available_runs(logs_directory: str) -> List[Dict[str, Any]]:
    """List all available runs (log folders) sorted by log creation time with basic information.

    Args:
        logs_directory: Path to the directory containing run logs (mandatory)
    """
    return _list_available_runs_helper(logs_directory)


@mcp.tool()
async def generate_performance_report(logs_directory: str) -> str:
    """Generate a comprehensive performance report from all available run logs.

    You do not need to list the available runs first, just call this with the logs
    directory. Then give the user the performance report contents, and inform them
    it has been written to their logs directory.

    Args:
        logs_directory: Path to the directory containing run logs
    """
    report = generate_all_run_reports(logs_directory)

    with open(f"{logs_directory}/performance_report.txt", "w", encoding="utf-8") as f:
        f.write(report)

    return report


@mcp.tool()
async def analyze_single_run(run_folder: str, logs_directory: str = "logs") -> str:
    """Generate a detailed performance report for a single run.

    Args:
        run_folder: Name of the specific run folder to analyze
        logs_directory: Path to the logs directory (default: "logs")
    """
    report = generate_single_run_report(run_folder, logs_directory)
    return report


@mcp.tool()
async def get_html_dashboard_instructions() -> str:
    """Get the HTML dashboard generation instructions.

    Call this FIRST when asked to generate an HTML dashboard from logs, BEFORE
    calling the generate_html_dashboard tool. No need to list the available runs
    first, just call this tool.
    """
    try:
        prompts_path = Path(__file__).parent / "prompts.yaml"
        with open(prompts_path, "r", encoding="utf-8") as f:
            prompts = yaml.safe_load(f)
        return prompts.get("dashboard_generation_prompt", "Dashboard generation prompt not found")
    except Exception as e:
        return f"Error loading prompts.yaml: {e}"


@mcp.tool()
async def generate_html_dashboard(logs_directory: str, dashboard_template: str) -> str:
    """Generate an HTML dashboard from all available run logs.

    Call get_html_dashboard_instructions BEFORE calling this tool with the full
    HTML template as a parameter.

    Args:
        logs_directory: Path to the directory containing run logs
        dashboard_template: Full HTML template to use for the dashboard
    """

    dashboard_path = f"{logs_directory}/dashboard.html"
    dashboard_data = process_all_runs(logs_directory)
    dashboard_html = dashboard_template.replace(
        "{*DATA_INSERTION_HERE*}", json.dumps(dashboard_data, indent=2)
    )

    try:
        with open(dashboard_path, "w", encoding="utf-8") as f:
            f.write(dashboard_html)
    except Exception as e:
        return f"Error writing dashboard to {dashboard_path}: {e}"

    return f"Dashboard created successfully: {dashboard_path}"


@mcp.tool()
async def error_handling() -> str:
    """Get the error handling cases.

    Call this when asked to troubleshoot an error during a run of the VSS pipeline,
    in order to get context on the error and/or potential solutions.
    """
    try:
        prompts_path = Path(__file__).parent / "prompts.yaml"
        with open(prompts_path, "r", encoding="utf-8") as f:
            prompts = yaml.safe_load(f)
        return prompts.get("error_handling_prompt", "Error handling prompt not found")
    except Exception as e:
        return f"Error loading prompts.yaml: {e}"


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport="stdio")
