"""
Workflow nodes for the VSS Evaluation Agent.
"""

# Load prompts
import os
from typing import Any, Dict

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..analysis_tools import format_metrics_for_llm, process_all_runs
from .state import EvaluationAgentState
from .tools import create_html_dashboard, generate_config, run_full_byov_pipeline

prompts_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts.yaml")
with open(prompts_path, "r") as file:
    PROMPTS = yaml.safe_load(file)


# ============================================================================
# CHOICE NODES
# ============================================================================


def initial_choice_node(state: EvaluationAgentState) -> Dict[str, Any]:
    """Node to let user choose between full workflow or analysis-only mode."""
    print("🎯 VSS Evaluation Agent")
    print("=" * 50)
    print("What would you like to do?")
    print("1. 🚀 Full workflow (configure + run + analyze)")
    print("2. 📊 Analyze existing logs only")

    choice = input("\nEnter your choice (1-2): ").strip()

    if choice == "2":
        workflow_mode = "analysis_only"
        print("📁 Skipping to log analysis...")
    else:
        workflow_mode = "full_workflow"
        print("🔄 Starting full workflow...")

    return {"workflow_mode": workflow_mode}


# ============================================================================
# CONFIGURATION NODE
# ============================================================================


def configuration_agent_node(state: EvaluationAgentState, llm) -> Dict[str, Any]:
    """Simple agent that gathers requirements and generates config using JSON responses."""
    print("⚙️ Starting configuration process...")

    # Get current messages
    messages = state.get("messages", [])

    # Initialize conversation if empty
    if not messages:
        messages = [
            SystemMessage(content=PROMPTS["config_generation_prompt"]),
            HumanMessage(content="Hello! I'd like to configure VSS for testing."),
        ]

    # Continue until we have a config
    current_messages = messages[:]
    config_generated = False

    while not config_generated:
        try:
            # Get LLM response with current conversation
            response = llm.invoke(current_messages)

            # Parse response format: STATUS|content
            if "|" in response.content:
                parts = response.content.split("|", 1)  # Split on first | only
                status = parts[0].strip()
                content = parts[1].strip() if len(parts) > 1 else ""

                # Validate status
                if status not in ["READY", "ASK"]:
                    print(f"❌ Unknown status: {status}")
                    break
            else:
                print(f"❌ Invalid response format (missing |): {response.content}")
                break

            if status == "ASK":
                # Ask follow-up question
                print(f"❓ {content}")
                user_response = input("Your response: ").strip()

                # Add to conversation history
                current_messages.append(AIMessage(content=response.content))
                current_messages.append(HumanMessage(content=user_response))

            elif status == "READY":
                # Config is ready
                generate_config(content)
                config_generated = True
                current_messages.append(
                    AIMessage(content="Configuration generated successfully at byov_config.yaml")
                )

            else:
                print(f"❌ Unknown status: {status}")
                break

        except Exception as e:
            print(f"❌ Configuration error: {e}")
            break

    return {
        "messages": current_messages,
        "config_generated": config_generated,
        "config_path": "tests/byov/byov_config.yaml" if config_generated else "",
    }


# ============================================================================
# EXECUTION NODES
# ============================================================================


def execute_pipeline_node(state: EvaluationAgentState) -> Dict[str, Any]:
    """Node to execute the full BYOV pipeline."""
    print("🏃 Executing full BYOV pipeline...")

    try:
        # Run the full BYOV pipeline with generated config
        run_full_byov_pipeline.invoke({})
        print("✅ Full BYOV pipeline completed")
        return {"execution_completed": True}
    except Exception as e:
        print(f"❌ Pipeline execution failed: {e}")
        return {"execution_completed": False}


def get_logs_path_node(state: EvaluationAgentState) -> Dict[str, Any]:
    """Node to get logs folder location from user."""
    print("📁 Getting logs path...")
    logs_path = input("Enter the path to your logs folder: ")
    return {"logs_folder_path": logs_path}


# ============================================================================
# ANALYSIS NODES
# ============================================================================


