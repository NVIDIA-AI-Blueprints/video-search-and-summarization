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

from collections.abc import AsyncGenerator
from datetime import datetime
import logging
import os
from pathlib import Path
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import ObjectStoreRef
from nat.data_models.function import FunctionBaseConfig
from nat.object_store.models import ObjectStoreItem
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class ReportGenConfig(FunctionBaseConfig, name="report_gen"):
    """Configuration for the report generation tool."""

    output_dir: str = Field(default="/tmp/agent_reports", description="Local directory to save report files (backup)")

    object_store: ObjectStoreRef = Field(description="Reference to the object store for serving files via HTTP")

    base_url: str | None = Field(
        default=None, description="Domain name of the machine, if not provided, public ip will be used"
    )

    save_local_copy: bool = Field(default=True, description="Whether to also save a local copy of the report file")

    template_path: str = Field(default="", description="Path to template (relative to project root)")

    llm_name: str = Field(
        default="",
        description="Name of the LLM to use for custom report generation (required when template_type='custom')",
    )

    template_name: str | None = Field(
        default=None,
        description="Name of the main template file to use for custom reports, if not provided, it will format message history to a markdown report",
    )

    report_prompt: str = Field(
        default="",
        description="System prompt for the LLM to use when generating custom reports. Use {template} and {messages} as placeholders. Required when template_type='custom'.",
    )


class ReportGenInput(BaseModel):
    """Input for the report generation tool."""

    messages: list[Any] | str = Field(
        ...,
        description="The list of messages that covers all important informationthat will be used to generate the report",
    )


class ReportGenOutput(BaseModel):
    """Output from the report generation tool."""

    local_file_path: str = Field(..., description="Local file path where the report is saved")

    http_url: str = Field(..., description="HTTP URL to access the report file")

    object_store_key: str = Field(..., description="Key/filename in the object store")

    summary: str = Field(..., description="Brief summary of the report")

    file_size: int = Field(..., description="Size of the report file in bytes")

    content: str = Field(..., description="The actual markdown content of the generated report")


