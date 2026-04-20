# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import asyncio
from collections.abc import AsyncGenerator
from collections.abc import Hashable
import copy
from datetime import UTC
from datetime import datetime
import json
import logging
import re
import time
from typing import Any
from typing import cast
from uuid import uuid4

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from langchain_core.messages import ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.prompts import MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from nat.builder.builder import Builder
from nat.builder.context import Context
from nat.builder.context import ContextState
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.api_server import ChatRequest
from nat.data_models.api_server import ChatRequestOrMessage
from nat.data_models.api_server import Message
from nat.data_models.component_ref import FunctionRef
from nat.data_models.component_ref import LLMRef
from nat.data_models.function import FunctionBaseConfig
from nat.data_models.intermediate_step import IntermediateStepPayload
from nat.data_models.intermediate_step import IntermediateStepType
from nat.data_models.intermediate_step import StreamEventData
from nat.data_models.intermediate_step import TokenUsageBaseModel
from nat.data_models.intermediate_step import TraceMetadata
from nat.data_models.intermediate_step import UsageInfo
from nat.utils.type_converter import GlobalTypeConverter
from pydantic import BaseModel
from pydantic import Field
from typing_extensions import override

from vss_agents.agents.data_models import AgentDecision
from vss_agents.agents.data_models import AgentMessageChunk
from vss_agents.agents.data_models import AgentMessageChunkType
from vss_agents.agents.data_models import AgentOutput
from vss_agents.agents.postprocessing import POSTPROCESSING_FEEDBACK_MARKER
from vss_agents.agents.postprocessing import PostprocessingConfig
from vss_agents.agents.postprocessing import PostprocessingNode
from vss_agents.utils.asyncmixin import AsyncMixin
from vss_agents.utils.reasoning_parsing import parse_reasoning_content
from vss_agents.utils.reasoning_utils import get_llm_reasoning_bind_kwargs
from vss_agents.utils.reasoning_utils import get_thinking_tag

logger = logging.getLogger(__name__)

PLAN_CLARIFY_PREFIX = "[USER]"
TOOL_NOT_FOUND_ERROR_MESSAGE = "There is no tool named {tool_name}. Tool must be one of {tools}."
NO_INPUT_ERROR_MESSAGE = "No human input received to the agent, Please ask a valid question."
EMPTY_MESSAGES_ERROR = 'No input received in state: "current_message"'
EMPTY_SCRATCHPAD_ERROR = 'No tool input received in state: "agent_scratchpad"'
_TOOL_RESULTS_DELIMITER = "\n\n---\n### Latest Tool Results\n"


class AgentRequestOptions(BaseModel):
    """Per-request options passed from the UI/client through the agent pipeline."""

    llm_reasoning: bool = Field(default=False, description="Enable LLM reasoning mode")
    vlm_reasoning: bool | None = Field(default=None, description="Enable VLM reasoning mode (None = use tool default)")
    search_source_type: str = Field(
        default="video_file", description="Video source type for search: 'video_file' or 'rtsp'"
    )
    use_critic: bool = Field(default=True, description="Whether to verify search results with VLM critic agent")


class TopAgentRequest(ChatRequestOrMessage):
    """Extended ChatRequestOrMessage with reasoning parameters."""

    llm_reasoning: bool | None = Field(default=None, description="Enable LLM reasoning mode")
    vlm_reasoning: bool | None = Field(default=None, description="Enable VLM reasoning mode")
    search_source_type: str = Field(
        default="video_file", description="Video source type for search: 'video_file' or 'rtsp'"
    )
    use_critic: bool | None = Field(default=None, description="Whether to verify search results with VLM critic agent")


def _extract_text_content(message: "Message") -> dict:
    """
    Extract text content from a NAT Message for LangChain compatibility.

    NAT Message.content can be:
    - str: use directly
    - list[UserContent]: extract text from TextContent items (ignore ImageContent, AudioContent)

    Args:
        message: NAT Message object

    Returns:
        Dict with 'role' and 'content' (text string) suitable for message processing
    """
    content = message.content
    if isinstance(content, str):
        text_content = content
    elif isinstance(content, list):
        # Extract text from TextContent items only (skip ImageContent, AudioContent)
        text_parts = []
        for item in content:
            # TextContent has type="text" and a text attribute
            if getattr(item, "type", None) == "text" and hasattr(item, "text"):
                text_parts.append(item.text)
        text_content = "\n".join(text_parts)
    else:
        text_content = str(content)

    return {"role": message.role.value if hasattr(message.role, "value") else message.role, "content": text_content}


# Helper function to extract text from message content (handles both string and list formats)
def _get_content_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    # content is list[str | dict[str, Any]]
    # Extract text from list of dicts (e.g., [{'type': 'text', 'text': '...'}])
    texts: list[str] = []
    for item in content:
        if isinstance(item, dict) and "text" in item:
            texts.append(str(item["text"]))
        elif isinstance(item, str):
            texts.append(item)
    return " ".join(texts)


def strip_frontend_tags(content: str) -> str:
    """
    Strip frontend display tags from message content.

    Args:
        content: The message content that may contain frontend tags

    Returns:
        The content with frontend tags replaced by descriptive text
    """
    if not content or not isinstance(content, str):
        return content or ""

    # Replace <incidents>...</incidents> with placeholder
    cleaned = re.sub(r"<incidents>.*?</incidents>", "[Incident data]", content, flags=re.DOTALL)

    return cleaned


class TopAgentState(BaseModel):
    """State for the Top Agent conversation tracking"""

    current_message: BaseMessage | None = Field(default=None, description="Current user query")
    agent_scratchpad: list[BaseMessage] = Field(default_factory=list, description="Agent thoughts / intermediate steps")
    conversation_history: list[BaseMessage] = Field(
        default_factory=list,
        description="Recent conversation messages as HumanMessage/AIMessage (agent-think stripped)",
    )
    iteration_count: int = Field(default=0, description="Current iteration count")
    final_answer: str = Field(default="", description="Final answer from the agent")
    plan: str = Field(default="", description="Execution plan drafted by the plan node")
    previous_conversation: str = Field(default="", description="Previous conversation summary")
    options: AgentRequestOptions = Field(default_factory=AgentRequestOptions, description="Per-request options")
    subagent_side_effects: list[str] = Field(
        default_factory=list,
        description="Accumulated sub-agent side effects (download links, media URLs)",
    )


class TopAgentConfig(FunctionBaseConfig, name="top_agent"):
    """Config for the Top Agent."""

    workflow_alias: str | None = Field(
        default=None,
        description="Alias used by the NAT MCP server to register this workflow.",
    )
    tool_names: list[FunctionRef] = Field(
        default_factory=list,
        description="The list of regular tools to provide to the top agent (e.g., get_fov_counts_with_chart).",
    )
    subagent_names: list[str] = Field(
        default_factory=list,
        description="Names of sub-agents that support native streaming (e.g., ['report_agent', 'multi_report_agent']). "
        "These will be called with their native streaming interface to show internal reasoning steps.",
    )
    llm_name: LLMRef = Field(description="The LLM model to use with the top agent.")
    log_level: str = Field(default="INFO", description="Logging level for the agent (DEBUG, INFO, WARNING, ERROR).")
    max_iterations: int = Field(default=10, description="Maximum number of iterations for the agent.")
    max_history: int = Field(
        default=10,
        ge=0,
        description="Maximum number of messages to keep in the conversation history. Set to 0 to disable.",
    )
    prompt: str = Field(..., description="The prompt to use for the top agent.")
    llm_reasoning: bool = Field(default=False, description="Enable LLM reasoning mode.")
    planning_enabled: bool = Field(default=False, description="Enable plan-then-execute mode.")
    plan_prompt: str | None = Field(
        default=None,
        description="Prompt for the plan node. If None, a default planning instruction is used.",
    )
    tool_call_prompt: str | None = Field(
        default=None,
        description="Tool call rules prompt. If None and planning is enabled, extracted from the main prompt via LLM.",
    )
    response_format_prompt: str | None = Field(
        default=None,
        description="Response format rules prompt. If None and planning is enabled, extracted from the main prompt via LLM.",
    )

    # Postprocessing configuration
    postprocessing: PostprocessingConfig | None = Field(
        default=None,
        description="Postprocessing configuration.",
    )


