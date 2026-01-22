"""
VSS Evaluation Agent - Main orchestrator for the evaluation workflow.
"""

import os
import sys
from typing import Optional

# LangChain imports
from langchain_nvidia_ai_endpoints import ChatNVIDIA

# LangGraph imports
from langgraph.graph import END, START, StateGraph

from .nodes import (
    configuration_agent_node,
    create_dashboard_node,
    execute_pipeline_node,
    generate_reports_node,
    get_logs_path_node,
    initial_choice_node,
    qa_with_summaries_node,
    reporting_menu_node,
)

# Import our modular components
from .state import EvaluationAgentState
from .tools import (
    create_html_dashboard,
    run_full_byov_pipeline,
    validate_custom_model_path,
)


class VSS_EvaluationAgent:
    """LangGraph-based evaluation agent for VSS pipeline analysis."""

    def __init__(self, nvidia_api_key: Optional[str] = None):
        """Initialize the evaluation agent."""
        self.nvidia_api_key = nvidia_api_key or os.getenv("NVIDIA_API_KEY")
        if not self.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY environment variable must be set")

        # Initialize NVIDIA NIM LLM
        self.llm = ChatNVIDIA(
            model="meta/llama-3.1-70b-instruct",
            nvidia_api_key=self.nvidia_api_key,
            temperature=0.2,
            max_tokens=4096,
        )

        # Available tools
        self.tools = [validate_custom_model_path, run_full_byov_pipeline, create_html_dashboard]

    # ========================================================================
    # WORKFLOW CONTROL
    # ========================================================================

    def route_from_initial_choice(self, state: EvaluationAgentState) -> str:
        """Route based on user's initial workflow choice."""
        mode = state.get("workflow_mode", "full_workflow")

        if mode == "analysis_only":
            return "get_logs_path"
        else:
            return "configuration_agent"

    def should_continue_to_execution(self, state: EvaluationAgentState) -> str:
        """Determine if we should proceed to execution phase."""
        if state.get("config_generated", False):
            return "execute_pipeline"
        return END

    def should_continue_to_reporting(self, state: EvaluationAgentState) -> str:
        """Determine if we should proceed to reporting phase."""
        if state.get("execution_completed", False):
            return "get_logs_path"
        return END

    def route_from_reporting_menu(self, state: EvaluationAgentState) -> str:
        """Route from reporting menu based on user selection."""
        action = state.get("selected_reporting_action", "reporting_menu")

        if action == "exit_analysis":
            return END
        elif action in ["generate_reports", "create_dashboard", "qa_with_summaries"]:
            return action
        else:
            # Invalid choice, return to menu
            return "reporting_menu"

    def return_to_menu(self, state: EvaluationAgentState) -> str:
        """After completing an analysis task, return to the menu."""
        return "reporting_menu"

    def create_workflow(self) -> StateGraph:
        """Create the LangGraph workflow."""
        workflow = StateGraph(EvaluationAgentState)

        # =====================
        # NODES - Using modular functions
        # =====================
        workflow.add_node("initial_choice", initial_choice_node)
        workflow.add_node(
            "configuration_agent", lambda state: configuration_agent_node(state, self.llm)
        )
        workflow.add_node("execute_pipeline", execute_pipeline_node)
        workflow.add_node("get_logs_path", get_logs_path_node)
        workflow.add_node("reporting_menu", reporting_menu_node)
        workflow.add_node("generate_reports", generate_reports_node)
        workflow.add_node("create_dashboard", create_dashboard_node)
        workflow.add_node(
            "analyze_summaries", lambda state: qa_with_summaries_node(state, self.llm)
        )

        # =====================
        # INITIAL CHOICE EDGES
        # =====================
        workflow.add_edge(START, "initial_choice")
        workflow.add_conditional_edges(
            "initial_choice",
            self.route_from_initial_choice,
            {"configuration_agent": "configuration_agent", "get_logs_path": "get_logs_path"},
        )

        # =====================
        # CONFIGURATION AGENT EDGES
        # =====================
        workflow.add_conditional_edges(
            "configuration_agent",
            self.should_continue_to_execution,
            {"execute_pipeline": "execute_pipeline", END: END},
        )

        # =====================
        # RUNNING PHASE EDGES
        # =====================
        workflow.add_conditional_edges(
            "execute_pipeline",
            self.should_continue_to_reporting,
            {"get_logs_path": "get_logs_path", END: END},
        )

        # =====================
        # REPORTING PHASE EDGES
        # =====================
        workflow.add_edge("get_logs_path", "reporting_menu")

        # From reporting menu, route to selected analysis or exit
        workflow.add_conditional_edges(
            "reporting_menu",
            self.route_from_reporting_menu,
            {
                "generate_reports": "generate_reports",
                "create_dashboard": "create_dashboard",
                "qa_with_summaries": "analyze_summaries",
                "reporting_menu": "reporting_menu",  # Invalid choice loops back
                END: END,
            },
        )

        # All analysis tasks return to the menu
        workflow.add_conditional_edges(
            "generate_reports", self.return_to_menu, {"reporting_menu": "reporting_menu"}
        )
        workflow.add_conditional_edges(
            "create_dashboard", self.return_to_menu, {"reporting_menu": "reporting_menu"}
        )
        workflow.add_conditional_edges(
            "analyze_summaries", self.return_to_menu, {"reporting_menu": "reporting_menu"}
        )

        return workflow.compile()

    def visualize_graph(self):
        """Visualize the LangGraph workflow."""
        app = self.create_workflow()
        png_data = app.get_graph().draw_mermaid_png()

        # Save PNG to file
        with open("agent/evaluation_agent_workflow.png", "wb") as f:
            f.write(png_data)
        print("✅ Evaluation agent workflow graph saved to evaluation_agent_workflow.png")

    def run(self):
        """Main execution flow using LangGraph."""
        # Create and run the workflow
        workflow = self.create_workflow()

        # Initialize state
        initial_state = EvaluationAgentState(
            messages=[],
            workflow_mode="",
            config_generated=False,
            config_path="",
            execution_completed=False,
            logs_folder_path="",
            selected_reporting_action="",
            run_reports_generated=False,
            report_summaries="",
            dashboard_generated=False,
            performance_analysis_done=False,
            best_model_identified="",
            regression_detected=False,
            qa_session_completed=False,
        )

        # Run the workflow
        final_state = workflow.invoke(initial_state)

        print("\n🎉 Evaluation workflow completed!")
        return final_state


def main():
    """Main entry point."""
    try:
        agent = VSS_EvaluationAgent()
        agent.run()
        # # Option to visualize or run
        # print("🛠️ VSS Evaluation Agent Setup")
        # print("1. 📊 Generate workflow diagram")
        # print("2. 🚀 Run the agent")
        # choice = input("What would you like to do? (1-2): ").strip()

        # if choice == "1":
        #     agent.visualize_graph()  # Generate the workflow diagram
        # else:
        #     agent.run()  # Run the full workflow

    except ValueError as e:
        print(f"❌ {e}")
        print("Please set your NVIDIA_API_KEY environment variable.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