def _format_messages_to_markdown(messages: list[Any]) -> str:
    """Format messages into markdown report."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md_content = [
        "# Deep Search Report",
        "",
        f"**Generated:** {timestamp}",
        f"**No. of Messages:** {len(messages)}",
        "",
        "---",
        "",
        "",
    ]

    for i, message in enumerate(messages, 1):
        md_content.append(f"### Message {i}")
        md_content.append("")

        # Extract message details
        message_type = type(message).__name__
        md_content.append(f"**Message Type:** {message_type}")

        # Handle different message types
        if hasattr(message, "content"):
            content = getattr(message, "content", "")
            if content:
                md_content.append("**Content:**")
                md_content.append(f"```\n{content}\n```")

        # Handle tool calls in AIMessage
        if hasattr(message, "tool_calls") and message.tool_calls:
            md_content.append("**Tool Calls:**")
            for tool_call in message.tool_calls:
                tool_name = tool_call.get("name") or getattr(tool_call, "name", "Unknown")
                tool_args = tool_call.get("args") or getattr(tool_call, "args", {})
                md_content.append(f"- **Tool:** {tool_name}")
                md_content.append(f"  **Args:** {tool_args}")

        # Handle tool call id for ToolMessage
        if hasattr(message, "tool_call_id"):
            tool_call_id = getattr(message, "tool_call_id", "")
            md_content.append(f"**Tool Call ID:** {tool_call_id}")

        # Handle role/type
        if hasattr(message, "type"):
            role = getattr(message, "type", "")
            md_content.append(f"**Role:** {role}")

        md_content.append("")
        md_content.append("---")
        md_content.append("")

    # Add summary
    message_types: dict[str, int] = {}
    for message in messages:
        message_type = type(message).__name__
        message_types[message_type] = message_types.get(message_type, 0) + 1

    md_content.extend(
        [
            "## Summary",
            "",
            f"- **Total Messages:** {len(messages)}",
        ]
    )

    for msg_type, count in message_types.items():
        md_content.append(f"- **{msg_type}:** {count}")

    # Add navigation footer
    md_content.extend(
        [
            "---",
            "",
            "*This report was generated by the Metropolis Deep Search Report Generation Tool*",
            "",
            f"**File generated at:** {timestamp}",
            "",
        ]
    )

    return "\n".join(md_content)


def _load_custom_template(template_path: str, template_name: str) -> str:
    """Load a custom template from the specified path."""
    # Check if this is a package resource path (e.g., "warehouse_report:templates")
    if ":" in template_path:
        package_name, resource_dir = template_path.split(":", 1)
        try:
            from importlib.resources import files

            package_files = files(package_name)
            resource_path = f"{resource_dir}/{template_name}" if resource_dir else template_name
            return (package_files / resource_path).read_text()
        except Exception as e:
            logger.error(f"Failed to load template {template_name} from package {package_name}: {e}")
            return f"# Report\n\nTemplate '{template_name}' could not be loaded from package '{package_name}'.\n\nError: {e}\n\n"
    else:
        # Regular file path
        full_template_path = Path(template_path) / template_name
        try:
            with open(full_template_path, encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to load custom template {template_name} from {template_path}: {e}")
            return (
                f"# Report\n\nTemplate '{template_name}' could not be loaded from '{template_path}'.\n\nError: {e}\n\n"
            )


async def _format_custom_report(
    messages: list[Any], template_path: str, template_name: str, report_prompt: str, llm: Any
) -> str:
    """Format custom report using LLM to extract information from messages and populate template."""
    if not llm:
        logger.warning("No LLM provided for custom report generation, falling back to conversation format")
        return _format_messages_to_markdown(messages)

    try:
        template_content = _load_custom_template(template_path, template_name)
        messages_text = "\n\n".join(
            [f"**{getattr(msg, 'type', type(msg).__name__)}**: {getattr(msg, 'content', str(msg))}" for msg in messages]
        )
        # Substitute the template into the report_prompt, but escape template placeholders
        # so they don't get treated as prompt variables
        escaped_template = template_content.replace("{", "{{").replace("}", "}}")
        formatted_system_prompt = report_prompt.format(template=escaped_template)

        prompt_template = ChatPromptTemplate.from_messages(
            [("system", formatted_system_prompt), ("user", "Conversation to extract information from:\n\n{messages}")]
        )

        chain = prompt_template | llm
        response = await chain.ainvoke({"messages": messages_text})

        content: str = str(response.content).strip()

        # Remove markdown code blocks if present
        if content.startswith("```markdown"):
            content = content[11:-3]
        elif content.startswith("```"):
            content = content[3:-3]

        return content

    except Exception as e:
        logger.error(f"Error generating custom report with LLM: {e}")
        return _format_messages_to_markdown(messages)


@register_function(config_type=ReportGenConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def report_gen(config: ReportGenConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Tool for formatting agent conversation messages into markdown documents(reports) and serving them via HTTP."""

    # Get the object store client
    object_store = await builder.get_object_store_client(config.object_store)

    async def _report_gen(trace_input: ReportGenInput) -> ReportGenOutput:
        """
        This tool formats agent conversation messages into markdown documents, saves them to an object store,
        and provides HTTP URLs for easy access. It can also optionally save local copies.
        """

        # Ensure messages is a list
        if isinstance(trace_input.messages, str):
            raise ValueError("messages must be a list of messages, not a string")

        if config.template_name:
            if not config.template_path:
                raise ValueError("template_path must be configured when template_type='custom'")

            if not config.llm_name:
                raise ValueError("llm_name must be configured when template_type='custom'")

            if not config.report_prompt:
                raise ValueError("report_prompt must be configured when template_type='custom'")

            # Get LLM for report generation
            try:
                llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
                logger.debug(f"LLM {config.llm_name} loaded for custom report generation")
            except Exception as e:
                raise ValueError(f"Failed to load LLM {config.llm_name}: {e}") from e

            markdown_content = await _format_custom_report(
                messages=trace_input.messages,
                template_path=config.template_path,
                template_name=config.template_name,
                report_prompt=config.report_prompt,
                llm=llm,
            )
        else:
            # Default to conversation format
            markdown_content = _format_messages_to_markdown(
                messages=trace_input.messages,
            )

        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"agent_report_{timestamp}.md"

        # Convert to bytes
        content_bytes = markdown_content.encode("utf-8")
        file_size = len(content_bytes)

        # Create object store item with metadata
        metadata = {
            "timestamp": timestamp,
            "generated_at": datetime.now().isoformat(),
            "messages_count": str(len(trace_input.messages)),
            "file_size": str(file_size),
            "content_type": "text/markdown",
        }

        object_store_item = ObjectStoreItem(data=content_bytes, content_type="text/markdown", metadata=metadata)

        # Save to object store
        await object_store.upsert_object(filename, object_store_item)

        # Generate HTTP URL
        if config.base_url:
            http_url = f"{config.base_url}/static/{filename}"
        else:
            # get public ip of the machine
            import urllib.request

            def get_public_ip() -> str:
                try:
                    with urllib.request.urlopen("https://api.ipify.org") as response:
                        result: str = response.read().decode("utf-8")
                        return result
                except Exception:
                    return "127.0.0.1"

            public_ip = get_public_ip()
            http_url = f"http://{public_ip}:8000/static/{filename}"

        # Save local copy if requested
        local_file_path = ""
        if config.save_local_copy:
            # Create output directory
            Path(config.output_dir).mkdir(parents=True, exist_ok=True)
            local_file_path = os.path.join(config.output_dir, filename)

            with open(local_file_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            logger.info(f"Local report saved to: {local_file_path}")

        logger.info(f"Report saved to object store and available at: {http_url}")

        # Generate summary
        messages_count = len(trace_input.messages)
        summary = f"Report saved successfully with {messages_count} messages. \nAvailable at: {http_url}"

        return ReportGenOutput(
            local_file_path=local_file_path,
            http_url=http_url,
            object_store_key=filename,
            summary=summary,
            file_size=file_size,
            content=markdown_content,
        )

    # Create function info with primary function
    function_info = FunctionInfo.create(
        single_fn=_report_gen,
        description=_report_gen.__doc__,
        input_schema=ReportGenInput,
        single_output_schema=ReportGenOutput,
    )

    # Add additional utility functions
    # function_info.add_tool(_get_trace_info)
    # function_info.add_tool(_list_recent_traces)
    # function_info.add_tool(_delete_trace)

    yield function_info