class TopAgent(AsyncMixin):
    """Top-level routing agent with native tool calling"""

    llm: BaseChatModel
    llm_with_tools: Runnable[Any, BaseMessage]
    subagent_functions: dict[str, Any]
    subagent_names: set[str]
    callbacks: list[BaseCallbackHandler]
    max_iterations: int
    prompt: ChatPromptTemplate
    plan_exec_prompt: ChatPromptTemplate | None
    tools_dict: dict[str, BaseTool]
    graph: CompiledStateGraph
    checkpointer: InMemorySaver
    planning_enabled: bool
    plan_prompt: str | None
    plan_system_prompt: str
    tool_call_prompt: str
    response_format_prompt: str

    @override
    async def __ainit__(
        self,
        llm: BaseChatModel,
        prompt: ChatPromptTemplate,
        tools: list[BaseTool] | None = None,
        subagents: list[BaseTool] | None = None,
        subagent_functions: dict[str, Any] | None = None,
        callbacks: list[BaseCallbackHandler] | None = None,
        max_iterations: int = 10,
        max_history: int = 3,
        postprocessing_config: PostprocessingConfig | None = None,
        postprocessing_llm: BaseChatModel | None = None,
        planning_enabled: bool = False,
        plan_prompt: str | None = None,
        plan_exec_prompt: ChatPromptTemplate | None = None,
        plan_system_prompt: str = "",
        tool_call_prompt: str = "",
        response_format_prompt: str = "",
    ) -> None:
        logger.info("Initializing Top Agent")
        await super().__ainit__()

        self.llm = llm
        self.max_history = max_history
        tools_list = tools or []
        subagents_list = subagents or []

        # Merge tools and subagents for LLM binding
        subagents_plus_tools = tools_list + subagents_list
        self.llm_with_tools = llm.bind_tools(subagents_plus_tools) if subagents_plus_tools else llm

        # Track which tools are subagents and store their native functions
        self.subagent_functions = subagent_functions or {}
        self.subagent_names = set(self.subagent_functions.keys())

        self.callbacks = callbacks or []
        self.max_iterations = max_iterations

        # Initialize postprocessing if config is present
        self.postprocessing = (
            PostprocessingNode(postprocessing_config, llm=postprocessing_llm) if postprocessing_config else None
        )

        logger.info(
            "Setting up top agent with %d regular tools, %d sub-agents",
            len(tools_list),
            len(subagents_list),
        )
        if self.subagent_names:
            logger.info("Sub-agents with native streaming: %s", list(self.subagent_names))

        # Store prompt for dynamic agent creation with model parameters
        self.prompt = prompt
        self.plan_exec_prompt = plan_exec_prompt
        self.planning_enabled = planning_enabled
        self.plan_prompt = plan_prompt
        self.plan_system_prompt = plan_system_prompt
        self.tool_call_prompt = tool_call_prompt
        self.response_format_prompt = response_format_prompt
        self.tools_dict = {tool.name: tool for tool in subagents_plus_tools}
        self.graph = await self._build_graph()
        logger.info("Successfully initialized Top Agent with %d total tools", len(self.tools_dict))

    def _get_tool(self, tool_name: str) -> BaseTool | None:
        """Get a tool by name from the tools dict."""
        tool = self.tools_dict.get(tool_name)
        if tool is None:
            logger.error("Tool not found: %s. Available tools: %s", tool_name, list(self.tools_dict.keys()))
        return tool

    async def _plan_update_node(self, state: TopAgentState) -> TopAgentState:
        """
        Plan-update node: uses the LLM to dynamically update the execution plan
        based on tool results in the scratchpad, then clears the scratchpad.

        The LLM handles structural updates (marking [x], adjusting steps).
        Exact tool results are appended programmatically so nothing is lost.
        """
        if not state.agent_scratchpad:
            return state

        writer = get_stream_writer()
        logger.debug("Starting Plan Update Node")

        # Extract tool calls and results from the scratchpad.
        # scratchpad_lines → concise summary for the LLM prompt
        # tool_results_lines → exact results appended programmatically
        scratchpad_lines: list[str] = []
        tool_results_lines: list[str] = []
        pending_calls: dict[str, dict[str, Any]] = {}  # tool_call_id -> {name, args}
        for msg in state.agent_scratchpad:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc["id"] or ""
                    pending_calls[tc_id] = {"name": tc["name"], "args": tc["args"]}
                    scratchpad_lines.append(f"Called tool `{tc['name']}` with args: {tc['args']}")
            elif isinstance(msg, ToolMessage):
                call_info = pending_calls.pop(msg.tool_call_id, None)
                tool_name = (call_info["name"] if call_info else None) or getattr(msg, "name", None) or "tool"
                result_text = _get_content_text(msg)
                # Full result for programmatic appendix
                tool_results_lines.append(f"`{tool_name}` result:\n{result_text}")
                # Truncated for the LLM prompt
                truncated = result_text[:500] + "…" if len(result_text) > 500 else result_text
                scratchpad_lines.append(f"Result from `{tool_name}`: {truncated}")
            else:
                text = _get_content_text(msg)
                if text.strip():
                    scratchpad_lines.append(text)
        scratchpad_summary = "\n".join(scratchpad_lines)

        # Strip previous tool results section before sending plan to LLM,
        # but preserve them so they can be re-appended with the new results.
        plan_parts = state.plan.split(_TOOL_RESULTS_DELIMITER, 1)
        clean_plan = plan_parts[0].rstrip()
        previous_tool_results = plan_parts[1] if len(plan_parts) > 1 else ""

        system_content = (
            "You are a plan-tracking assistant. You will be given the user's question, an execution plan and "
            "a scratchpad of previous tool calls and their results.\n\n"
            "Your job:\n"
            "- Mark completed steps with [x] and append a concise result summary.\n"
            "- Keep pending steps with [ ].\n"
            "- Adjust, add, or remove remaining steps based on what was learned from the results.\n"
            "- Return ONLY the updated plan — no commentary, no preamble.\n"
        )

        thinking_tag = get_thinking_tag(self.llm, state.options.llm_reasoning)
        if thinking_tag:
            system_content += f"\n{thinking_tag}"

        user_question = ""
        if state.current_message and hasattr(state.current_message, "content"):
            user_question = str(state.current_message.content) if state.current_message.content else ""

        messages: list[BaseMessage] = [
            SystemMessage(content=system_content),
            HumanMessage(
                content=(
                    f"User question:\n{user_question}\n\n"
                    f"Current plan:\n{clean_plan}\n\nScratchpad (recent tool calls and results):\n{scratchpad_summary}"
                )
            ),
        ]

        llm_kwargs = get_llm_reasoning_bind_kwargs(self.llm, state.options.llm_reasoning)
        llm_to_use = self.llm.bind(**llm_kwargs) if llm_kwargs else self.llm

        result = await llm_to_use.ainvoke(messages, config=RunnableConfig(callbacks=self.callbacks))

        _, updated_plan = parse_reasoning_content(result)
        if not updated_plan:
            updated_plan = str(result.content) if hasattr(result, "content") else clean_plan

        # Programmatically append exact tool results so the agent has them,
        # combining previous results with new ones from this cycle.
        all_tool_results = previous_tool_results
        if tool_results_lines:
            new_results = "\n\n".join(tool_results_lines)
            all_tool_results = f"{previous_tool_results}\n\n{new_results}" if previous_tool_results else new_results
        if all_tool_results:
            updated_plan += _TOOL_RESULTS_DELIMITER + all_tool_results

        logger.info("Plan update node produced updated plan:\n%s", updated_plan)
        writer(AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content="Updated Plan:\n\n" + updated_plan))

        state.plan = updated_plan
        state.agent_scratchpad = []
        return state

    def _tool_accepts_param(self, tool_name: str, param_name: str) -> bool:
        """Check if a tool accepts a specific parameter by inspecting its schema."""
        tool = self.tools_dict.get(tool_name)
        if tool and hasattr(tool, "args_schema") and tool.args_schema is not None:
            schema_fields = getattr(tool.args_schema, "model_fields", {})
            return param_name in schema_fields
        return False

    async def astream(
        self,
        input_messages: list[BaseMessage],
        options: AgentRequestOptions | None = None,
    ) -> AsyncGenerator[AgentMessageChunk]:
        """Stream the agent's response."""
        if options is None:
            options = AgentRequestOptions()

        if not input_messages:
            raise RuntimeError(EMPTY_MESSAGES_ERROR)

        current_message = input_messages[-1]

        logger.info(f"Current message: {current_message.content[:50] if current_message.content else '(empty)'}...")

        # Get conversation_id from ContextVar
        thread_id = ContextState.get().conversation_id.get()
        previous_state = self.graph.get_state({"configurable": {"thread_id": thread_id}}).values

        if previous_state and self.max_history > 0:
            # Follow up question, add previous messages to the current messages
            logger.info("Follow a previous conversation %s: %s", thread_id, previous_state)
            # Retrieve conversation history from previous state
            conversation_history = previous_state.get("conversation_history", [])
            logger.info(f"Retrieved {len(conversation_history)} messages of conversation history from previous state")

            # Only summarize when history has reached max_history.
            # Summarize the older half into previous_conversation, keep the newer half.
            previous_conversation = previous_state.get("previous_conversation", "")
            half = self.max_history // 2

            if len(conversation_history) >= self.max_history:
                older_half = conversation_history[:half]
                conversation_history = conversation_history[half:]
                logger.info(
                    "History reached max_history (%d), summarizing older %d messages, keeping newer %d",
                    self.max_history,
                    len(older_half),
                    len(conversation_history),
                )

                older_text = "\n".join(_get_content_text(m) for m in older_half)

                summary_thinking_tag = get_thinking_tag(self.llm, options.llm_reasoning)
                summary_prompt = (
                    "Briefly summarize the conversation history in 2-3 sentences:\n"
                    "- What did the user ask?\n"
                    "- What tools were called?\n"
                    "- What was the high-level outcome?\n\n"
                    "Keep it concise.\n\n"
                    "Older conversation summary: {older_conversation_summary}\n"
                    "Latest messages:\n{latest_messages}"
                )

                summary_messages: list[BaseMessage] = []
                if summary_thinking_tag:
                    summary_messages.append(SystemMessage(content=summary_thinking_tag))
                summary_messages.append(
                    HumanMessage(
                        content=summary_prompt.format(
                            older_conversation_summary=previous_conversation,
                            latest_messages=older_text,
                        )
                    )
                )

                llm_kwargs = get_llm_reasoning_bind_kwargs(self.llm, options.llm_reasoning)
                llm_to_use = self.llm.bind(**llm_kwargs) if llm_kwargs else self.llm
                summary_result = await llm_to_use.ainvoke(
                    summary_messages, config=RunnableConfig(callbacks=self.callbacks)
                )
                summary_reasoning, summary_content = parse_reasoning_content(summary_result)
                if summary_reasoning:
                    previous_conversation = summary_content
                else:
                    previous_conversation = summary_content or summary_result.content

                logger.info(
                    "Summarized older history into previous_conversation (%d chars)", len(previous_conversation)
                )

            input_state = TopAgentState(
                current_message=copy.deepcopy(current_message),
                previous_conversation=previous_conversation,
                conversation_history=list(conversation_history),
                agent_scratchpad=[],
                final_answer="",
                options=options,
            )
        else:
            input_state = TopAgentState(
                current_message=copy.deepcopy(current_message),
                previous_conversation="",
                conversation_history=[],
                agent_scratchpad=[],
                options=options,
            )

        try:
            config: RunnableConfig = RunnableConfig(
                configurable={
                    "thread_id": thread_id,
                    "stream": True,
                },
                recursion_limit=self.max_iterations,
            )
            async for chunk in self.graph.astream(input=input_state, config=config, stream_mode="custom"):
                if isinstance(chunk, AgentMessageChunk):
                    yield chunk

        except Exception as ex:
            logger.exception("Failed to stream agent")
            error_chunk = AgentMessageChunk(
                type=AgentMessageChunkType.ERROR,
                content=f"Error: {ex}",
            )
            yield error_chunk
            user_message = "Sorry, I wasn't able to complete your request. Please try again. If the issue persists, please contact your administrator."
            yield AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=user_message)

    async def _plan_node(self, state: TopAgentState) -> TopAgentState:
        """
        Planning node: drafts a step-by-step execution plan using tool names/descriptions only.

        Invokes the LLM without tool bindings so it focuses on planning rather than executing.
        The resulting plan is stored in state.plan and emitted as a THOUGHT chunk.
        """
        writer = get_stream_writer()
        logger.debug("Starting Plan Node")

        if state.current_message is None:
            raise RuntimeError(EMPTY_MESSAGES_ERROR)

        question = state.current_message.content
        if not isinstance(question, str):
            question = str(question)

        # TODO: Hack for UI to show the uploaded video, use commands "/show" to by pass plan in next release.
        lowered_question = question.lower()
        if lowered_question.startswith("let's show the videos just uploaded"):
            logger.info("Plan node: by pass plan for showing uploaded video")
            state.plan = (
                "1. Call vst_video_clip tool in parallel with each video name as a separate input:"
                + lowered_question.removeprefix("let's show the videos just uploaded").removesuffix("?")
            )
            state.plan += (
                "\n\n 2. Format the result url into html tags like <video src='url' alt='video name'>video name</video>"
            )
            return state

        # Build one-line description per tool
        tool_descriptions = "\n".join(f"- {t.name}: {t.description}" for t in self.tools_dict.values())
        tool_descriptions_block = f"\n\nAvailable tools:\n{tool_descriptions}"
        previous_exec_feedback = ""
        if state.agent_scratchpad:
            previous_exec_feedback = "\n\nPrevious execution feedback:\n" + "\n".join(
                _get_content_text(m) for m in state.agent_scratchpad
            )

        planning_instruction = self.plan_prompt or (
            "Review the available tools, the conversation history, and the user's question, "
            "then produce a concise numbered execution plan. Start each step with a tool name and a brief description of the step.\n"
            "Put relevant context (e.g. sensor IDs or time ranges) from the conversation history directly in the plan steps "
            "so the execution agent does not need to re-read the history.\n"
            "If the user's request is too ambiguous to build a reliable plan, respond with EXACTLY:\n"
            "[USER] <your clarifying question>\n"
            "If user's question can be answered directly without any tools, respond with EXACTLY:\n"
            "[USER] <your answer>\n"
            "This will be sent back to the user directly — do NOT produce a plan in that case.\n\n"
            "Example plan:\n"
            "1. Call `get_sensor_ids` — resolve the camera the user mentioned (camera 3 from prior turn).\n"
            "2. Call `get_event_clips` with sensor_id from step 1 and time range 08:00-09:00 from the query.\n"
            "3. Summarize the clips and return them to the user.\n\n"
            "Example clarify:\n"
            "[USER] Which video or camera are you referring to? "
            "Please provide a sensor name or video ID so I can look it up.\n\n"
            "Example direct answer:\n"
            "what tools are available?\n"
            "[USER] The available tools are: ... (list of tools)\n"
        )

        # Include previous conversation summary in the system message so the plan can reference prior context
        logger.debug("Planning instruction: " + planning_instruction)
        logger.debug("Tool descriptions: " + tool_descriptions_block)
        summary_block = ""
        if state.previous_conversation:
            summary_block = f"\n\nPrevious conversation summary:\n{state.previous_conversation}\n\n"
            logger.debug("Summary: " + summary_block)
        system_content = (
            self.plan_system_prompt
            + planning_instruction
            + tool_descriptions_block
            + summary_block
            + previous_exec_feedback
        )

        thinking_tag = get_thinking_tag(self.llm, state.options.llm_reasoning)
        if thinking_tag:
            system_content += f"\n{thinking_tag}"

        # Include recent conversation history so the plan can reference prior turns
        messages: list[BaseMessage] = [SystemMessage(content=system_content)]

        if state.conversation_history and self.max_history > 0:
            messages.extend(state.conversation_history)
        messages.append(HumanMessage(content=question))

        llm_kwargs = get_llm_reasoning_bind_kwargs(self.llm, state.options.llm_reasoning)
        llm_to_use = self.llm.bind(**llm_kwargs) if llm_kwargs else self.llm

        result = await llm_to_use.ainvoke(messages, config=RunnableConfig(callbacks=self.callbacks))

        plan_reasoning, plan_text = parse_reasoning_content(result)
        if not plan_text:
            plan_text = str(result.content) if hasattr(result, "content") else ""

        logger.debug("Plan node produced plan:\n%s", plan_text)
        if plan_reasoning:
            logger.debug("Plan node reasoning:\n%s", plan_reasoning)

        # Check if the planner wants to ask the user for clarification
        if plan_text.strip().startswith(PLAN_CLARIFY_PREFIX):
            clarification = plan_text.strip()[len(PLAN_CLARIFY_PREFIX) :].strip()
            logger.info("Plan node requesting clarification: %s", clarification)
            state.final_answer = clarification
            return state

        writer(AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content="Plan: \n\n" + plan_text))
        state.plan = plan_text
        logger.info(f"Plan node produced plan: {plan_text}")
        return state

    async def agent_node(self, state: TopAgentState) -> TopAgentState:
        """
        Main reasoning node for the top agent.

        This node calls the LLM to decide what action to take next.
        Returns the updated state with the agent's response.
        """
        writer = get_stream_writer()
        logger.debug("Starting Agent Node")

        if state.current_message is None:
            raise RuntimeError(EMPTY_MESSAGES_ERROR)
        if (
            len(state.agent_scratchpad) == 0
            and isinstance(state.current_message.content, str)
            and state.current_message.content.strip() == ""
        ):
            logger.error("No human input passed to the agent.")
            writer(AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=NO_INPUT_ERROR_MESSAGE))
            state.final_answer = NO_INPUT_ERROR_MESSAGE
            return state

        question = state.current_message.content

        try:
            # Get the thinking tag based on the LLM model and llm_reasoning state
            thinking_tag = get_thinking_tag(self.llm, state.options.llm_reasoning)
            thinking_tag_formatted = f"\n{thinking_tag}" if thinking_tag else ""

            if thinking_tag:
                logger.info(f"Applying thinking tag: '{thinking_tag}'")

            llm_kwargs = get_llm_reasoning_bind_kwargs(self.llm, state.options.llm_reasoning)
            llm_to_use = self.llm_with_tools.bind(**llm_kwargs) if llm_kwargs else self.llm_with_tools

            if state.plan and self.plan_exec_prompt is not None:
                prompt_to_use = self.plan_exec_prompt
                logger.info("Using plan (updated by plan_update node):\n%s", state.plan)
                invoke_kwargs: dict[str, Any] = {
                    "question": question,
                    "plan_section": state.plan,  # Already updated by plan_update node
                    "current_time": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "thinking_tag": thinking_tag_formatted,
                }
            else:
                prompt_to_use = self.prompt
                invoke_kwargs = {
                    "question": question,
                    "conversation_summary": state.previous_conversation,
                    "agent_scratchpad": state.agent_scratchpad,
                    "conversation_history": state.conversation_history,
                    "current_time": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "thinking_tag": thinking_tag_formatted,
                }

            agent_to_use = prompt_to_use | llm_to_use
            output_message = await agent_to_use.ainvoke(
                invoke_kwargs,
                config=RunnableConfig(callbacks=self.callbacks),
            )

            reasoning, final_result = parse_reasoning_content(output_message)
            logger.debug("The user's question was: %s", question)
            logger.debug("The agent's thoughts are:\n%s", reasoning)
            logger.debug("The agent's final result is:\n%s", final_result)
            if reasoning:
                writer(AgentMessageChunk(type=AgentMessageChunkType.THOUGHT, content=reasoning))

            # Get tool_calls if output_message is AIMessage
            tool_calls: list[Any] = []
            if isinstance(output_message, AIMessage) and output_message.tool_calls:
                tool_calls = output_message.tool_calls

            # Check if we have a final answer
            if final_result and not tool_calls:
                state.final_answer = final_result
                logger.debug("Agent provided final answer (pending postprocessing validation)")
                # Still add the final answer to the scratchpad for the conversation history summary and postprocessing retries

            # Add agent response to scratchpad
            # Combine reasoning and content to preserve full context
            # Format the summary response without think tags to avoid confusion with the think tag in the system message
            if reasoning:
                full_content = f"The model's reasoning is: {reasoning}\nThe model's answer is: {final_result or ''}"
            else:
                full_content = final_result or ""

            # Local Nemotron Nano 9b v2 NIM requires content to be non-empty
            # Use a single space as a minimal valid placeholder instead of empty string
            model_name = getattr(self.llm, "model_name", "") or getattr(self.llm, "model", "")
            model_name = str(model_name).lower() if model_name else ""
            if full_content.strip() == "":
                logging.info("Full content is empty, setting to 'Agent wants to call tools.")
                full_content = "Agent wants to call tools."

            if tool_calls:
                state.agent_scratchpad.append(AIMessage(content=full_content, tool_calls=tool_calls))
            else:
                state.agent_scratchpad.append(AIMessage(content=full_content))

            return state

        except Exception as e:
            logger.exception("Failed to call agent_node")
            raise e

    async def tool_or_subagent_node(self, state: TopAgentState) -> TopAgentState:
        """
        Execute tools or sub-agents requested by the agent.

        This node can handle both:
        - Regular tools (like video_analytics_mcp.video_analytics.get_sensor_ids)
        - Sub-agents (like report_agent) that may return structured output with thinking traces
        """
        writer = get_stream_writer()
        try:
            logger.debug("Starting tool/sub-agent execution")
            if not state.agent_scratchpad or len(state.agent_scratchpad) == 0:
                raise RuntimeError(EMPTY_SCRATCHPAD_ERROR)
            last_message = state.agent_scratchpad[-1]
            if not isinstance(last_message, AIMessage):
                raise RuntimeError("Expected AIMessage in agent_scratchpad for tool execution")
            agent_output: AIMessage = last_message
            tool_calls: list[Any] = agent_output.tool_calls if hasattr(agent_output, "tool_calls") else []
            if not tool_calls:
                logger.warning("No tool calls found in agent output")
                return state
            requested_tool_names = [tool_call["name"] for tool_call in tool_calls]
            requested_tools = [self._get_tool(tool_name) for tool_name in requested_tool_names]
            if not requested_tools:
                configured_tool_names = list(self.tools_dict.keys())
                logger.warning(
                    "Some requested tools not found: %s. Available: %s",
                    requested_tool_names,
                    configured_tool_names,
                )
                error_message = HumanMessage(
                    content=TOOL_NOT_FOUND_ERROR_MESSAGE.format(
                        tool_name=requested_tool_names,
                        tools=configured_tool_names,
                    ),
                )
                state.agent_scratchpad.append(error_message)
                return state

            # Run the tool/sub-agent
            async def run_tool(tool: BaseTool | None, tool_call: dict[str, Any]) -> ToolMessage:
                try:
                    if tool is None:
                        return ToolMessage(
                            name=tool_call["name"],
                            tool_call_id=tool_call["id"],
                            content=f"Tool '{tool_call['name']}' not found",
                        )

                    logger.info(f"Executing tool/sub-agent: {tool_call['name']}")
                    tool_response: Any = None

                    # Check if this is a sub-agent that we should call natively for streaming
                    tool_name = tool_call["name"]
                    is_subagent = tool_name in self.subagent_names

                    # Build tool args once, filtering None values and injecting llm_reasoning/vlm_reasoning if supported
                    tool_args = {k: v for k, v in tool_call["args"].items() if v is not None}
                    if self._tool_accepts_param(tool_name, "llm_reasoning"):
                        tool_args["llm_reasoning"] = state.options.llm_reasoning
                        logger.info(f"Passing llm_reasoning={state.options.llm_reasoning} to {tool_name}")
                    if self._tool_accepts_param(tool_name, "vlm_reasoning"):
                        tool_args["vlm_reasoning"] = state.options.vlm_reasoning
                        logger.info(f"Passing vlm_reasoning={state.options.vlm_reasoning} to {tool_name}")
                    # Only inject search_source_type for search_agent (video_file/rtsp). report_agent and
                    # others use source_type with different semantics (e.g. sensor/place)
                    if tool_name == "search_agent" and self._tool_accepts_param(tool_name, "source_type"):
                        tool_args["source_type"] = state.options.search_source_type
                        logger.info(f"Passing source_type={state.options.search_source_type} to {tool_name}")
                    if self._tool_accepts_param(tool_name, "use_critic"):
                        tool_args["use_critic"] = state.options.use_critic
                        logger.info(f"Passing use_critic={state.options.use_critic} to {tool_name}")

                    # Use native streaming for configured sub-agents
                    final_chunks = []
                    if is_subagent:
                        # Yield sub-agent call message before processing
                        subagent_msg = f"Calling sub-agent: {tool_name}\nArgs: {tool_call['args']}"
                        writer(AgentMessageChunk(type=AgentMessageChunkType.SUBAGENT_CALL, content=subagent_msg))

                        # Emit TOOL_START telemetry for subagent
                        subagent_run_id = str(uuid4())
                        subagent_start_time = time.time()
                        saved_context = None
                        try:
                            step_manager = Context.get().intermediate_step_manager
                            # Save the current context state before emitting TOOL_START
                            context_state = step_manager._context_state
                            saved_context = context_state.active_span_id_stack.get().copy()

                            tool_start_payload = IntermediateStepPayload(
                                event_type=IntermediateStepType.TOOL_START,
                                framework=LLMFrameworkEnum.LANGCHAIN,
                                name=tool_name,
                                UUID=subagent_run_id,
                                data=StreamEventData(input=json.dumps(tool_call["args"])),
                                metadata=TraceMetadata(tool_inputs=tool_call["args"]),
                                usage_info=UsageInfo(token_usage=TokenUsageBaseModel()),
                            )
                            step_manager.push_intermediate_step(tool_start_payload)
                            logger.info(f"TOOL_START telemetry emitted for {tool_name} with UUID {subagent_run_id}")
                        except Exception as e:
                            logger.warning(f"Failed to emit TOOL_START telemetry for {tool_name}: {e}", exc_info=True)

                        nat_function = self.subagent_functions[tool_name]
                        async for chunk in nat_function.astream(tool_args):
                            if isinstance(chunk, AgentMessageChunk):
                                logger.debug(f"Received AgentMessageChunk from {tool_name}: type={chunk.type}")
                                if chunk.type == AgentMessageChunkType.FINAL:
                                    # Try to parse as AgentOutput JSON for sub-agents
                                    try:
                                        agent_output = AgentOutput.model_validate_json(chunk.content)
                                        logger.debug(f"Received AgentOutput from {tool_name} via FINAL chunk")
                                        final_content_parts = []
                                        if agent_output.messages:
                                            final_content_parts.extend(agent_output.messages)
                                        if agent_output.side_effects:
                                            for value in agent_output.side_effects.values():
                                                final_content_parts.append(f"{value}")

                                        final_content = "\n".join(final_content_parts)
                                        final_chunks.append(final_content)

                                        # Don't set final_answer from sub-agents so the planning is not blocked
                                        # Accumulate side_effects (download links, media URLs)
                                        # so they can be appended in finalize_node.
                                        side_effects_parts = []
                                        if agent_output.side_effects:
                                            # Keep artifact_note in scratchpad context only
                                            side_effects_parts = [
                                                f"{value}"
                                                for key, value in agent_output.side_effects.items()
                                                if key != "artifact_note"
                                            ]
                                            if side_effects_parts:
                                                state.subagent_side_effects.append("\n".join(side_effects_parts))
                                        logger.info(
                                            f"Accumulated side_effects from {tool_name}: {len(side_effects_parts)} items"
                                        )

                                        if agent_output.messages:
                                            messages_text = " ".join(agent_output.messages)
                                        else:
                                            messages_text = final_content
                                        artifact_note = (
                                            str(agent_output.side_effects.get("artifact_note", ""))
                                            if agent_output.side_effects
                                            else ""
                                        )
                                        if artifact_note:
                                            messages_text = (
                                                f"{messages_text}\n\n{artifact_note}"
                                                if messages_text
                                                else artifact_note
                                            )
                                        tool_response = f"tool: {tool_name} completed. Result: {messages_text}"
                                    except (json.JSONDecodeError, Exception):
                                        # Not AgentOutput JSON, treat as plain text
                                        final_chunks.append(chunk.content)
                                        logger.info(f"Raw output from {tool_name}")
                                        tool_response = chunk.content
                                else:
                                    # For non-FINAL chunks, yield directly
                                    writer(chunk)
                            else:
                                # Store non-AgentMessageChunk results
                                final_chunks.append(str(chunk))
                                tool_response = chunk

                        # Emit TOOL_END telemetry for subagent
                        try:
                            step_manager = Context.get().intermediate_step_manager
                            subagent_output = (
                                tool_response or "\n".join(final_chunks) or f"Subagent {tool_name} completed"
                            )
                            logger.info(
                                f"Emitting TOOL_END for {tool_name} with UUID {subagent_run_id}, output length: {len(str(subagent_output))}"
                            )

                            # Manually ensure the step is in outstanding_start_steps
                            # This is needed because the async subagent execution may have lost the context
                            if (
                                subagent_run_id not in step_manager._outstanding_start_steps
                                and saved_context is not None
                            ):
                                from nat.builder.intermediate_step_manager import OpenStep

                                logger.info(f"Manually registering outstanding step for {tool_name}")
                                parent_step_id = saved_context[-1] if saved_context else None
                                step_manager._outstanding_start_steps[subagent_run_id] = OpenStep(
                                    step_id=subagent_run_id,
                                    step_name=tool_name,
                                    step_type=IntermediateStepType.TOOL_START,
                                    step_parent_id=parent_step_id,
                                    prev_stack=saved_context,
                                    active_stack=[*saved_context, subagent_run_id],
                                )

                            tool_end_payload = IntermediateStepPayload(
                                event_type=IntermediateStepType.TOOL_END,
                                span_event_timestamp=subagent_start_time,
                                framework=LLMFrameworkEnum.LANGCHAIN,
                                name=tool_name,
                                UUID=subagent_run_id,
                                metadata=TraceMetadata(tool_outputs=subagent_output),
                                usage_info=UsageInfo(token_usage=TokenUsageBaseModel()),
                                data=StreamEventData(input=json.dumps(tool_call["args"]), output=subagent_output),
                            )
                            step_manager.push_intermediate_step(tool_end_payload)
                            logger.info(f"TOOL_END telemetry emitted for {tool_name}")
                        except Exception as e:
                            logger.warning(f"Failed to emit TOOL_END telemetry for {tool_name}: {e}", exc_info=True)
                    else:
                        # Use LangChain streaming for regular tools
                        async for chunk in tool.astream(
                            input=tool_args,
                            config=RunnableConfig(callbacks=self.callbacks),
                        ):
                            if isinstance(chunk, AgentMessageChunk):
                                logger.debug(f"Received AgentMessageChunk from {tool_call['name']}: type={chunk.type}")
                                # Yield the chunk directly to the stream writer
                                writer(chunk)
                                if chunk.type == AgentMessageChunkType.FINAL:
                                    final_chunks.append(chunk.content)
                                    # Mark that we have a final answer
                                    state.final_answer = chunk.content
                                    logger.info(f"Set state.final_answer from {tool_call['name']}")
                            else:
                                tool_response = chunk

                    # If no response was captured, use a default summary
                    if tool_response is None:
                        tool_response = f"tool: {tool_call['name']} completed"

                    # Convert tool response to string for scratchpad and check for summary field
                    tool_response_str = str(tool_response)

                    if (
                        not is_subagent
                        and not state.final_answer
                        and hasattr(tool_response, "summary")
                        and tool_response.summary
                    ):
                        # Extract summary but defer FINAL chunk until postprocessing validates it
                        final_content = tool_response.summary
                        state.final_answer = final_content
                        logger.info(f"Extracted summary from {tool_call['name']} (pending postprocessing validation)")
                        # Use a shorter message for scratchpad and reasoning trace
                        tool_response_str = f"Returned summary with {len(final_content)} characters"

                    # Yield tool call in reasoning trace for regular tools (even if we extracted a summary)
                    # Sub-agents already yielded their call message earlier
                    if not is_subagent:
                        # For regular tools, yield TOOL_CALL with call info and result
                        result_msg = (
                            f"Tool: {tool_call['name']}\nArgs: {tool_call['args']}\nResult: {tool_response_str}"
                        )
                        writer(AgentMessageChunk(type=AgentMessageChunkType.TOOL_CALL, content=result_msg))

                    logger.debug(
                        f"Tool {tool_call['name']} completed, final_answer={'set' if state.final_answer else 'not set'}"
                    )

                    # Convert empty tool response to placeholder
                    tool_content = tool_response
                    if not tool_content or (isinstance(tool_content, str) and tool_content.strip() == ""):
                        logger.warning(f"Tool {tool_call['name']} returned empty content, using placeholder")
                        tool_content = "Tool returned empty content"

                    return ToolMessage(
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"],
                        content=tool_content,
                    )

                except Exception as ex:
                    logger.exception("Tool execution failed")
                    error_response = f"Tool call failed: {ex!s}"
                    return ToolMessage(
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"],
                        content=error_response,
                    )

            # Execute all tool calls
            tasks = [run_tool(tool, tool_call) for tool, tool_call in zip(requested_tools, tool_calls, strict=False)]
            for task in asyncio.as_completed(tasks):
                tool_response = await task
                state.agent_scratchpad.append(tool_response)

            # Add final answer to scratchpad for conversation history summary and postprocessing retries
            if state.final_answer:
                state.agent_scratchpad.append(AIMessage(content=state.final_answer))

        except Exception as ex:
            logger.exception("Failed to call tool_or_subagent_node")
            state.agent_scratchpad.append(HumanMessage(content=str(ex)))

        return state

    async def _postprocessing_node(self, state: TopAgentState) -> TopAgentState:
        """Postprocess output: validate before finalizing the graph."""
        if not self.postprocessing or not self.postprocessing.config.enabled or not state.final_answer:
            return state

        user_query = ""
        if state.current_message and hasattr(state.current_message, "content"):
            user_query = str(state.current_message.content) if state.current_message.content else ""

        result = await self.postprocessing.process(
            state.final_answer,
            user_query=user_query,
            scratchpad=state.agent_scratchpad,
            llm_reasoning=state.options.llm_reasoning,
        )

        if result.passed:
            logger.info("Postprocessing passed")
        else:
            logger.info(f"Postprocessing failed: {result.feedback}")
            state.final_answer = ""
            feedback_message = f"{POSTPROCESSING_FEEDBACK_MARKER}\n{result.feedback}\nPlease try again."
            state.agent_scratchpad.append(HumanMessage(content=feedback_message))
            logger.info("Appended postprocessing feedback to scratchpad")

        return state

    async def _conditional_edge(self, state: TopAgentState) -> str:
        """Determine next action from agent node."""
        try:
            logger.debug("Starting Conditional Edge")

            # Check if we have a final answer
            if state.final_answer:
                logger.info("Agent has final answer, ending: %s", state.final_answer)
                return AgentDecision.END.value

            # Check last message in scratchpad
            if not state.agent_scratchpad:
                logger.debug("No scratchpad, routing to agent")
                return AgentDecision.AGENT.value

            agent_output = state.agent_scratchpad[-1]
            if isinstance(agent_output, AIMessage):
                if agent_output.tool_calls:
                    logger.info("Agent is calling %d tools", len(agent_output.tool_calls))
                    return AgentDecision.TOOL.value
                else:
                    logger.info("Agent has no tool calls, ending")
                    return AgentDecision.END.value
            else:
                # Tool message or human message, route back to agent
                logger.debug("Last message is not AIMessage, routing to agent")
                return AgentDecision.AGENT.value

        except Exception:
            logger.exception("Failed to determine next action")
            logger.warning("Ending graph traversal due to error")
            return AgentDecision.END.value

    async def _conditional_edge_from_tool(self, state: TopAgentState) -> str:
        """Conditional edge from tool node - check if we should end or continue to agent."""
        try:
            if state.final_answer:
                logger.info("Tool node set final_answer, ending graph traversal")
                return AgentDecision.END.value
            else:
                logger.debug("Tool finished, continuing to agent")
                return AgentDecision.AGENT.value
        except Exception:
            logger.exception("Failed to determine next step from tool")
            return AgentDecision.AGENT.value

    async def finalize_node(self, state: TopAgentState) -> TopAgentState:
        """Final node that emits FINAL chunk and updates conversation history."""
        # Append accumulated sub-agent side effects
        if state.subagent_side_effects:
            subagent_content = "\n\n".join(state.subagent_side_effects)
            if state.final_answer:
                state.final_answer = state.final_answer + "\n\n" + subagent_content
            else:
                state.final_answer = subagent_content

        if state.final_answer:
            # Remove backslash-escaped quotes (LLM artifact from JSON context, e.g. src=\"url\" -> src="url")
            state.final_answer = state.final_answer.replace('\\"', '"').replace("\\'", "'")
            # strip inline code quotes e.g. `abc` -> abc, but leave code blocks unchanged
            state.final_answer = re.sub(r"(?<!`)`(?!`)([^`]+)`(?!`)", r"\1", state.final_answer)

            writer = get_stream_writer()
            writer(AgentMessageChunk(type=AgentMessageChunkType.FINAL, content=state.final_answer))
            # clean up the agent_scratchpad
            state.agent_scratchpad = []

            if state.current_message:
                cleaned_response = strip_frontend_tags(state.final_answer)
                if cleaned_response and self.max_history > 0:
                    # Append new turn, keep last max_history messages

                    state.conversation_history.append(HumanMessage(content=state.current_message.content))
                    state.conversation_history.append(AIMessage(content=cleaned_response))
                    logger.info(
                        f"Updated conversation history in finalize_node: {len(state.conversation_history)} messages (max {self.max_history})"
                    )
        return state

    async def _build_graph(self) -> CompiledStateGraph:
        try:
            self.checkpointer = InMemorySaver()
            graph = StateGraph(TopAgentState)
            graph.add_node("agent", self.agent_node)
            graph.add_node("tool", self.tool_or_subagent_node)
            graph.add_node("finalize", self.finalize_node)

            if self.postprocessing:
                # Validate before ending the graph
                graph.add_node("postprocessing", self._postprocessing_node)
                end_target = "postprocessing"
            else:
                end_target = "finalize"

            if self.planning_enabled:
                graph.add_node("plan", self._plan_node)
                graph.add_node("plan_update", self._plan_update_node)
                graph.set_entry_point("plan")
                # If the plan node set final_answer (clarification), skip to finalize;
                # otherwise proceed to agent for execution.
                graph.add_conditional_edges(
                    "plan",
                    lambda s: AgentDecision.END.value if s.final_answer else AgentDecision.AGENT.value,
                    {
                        AgentDecision.END.value: end_target,
                        AgentDecision.AGENT.value: "agent",
                    },
                )
                # tool → plan_update (if no final_answer) or end_target (if final_answer set)
                conditional_edge_from_tool_outputs: dict[Hashable, str] = {
                    AgentDecision.END.value: end_target,
                    AgentDecision.AGENT.value: "plan_update",
                }
                graph.add_conditional_edges(
                    "tool", self._conditional_edge_from_tool, conditional_edge_from_tool_outputs
                )
                graph.add_edge("plan_update", "agent")
            else:
                graph.set_entry_point("agent")
                # Make tool -> agent edge conditional to support tools that set final_answer
                tool_edge_outputs: dict[Hashable, str] = {
                    AgentDecision.END.value: end_target,
                    AgentDecision.AGENT.value: "agent",
                }
                graph.add_conditional_edges("tool", self._conditional_edge_from_tool, tool_edge_outputs)
            conditional_edge_possible_outputs: dict[Hashable, str] = {
                AgentDecision.TOOL.value: "tool",
                AgentDecision.END.value: end_target,
                AgentDecision.AGENT.value: "agent",
            }
            graph.add_conditional_edges("agent", self._conditional_edge, conditional_edge_possible_outputs)

            if self.postprocessing:
                if self.planning_enabled:
                    graph.add_conditional_edges("postprocessing", lambda s: "finalize" if s.final_answer else "plan")
                else:
                    graph.add_conditional_edges("postprocessing", lambda s: "finalize" if s.final_answer else "agent")

            graph.add_edge("finalize", "__end__")
            self.graph = graph.compile(checkpointer=self.checkpointer)
            logger.info("Agent Graph built and compiled successfully")
            return self.graph
        except Exception:
            logger.exception("Failed to build the Agent Graph")
            raise


