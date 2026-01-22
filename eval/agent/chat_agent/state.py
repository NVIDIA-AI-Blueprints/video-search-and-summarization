"""
State schema for the VSS Evaluation Agent.
"""

from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class EvaluationAgentState(TypedDict):
    """State schema for the VSS Evaluation agent."""

    messages: Annotated[list[BaseMessage], add_messages]

    # Initial Choice
    workflow_mode: str  # "full_workflow" or "analysis_only"

    # Configuration Phase
    config_generated: bool
    config_path: str

    # Execution Phase
    execution_completed: bool
    logs_folder_path: str

    # Analysis Phase
    selected_reporting_action: str  # Menu-driven reporting choice
    run_reports_generated: bool
    report_summaries: str
    dashboard_generated: bool
    performance_analysis_done: bool
    best_model_identified: str
    regression_detected: bool
    qa_session_completed: bool