def reporting_menu_node(state: EvaluationAgentState) -> Dict[str, Any]:
    """Node to present reporting options menu to user."""
    print("\n🔍 Analysis & Reporting Menu")
    print("=" * 40)
    print("What would you like to analyze?")
    print("1. 📊 Generate run summaries")
    print("2. 📈 Create HTML dashboard")
    print("3. 💬 Interactive Q&A with VSS Reporting Agent")
    print("4. ✅ Exit analysis")

    choice = input("\nEnter your choice (1-4): ").strip()

    choice_map = {
        "1": "generate_reports",
        "2": "create_dashboard",
        "3": "qa_with_summaries",
        "4": "exit_analysis",
    }

    selected_action = choice_map.get(choice, "reporting_menu")
    return {"selected_reporting_action": selected_action}


def generate_reports_node(state: EvaluationAgentState) -> Dict[str, Any]:
    """Node to generate run reports."""
    logs_directory = state.get("logs_folder_path", "logs")
    print(f"📊 Generating reports from: {logs_directory}")

    # Extract metrics from all runs
    all_runs = process_all_runs(logs_directory)

    if not all_runs:
        print("No valid run data found!")
        return {}

    # Format for LLM
    formatted_report = format_metrics_for_llm(all_runs)

    print(formatted_report)

    print("✅ Summary reports generated successfully!")

    return {"run_reports_generated": True, "report_summaries": formatted_report}


def create_dashboard_node(state: EvaluationAgentState) -> Dict[str, Any]:
    """Node to create HTML dashboard."""
    logs_directory = state.get("logs_folder_path", "logs")
    print(f"📈 Creating dashboard from: {logs_directory}")

    try:
        # Use the create_html_dashboard tool
        result = create_html_dashboard.invoke(
            {"logs_path": logs_directory, "template_path": "agent/dashboard_template.html"}
        )
        print(result)
        return {"dashboard_generated": True}
    except Exception as e:
        print(f"❌ Error creating dashboard: {e}")
        return {"dashboard_generated": False}


def qa_with_summaries_node(state: EvaluationAgentState, llm) -> Dict[str, Any]:
    """Node to generate summaries first, then start Q&A session."""
    print("🔄 Preparing Q&A session...")

    # First, generate run summaries if not already done
    report_summaries = state.get("report_summaries", "")

    if not report_summaries:
        print("📊 Generating run summaries first...")
        logs_directory = state.get("logs_folder_path", "logs")

        # Extract metrics from all runs
        all_runs = process_all_runs(logs_directory)

        if not all_runs:
            print("❌ No valid run data found! Cannot start Q&A session.")
            return {"qa_session_completed": False}

        # Format for LLM
        report_summaries = format_metrics_for_llm(all_runs)
        print("✅ Run summaries generated successfully!")
    else:
        print("✅ Using existing run summaries...")

    print("🤖 Starting Q&A session with VSS Reporting Agent...")

    # Create system message to establish the agent role
    system_prompt = f"""You are a VSS (Video Search & Summarization) Pipeline Reporting Agent.
    You are an expert at analyzing performance data, identifying bottlenecks, and
    providing insights about video processing pipelines.

    You have access to detailed performance data from VSS pipeline runs. Your role is to:
    - Answer questions about performance metrics, latencies, and efficiency
    - Identify bottlenecks and optimization opportunities
    - Compare different models and configurations
    - Explain GPU utilization patterns
    - Analyze accuracy metrics and token usage
    - Provide actionable recommendations

    Be concise but thorough in your responses. Use the actual data to support your answers.

    Here is the performance data from the VSS pipeline runs:

    {report_summaries}

    You are now ready to answer questions about this data."""

    # Initialize conversation
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="Hello! I have questions about the VSS pipeline performance data."),
    ]

    print("=" * 60)

    # Interactive chat loop
    while True:
        try:
            # Get user question
            user_input = input("\n❓ Your question: ").strip()

            # Check for exit commands
            if user_input.lower() in ["exit", "quit", "bye", "end"]:
                print(
                    "🤖 VSS Reporting Agent: Thank you for using the VSS reporting "
                    "system. Goodbye!"
                )
                break

            if not user_input:
                continue

            # Add user message to conversation
            messages.append(HumanMessage(content=user_input))

            # Get LLM response
            print("🤖 VSS Reporting Agent: ", end="", flush=True)
            response = llm.invoke(messages)
            print(response.content)

            # Add assistant response to conversation history
            messages.append(AIMessage(content=response.content))

        except KeyboardInterrupt:
            print("\n🤖 VSS Reporting Agent: Session interrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error in Q&A session: {e}")
            break

    return {
        "qa_session_completed": True,
        "run_reports_generated": True,
        "report_summaries": report_summaries,
    }