async def _extract_prompt_sections(
    llm: BaseChatModel,
    prompt_text: str,
    callbacks: list[BaseCallbackHandler] | None = None,
) -> tuple[str, str]:
    """Extract tool_call_prompt and response_format_prompt from the main prompt via LLM.

    Called at factory init time when planning is enabled but the user hasn't
    provided these prompts explicitly. The extracted prompts are used in the
    plan_exec_prompt so the execution agent knows how to call tools and format
    responses without re-reading the full system prompt.

    Returns:
        Tuple of (tool_call_prompt, response_format_prompt). Empty strings on failure.
    """
    extraction_system = (
        "You are a prompt analysis assistant. Given a system prompt, extract two specific sections:\n"
        "1. Tool Call Rules — any instructions about how to call tools, retry behavior, "
        "parameter requirements, error handling for tool calls.\n"
        "2. Response Format Rules — any instructions about response formatting, markdown, "
        "URL handling, output structure, phrases to avoid.\n\n"
        "Return ONLY the extracted text in the exact XML format below.\n"
        "If a section is not found in the prompt, leave the tags empty.\n\n"
        "<tool_call_prompt>\n</tool_call_prompt>\n"
        "<response_format_prompt>\n</response_format_prompt>"
    )
    messages: list[BaseMessage] = [
        SystemMessage(content=extraction_system),
        HumanMessage(content=f"Extract sections from this system prompt:\n\n{prompt_text}"),
    ]
    try:
        result = await llm.ainvoke(messages, config=RunnableConfig(callbacks=callbacks or []))
        content = result.content if isinstance(result.content, str) else str(result.content)

        tool_call_match = re.search(r"<tool_call_prompt>(.*?)</tool_call_prompt>", content, re.DOTALL)
        response_format_match = re.search(r"<response_format_prompt>(.*?)</response_format_prompt>", content, re.DOTALL)

        tool_call = tool_call_match.group(1).strip() if tool_call_match else ""
        response_format = response_format_match.group(1).strip() if response_format_match else ""

        logger.info(
            "Extracted prompt sections: tool_call=%d chars, response_format=%d chars",
            len(tool_call),
            len(response_format),
        )
        return tool_call, response_format
    except Exception:
        logger.exception("Failed to extract prompt sections via LLM, using empty defaults")
        return "", ""


async def _get_subagents(subagent_names: list[str], builder: Builder) -> tuple[list[BaseTool], dict[str, Any]]:
    """
    Setup sub-agents by fetching them as both LangChain tools and native NAT functions.

    Args:
        subagent_names: List of sub-agent names to setup
        builder: Builder instance for fetching tools and functions

    Returns:
        Tuple of (subagent_tools, subagent_functions) where:
        - subagent_tools: List of BaseTool for LLM binding
        - subagent_functions: Dict mapping subagent names to native NAT functions for streaming
    """
    subagent_functions: dict[str, Any] = {}
    subagent_tools: list[BaseTool] = []

    if not subagent_names:
        return subagent_tools, subagent_functions

    logger.info(f"Setting up sub-agents: {subagent_names}")
    for subagent_name in subagent_names:
        try:
            # Get as LangChain tool for the LLM
            subagent_tool = await builder.get_tool(subagent_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
            subagent_tools.append(subagent_tool)

            # Get as native NAT function for streaming
            nat_function = await builder.get_function(subagent_name)
            if nat_function and hasattr(nat_function, "astream"):
                subagent_functions[subagent_name] = nat_function
                logger.info(f"Registered {subagent_name} for native streaming")
            else:
                logger.warning(f"{subagent_name} does not support streaming (no astream method)")
        except Exception as e:
            logger.error(f"Failed to setup sub-agent {subagent_name}: {e}")

    return subagent_tools, subagent_functions


@register_function(config_type=TopAgentConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def top_agent(config: TopAgentConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Top-level routing agent with simple tool calling"""

    # Configure agent logger level
    vss_logger = logging.getLogger("vss_agents")
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    vss_logger.setLevel(log_level)
    # Configure handler if not already present
    if not vss_logger.handlers:
        new_handler = logging.StreamHandler()
        new_handler.setLevel(log_level)
        new_handler.setFormatter(
            logging.Formatter(fmt="%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s")
        )
        vss_logger.addHandler(new_handler)
        vss_logger.propagate = False
    else:
        for existing_handler in vss_logger.handlers:
            existing_handler.setLevel(log_level)

    logger.info(f"Logging configured at {config.log_level} level for all vss_agents modules")

    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    # --- Resolve tool_call_prompt and response_format_prompt -----------------
    tool_call_prompt = config.tool_call_prompt or ""
    response_format_prompt = config.response_format_prompt or ""
    prompts_explicitly_provided = bool(config.tool_call_prompt and config.response_format_prompt)

    # If planning is enabled and prompts weren't provided, extract them from the
    # main prompt via LLM so we can inject them into plan_exec_prompt.
    if config.planning_enabled and not prompts_explicitly_provided:
        tool_call_prompt, response_format_prompt = await _extract_prompt_sections(llm, config.prompt)

    # --- Build the main agent system prompt ----------------------------------
    # When the user provided tool_call / response_format separately they have
    # removed those sections from config.prompt, so we need to append them.
    # When extracted, the main prompt already contains them — appending again is
    # harmless (slight repetition) but keeps both paths identical.
    agent_prompt_text = config.prompt
    if not config.planning_enabled:
        if config.tool_call_prompt:
            agent_prompt_text += "\n\n" + config.tool_call_prompt
        if config.response_format_prompt:
            agent_prompt_text += "\n\n" + config.response_format_prompt

    prompt = ChatPromptTemplate(
        [
            (
                "system",
                agent_prompt_text
                + "\n\n"
                + "current time: {current_time}"
                + "\n\nPrevious conversation summary: {conversation_summary}"
                + "{thinking_tag}",
            ),
            MessagesPlaceholder(variable_name="conversation_history", optional=True),
            ("user", "{question}"),
            MessagesPlaceholder(variable_name="agent_scratchpad", optional=True),
        ]
    )

    # --- Build plan_exec_prompt (only when planning is enabled) --------------
    plan_exec_prompt: ChatPromptTemplate | None = None
    if config.planning_enabled:
        plan_exec_system = (
            "Follow the execution plan precisely to answer the user's question.\n"
            "All necessary context (sensor IDs, time ranges, etc.) should be already encoded in the plan.\n\n"
            "If the plan lacks a required context or input parameter, ask the user for the missing information.\n\n"
            "[x] means a step has been completed and the result is appended.\n\n"
            "Summarize and return the final answer to the user after all steps are completed.\n"
            "- Your final answer MUST answer ALL parts of the user's question (User's Question: {question}). "
            "If the user asked multiple things, you MUST answer each one. "
        )
        if tool_call_prompt:
            plan_exec_system += "\n\n## Tool call rules:\n " + tool_call_prompt
        if response_format_prompt:
            plan_exec_system += "\n\n## Response format rules:\n " + response_format_prompt
        plan_exec_system += "\n\ncurrent time: {current_time}{thinking_tag}"

        plan_exec_prompt = ChatPromptTemplate(
            [
                ("system", plan_exec_system),
                ("user", "User Question: {question}\n\nExecution Plan:\n{plan_section}\n\n"),
            ]
        )

    # Get regular tools
    tools = await builder.get_tools(tool_names=config.tool_names, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    # Get sub-agents both as LangChain tools (for LLM) and as native NAT functions (for streaming)
    subagent_tools, subagent_functions = await _get_subagents(config.subagent_names, builder)

    logger.info(f"Total tools: {len(tools)} regular, {len(subagent_tools)} sub-agents")

    # Use custom LLM for postprocessing if specified, otherwise use workflow LLM
    postprocessing_llm = llm
    if config.postprocessing and config.postprocessing.validators:
        llm_rule_validator_cfg = config.postprocessing.validators.llm_based_rule_validator
        if llm_rule_validator_cfg and llm_rule_validator_cfg.llm_name:
            logger.info(f"Using custom LLM for postprocessing: {llm_rule_validator_cfg.llm_name}")
            postprocessing_llm = await builder.get_llm(
                llm_rule_validator_cfg.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN
            )

    agent = cast(
        "TopAgent",
        await TopAgent(
            llm=llm,
            tools=tools,
            subagents=subagent_tools,
            subagent_functions=subagent_functions,
            max_iterations=config.max_iterations,
            max_history=config.max_history,
            prompt=prompt,
            postprocessing_config=config.postprocessing,
            postprocessing_llm=postprocessing_llm,
            planning_enabled=config.planning_enabled,
            plan_prompt=config.plan_prompt,
            plan_exec_prompt=plan_exec_prompt,
            plan_system_prompt=config.prompt,
            tool_call_prompt=tool_call_prompt,
            response_format_prompt=response_format_prompt,
        ),
    )

    async def _response_fn(
        request: ChatRequestOrMessage,
    ) -> AsyncGenerator[str]:
        """Streaming top agent response.

        Args:
            request: ChatRequestOrMessage with messages and optional reasoning parameters
        """
        # Validate as TopAgentRequest for typed access to per-request option fields
        typed_request = TopAgentRequest.model_validate(request.model_dump())
        options = AgentRequestOptions(
            llm_reasoning=typed_request.llm_reasoning
            if typed_request.llm_reasoning is not None
            else config.llm_reasoning,
            vlm_reasoning=typed_request.vlm_reasoning if typed_request.vlm_reasoning is not None else None,
            search_source_type=typed_request.search_source_type or "video_file",
            use_critic=typed_request.use_critic if typed_request.use_critic is not None else True,
        )

        # Override with WebSocket payload values if present (WebSocket requests don't pass params through request object)
        context = Context.get()
        if hasattr(context.metadata, "payload") and isinstance(context.metadata.payload, dict):
            payload = context.metadata.payload
            if "llm_reasoning" in payload:
                options.llm_reasoning = bool(payload["llm_reasoning"])
            if "vlm_reasoning" in payload:
                options.vlm_reasoning = bool(payload["vlm_reasoning"])
            if "search_source_type" in payload:
                options.search_source_type = str(payload["search_source_type"])
            if "use_critic" in payload:
                options.use_critic = bool(payload["use_critic"])
            logger.info(f"Extracted from WebSocket payload - {options}")

        logger.info("Creating Top Agent with options=%s", options)

        try:
            # Convert request to ChatRequest following NAT's agent pattern:
            # https://github.com/NVIDIA/NeMo-Agent-Toolkit/blob/6184d2fb/src/nat/agent/tool_calling_agent/register.py#L86-L99
            chat_request = GlobalTypeConverter.get().convert(request, to_type=ChatRequest)
            # Extract only the latest message. Conversation history is managed by agent state
            current_message = HumanMessage(content=_extract_text_content(chat_request.messages[-1]).get("content", ""))
            # Collect all steps for unified trace
            steps = []
            final_content = []
            step_num = 0

            # Stream agent responses
            async for chunk in agent.astream(
                input_messages=[current_message],
                options=options,
            ):
                if chunk.type == AgentMessageChunkType.THOUGHT:
                    step_num += 1
                    # Replace \n with spaces to clean up the display
                    clean_content = chunk.content.replace("\\n", " ").replace("\n", " ")
                    steps.append(f'<agent-think-step title="{step_num} - Thought">{clean_content}</agent-think-step>')
                elif chunk.type == AgentMessageChunkType.TOOL_CALL:
                    step_num += 1
                    clean_content = chunk.content.replace("\\n", " ").replace("\n", " ")
                    steps.append(f'<agent-think-step title="{step_num} - Tool Call">{clean_content}</agent-think-step>')
                elif chunk.type == AgentMessageChunkType.SUBAGENT_CALL:
                    step_num += 1
                    clean_content = chunk.content.replace("\\n", " ").replace("\n", " ")
                    steps.append(
                        f'<agent-think-step title="{step_num} - Sub-Agent Call">{clean_content}</agent-think-step>'
                    )
                elif chunk.type == AgentMessageChunkType.FINAL:
                    final_content.append(chunk.content)
                elif chunk.type == AgentMessageChunkType.ERROR:
                    step_num += 1
                    clean_content = chunk.content.replace("\\n", " ").replace("\n", " ")
                    steps.append(f'<agent-think-step title="{step_num} - Error">{clean_content}</agent-think-step>')

            # Yield all steps wrapped in unified agent-think
            if steps:
                steps_content = "\n".join(steps)
                agent_think_block = f"\n\n<agent-think>{steps_content}</agent-think>\n\n"
                logger.debug(f"Agent think block: {agent_think_block}")
                yield agent_think_block

            # Yield final content
            if final_content:
                final_output = "\n\n".join(final_content) + "\n\n"
                logger.debug(f"Final output: {final_output}")
                yield final_output

        except Exception as ex:
            logger.exception("Agent failed with exception")
            yield f"I seem to be having a problem. {ex}"

    async def _single_fn(request: ChatRequestOrMessage) -> str:
        message = ""
        async for chunk in _response_fn(request):
            message += chunk
        return message

    yield FunctionInfo.create(stream_fn=_response_fn, single_fn=_single_fn, input_schema=ChatRequestOrMessage)
