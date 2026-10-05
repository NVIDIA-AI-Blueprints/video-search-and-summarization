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
from datetime import datetime
from importlib.resources import files
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any

try:
    import markdown
    from xhtml2pdf import pisa

    PDF_CONVERSION_AVAILABLE = True
except ImportError:
    PDF_CONVERSION_AVAILABLE = False

from langchain_core.prompts import ChatPromptTemplate
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.component_ref import ObjectStoreRef
from nat.data_models.function import FunctionBaseConfig
from nat.object_store.models import ObjectStoreItem
from pydantic import BaseModel
from pydantic import Field

from vss_agents.tools.video_understanding import extend_timestamp
from vss_agents.utils.reasoning_utils import get_llm_reasoning_bind_kwargs
from vss_agents.utils.reasoning_utils import get_thinking_tag

logger = logging.getLogger(__name__)

_REPORT_VLM_GROUNDING_INSTRUCTION = """
Grounding requirements:
- Report only facts directly supported by the supplied video evidence (visible, and audible when audio is available).
- Do not make unsupported claims about causes, contributing factors, severity, injuries, impact, identities, roles, or counts.
- Actions, outcomes, and responses may be reported only when directly supported by the available evidence.
- Do not invent identities, roles, people, vehicles, object IDs, or counts.
- Do not treat the number of object IDs as a people or vehicle count.
- Date/time values may be reformatted for readability only when the underlying instant is preserved and the timezone is stated explicitly.
- If a requested detail is not supported by the evidence, state that it is Unknown or N/A.
""".strip()

_REPORT_LLM_GROUNDING_INSTRUCTION = """
Grounding requirements (these override any conflicting template instruction):
- Authoritative facts take precedence; the full alert metadata (including geolocation) may supply fields not listed there.
- Copy authoritative values exactly into corresponding report fields; do not replace or reinterpret them.
- Treat video-understanding results only as evidence of directly supported observations (visible, and audible when audio evidence is present).
- Do not make unsupported claims about causes, contributing factors, severity, injuries, impact, identities, roles, or counts.
- Actions, outcomes, and responses may be reported only when directly supported by the available evidence.
- Do not invent identities, roles, people, vehicles, object IDs, or counts.
- Do not treat the length of objectIds as a people or vehicle count; use trusted counts from authoritative facts only when provided.
- Date/time values may be reformatted for readability only when the underlying instant is preserved and the timezone is stated explicitly.
- Use "Unknown" or "N/A" whenever the supplied evidence does not support a field.
- Structural completeness of the template is not proof of factual accuracy.
""".strip()

_RESOURCES_HEADING_RE = re.compile(r"^##\s*Resources\b", re.IGNORECASE | re.MULTILINE)
_THINK_OPEN_RE = re.compile(r"<think\b", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think>", re.IGNORECASE)
_TABLE_SEPARATOR_RE = re.compile(r"^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_UNFILLED_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
# Labeled values on one line, including a leading bullet. [ \t] must not cross newlines.
_PROSE_LABELED_VALUE_RE = re.compile(
    r"^[ \t]*(?:-[ \t]*)?\*\*(.+?)\*\*[ \t]*:?[ \t]*(.*)$",
    re.MULTILINE,
)
# Bounded hashes plus spaces; trailing ### is stripped in Python so this stays linear.
_ATX_HEADING_RE = re.compile(r"^(#{1,6})[ \t]*(.*\S)[ \t]*$", re.MULTILINE)
_SETEXT_HEADING_RE = re.compile(r"(?m)^(?P<title>[^#|\n][^\n]*)\n(?P<underline>[-=])(?P=underline){2,}[ \t]*$")
_TRAILING_HEADING_HASHES_RE = re.compile(r"[ \t]+#+\s*$")
_FENCED_BLOCK_RE = re.compile(r"```[\w-]*[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)
_SECTION_UNKNOWN_RE = re.compile(r"\b(Unknown|N/A)\b", re.IGNORECASE)
_BLANK_RENDERED_VALUE_RE = re.compile(r"(?i)<br\s*/?>|&nbsp;")


class ReportContentValidationError(ValueError):
    """Raised when generated report Markdown fails content validation before publication."""

    def __init__(self, reason: str, *, response_len: int = 0, body_len: int = 0) -> None:
        self.reason = reason
        self.response_len = response_len
        self.body_len = body_len
        super().__init__(
            f"Generated report failed content validation ({reason}; response_len={response_len}, body_len={body_len})"
        )


def _get_object_store_url(object_store: Any, filename: str, config: "TemplateReportGenConfig") -> str:
    """
    Get HTTP URL for a file from any object store type.

    Supports:
    - S3/MinIO object store (construct URL from endpoint)
    - in_memory and other stores (use NAT file server /static/ endpoint)

    Args:
        object_store: The object store instance
        filename: The file key/name
        config: The template report gen config

    Returns:
        str: HTTP URL to access the file
    """
    # S3/MinIO object store - construct URL from attributes
    if hasattr(object_store, "endpoint_url") and hasattr(object_store, "bucket_name"):
        endpoint = object_store.endpoint_url
        bucket = object_store.bucket_name
        # Remove trailing slash from endpoint
        endpoint = endpoint.rstrip("/")
        return f"{endpoint}/{bucket}/{filename}"

    # For in_memory and other stores - use NAT's /static/ endpoint from config
    # The file server is configured via general.front_end.object_store
    # Remove trailing slash and construct URL
    base_url = config.base_url.rstrip("/")
    return f"{base_url}/{filename}"


def _replace_public_urls_with_private(
    markdown_content: str, vst_internal_url: str | None, vst_external_url: str | None
) -> str:
    """
    Replace external (public) URLs in markdown image tags with internal (private) IP URLs for PDF generation.

    Handles markdown format: ![alt](url)

    Args:
        markdown_content: Markdown content with image URLs
        vst_internal_url: Internal VST URL (e.g., 'http://10.0.0.1:30888') - private IP for PDF
        vst_external_url: External VST URL (e.g., 'http://public.example.com:30888') - public URL to replace

    Returns:
        Markdown content with image URLs updated to use private IP
    """
    if not vst_internal_url or not vst_external_url:
        logger.debug(
            f"URL replacement skipped - vst_internal_url: {vst_internal_url is not None}, "
            f"vst_external_url: {vst_external_url is not None}"
        )
        return markdown_content

    # Extract base URLs (scheme + host + port)
    internal_match = re.match(r"(https?://[^/]+)", vst_internal_url)
    external_match = re.match(r"(https?://[^/]+)", vst_external_url)

    if not internal_match or not external_match:
        logger.warning(f"Could not parse URLs - internal: {vst_internal_url}, external: {vst_external_url}")
        return markdown_content

    internal_base = internal_match.group(1)  # e.g., 'http://10.0.0.1:30888'
    external_base = external_match.group(1)  # e.g., 'http://203.0.113.1:30888'

    logger.info(
        f"Replacing external URL '{external_base}' with internal URL '{internal_base}' in markdown image URLs for PDF"
    )

    # Replace URLs in markdown image format: ![alt](URL)
    def replace_md_img(match: re.Match[str]) -> str:
        full_match = match.group(0)
        url = match.group(2)

        # Replace external base with internal base if found
        if external_base in url:
            new_url = url.replace(external_base, internal_base)
            logger.debug(f"Replacing image URL: {url} -> {new_url}")
            return full_match.replace(url, new_url)

        return full_match

    # Replace in ![alt](url) format - the classic markdown format
    result = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_md_img, markdown_content)

    logger.info("URL replacement completed for template report PDF generation")

    return result


class TemplateReportGenConfig(FunctionBaseConfig, name="template_report_gen"):
    """Configuration for the template report generation tool."""

    object_store: ObjectStoreRef = Field(description="Reference to the object store for serving files via HTTP")

    base_url: str = Field(
        default="http://localhost:8000/static",
        description="Base URL for file server (used for in_memory and other non-S3 object stores). Should end with /static for NAT file server.",
    )

    template_path: str | None = Field(
        default="",
        description="Path to template (relative to project root), if not provided, it will skip the template formatting and use the output from VLM directly for the port",
    )
    output_dir: str = Field(
        default="./agent_reports",
        description="Base directory for local copies. Reports will be saved in {output_dir}/{sensor_id}/ subdirectories",
    )

    save_local_copy: bool = Field(
        default=False,
        description="Whether to also save a local copy of the report files organized by sensor_id",
    )
    use_sensor_id_prefix_for_object_store_path: bool = Field(
        default=False,
        description="Whether to prefix the object store path with sensor_id",
    )
    llm_name: str = Field(
        default="",
        description="Name of the LLM to use for custom report generation (required when template_type='custom')",
    )

    template_name: str | None = Field(
        default=None,
        description="Name of the main template file to use for custom reports, if not provided, it will skip the template formatting and use the output from VLM directly for the port",
    )
    agent_version: str = Field(
        default="v1.0.0",
        description="Version of the AI agent to include in the report",
    )
    video_understanding_tool: str = Field(
        default="",
        description="Name of the video understanding tool to use for custom report generation (required when template_type='custom')",
    )
    vlm_prompts: list[str] = Field(
        default=[],
        description="List of prompts to query the VLM for video understanding",
    )

    report_prompt: str = Field(
        default="",
        description="System prompt for the LLM to use when generating custom reports. Must contain {template} for the report template. ",
    )
    required_report_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Optional explicit field labels that must appear in the generated Markdown with a "
            "non-empty resolved value (or Unknown/N/A for that label). Empty (default) means "
            "generic body validation only: reject empty/thinking/resources-only output. "
            "Does not infer requirements from template titles, headings, or placeholder lists, "
            "so customized templates and prompt overrides remain supported."
        ),
    )
    required_report_sections: list[str] = Field(
        default_factory=list,
        description=(
            "Optional Markdown heading titles that must appear with supported content or an "
            "explicit Unknown/N/A in that section. Empty (default) does not require any section. "
            "Headings are not inferred from the template, so renamed or removed sections stay valid "
            "unless this list is updated to match."
        ),
    )
    include_picture_url: bool = Field(
        default=True,
        description="Whether to include the picture URL in the report",
    )
    picture_url_tool: FunctionRef = Field(
        default="vst_picture_url",
        description="A tool to be used to get the picture URL by sensor ID and timestamp(default to use VST service)",
    )
    video_url_tool: str | None = Field(
        default=None,
        description="A tool to be used to get the video URL by sensor ID and timestamp, only required if we use VST for media storage",
    )
    geolocation_tool: FunctionRef | None = Field(
        default=None,
        description="A tool to fetch geolocation information from latitude and longitude coordinates",
    )

    vst_internal_url: str | None = Field(
        default=None,
        description="Internal VST URL for API calls (e.g., 'http://${INTERNAL_IP}:30888'). Used for PDF generation with private IPs.",
    )

    vst_external_url: str | None = Field(
        default=None,
        description="External VST URL for client-facing URLs (e.g., 'http://${EXTERNAL_IP}:30888'). Used to identify URLs to replace in PDFs.",
    )


class TemplateReportGenInput(BaseModel):
    """Input for the report generation tool."""

    alert_sensor_id: str = Field(..., description="Sensor ID for which alerts are requested")
    alert_from_timestamp: str = Field(..., description="Start timestamp in ISO format")
    alert_to_timestamp: str = Field(..., description="End timestamp in ISO format")
    alert_metadata: dict = Field(..., description="Metadata for the alert")
    vlm_reasoning: bool | None = Field(None, description="Enable VLM reasoning mode for video analysis")
    llm_reasoning: bool | None = Field(None, description="Enable LLM reasoning mode for report generation")


class TemplateReportGenOutput(BaseModel):
    """Output from the report generation tool."""

    http_url: str = Field(..., description="HTTP URL to access the markdown report file")
    pdf_url: str = Field(..., description="HTTP URL to access the PDF report file")
    object_store_key: str = Field(..., description="Key/filename in the object store")
    summary: str = Field(..., description="Brief summary of the report")
    file_size: int = Field(..., description="Size of the markdown report file in bytes")
    pdf_file_size: int = Field(..., description="Size of the PDF report file in bytes")
    content: str = Field(..., description="The actual markdown content of the generated report")
    image_url: str = Field(..., description="The URL of the image")
    video_url: str | None = Field(None, description="The URL of the video")


def _convert_markdown_to_pdf(markdown_file_path: str, output_pdf_path: str) -> bool:
    """Convert markdown file to PDF using Python packages."""
    if not PDF_CONVERSION_AVAILABLE:
        logger.warning("PDF conversion not available. Install 'markdown' and 'xhtml2pdf' packages.")
        return False

    try:
        # Read markdown file
        with open(markdown_file_path, encoding="utf-8") as f:
            markdown_content = f.read()

        # Convert markdown to HTML
        html_content = markdown.markdown(markdown_content, extensions=["tables", "fenced_code"])

        # Add professional CSS styling with NVIDIA branding for better PDF appearance
        styled_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                * {{
                    box-sizing: border-box;
                }}
                body {{
                    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Arial, sans-serif;
                    font-size: 12px;
                    line-height: 1.6;
                    margin: 12mm;
                    color: #000000;
                    background-color: #ffffff;
                }}
                h1 {{
                    color: #000000;
                    font-size: 26px;
                    font-weight: bold;
                    margin-top: 1.5em;
                    margin-bottom: 0.75em;
                    padding-bottom: 0.5em;
                    border-bottom: 4px solid #76B900;
                    text-transform: uppercase;
                }}
                h1:first-child {{
                    margin-top: 0;
                }}
                h2 {{
                    color: #000000;
                    font-size: 20px;
                    font-weight: bold;
                    margin-top: 1.5em;
                    margin-bottom: 0.6em;
                    padding-bottom: 0.4em;
                    border-bottom: 3px solid #76B900;
                }}
                h3 {{
                    color: #000000;
                    font-size: 16px;
                    font-weight: bold;
                    margin-top: 1.25em;
                    margin-bottom: 0.5em;
                }}
                h4 {{
                    color: #1a1a1a;
                    font-size: 14px;
                    font-weight: bold;
                    margin-top: 1em;
                    margin-bottom: 0.4em;
                }}
                p {{
                    margin: 0.6em 0;
                    text-align: justify;
                }}
                ul, ol {{
                    margin: 0.6em 0;
                    padding-left: 1.5em;
                }}
                li {{
                    margin: 0.3em 0;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 1em 0;
                    font-size: 11px;
                    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.15);
                    border-radius: 0;
                    overflow: hidden;
                    line-height: 1.3;
                }}
                th, td {{
                    border: 1px solid #d0d0d0;
                    padding: 6px 10px;
                    text-align: left;
                    vertical-align: top;
                    line-height: 1.3;
                }}
                th {{
                    background: linear-gradient(to bottom, #76B900, #669900);
                    color: #76B900;
                    font-weight: bold;
                    text-transform: uppercase;
                    font-size: 11px;
                    border-bottom: 2px solid #669900;
                }}
                tr:nth-child(even) {{
                    background-color: #f5f5f5;
                }}
                tr:hover {{
                    background-color: #e8f5d0;
                }}
                img {{
                    max-width: 100%;
                    height: auto;
                    display: block;
                    margin: 1em auto;
                    border-radius: 2px;
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
                }}
                code {{
                    background-color: #f0f0f0;
                    color: #000000;
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-family: 'Courier New', Courier, monospace;
                    font-size: 10px;
                    font-weight: bold;
                }}
                pre {{
                    background-color: #f5f5f5;
                    border: 1px solid #d0d0d0;
                    border-left: 4px solid #76B900;
                    padding: 12px;
                    border-radius: 2px;
                    overflow-x: auto;
                    margin: 1em 0;
                }}
                pre code {{
                    background-color: transparent;
                    color: #000000;
                    padding: 0;
                    font-size: 10px;
                }}
                blockquote {{
                    border-left: 4px solid #76B900;
                    margin: 1em 0;
                    padding: 0.5em 0 0.5em 1em;
                    background-color: #f5f5f5;
                    color: #1a1a1a;
                    font-style: italic;
                }}
                hr {{
                    border: none;
                    border-top: 2px solid #76B900;
                    margin: 2em 0;
                }}
                /* FIX: word-break and overflow-wrap are consolidated here (single
                   'a' rule) so long URLs can wrap in the PDF. Without these,
                   text-align:justify on <p> stretches the gap between label
                   text and an unbreakable URL on the same justified line. */
                a {{
                    color: #76B900;
                    text-decoration: none;
                    font-weight: bold;
                    border-bottom: 1px solid transparent;
                    transition: border-bottom 0.2s;
                    word-break: break-all;
                    overflow-wrap: break-word;
                }}
                a:hover {{
                    border-bottom: 1px solid #76B900;
                }}
                strong {{
                    font-weight: 700;
                    color: #000000;
                }}
                em {{
                    font-style: italic;
                    color: #1a1a1a;
                }}
                /* Make list category headers bold */
                dt {{
                    font-weight: 700;
                    color: #000000;
                    margin-top: 0.5em;
                }}
                .page-break {{
                    page-break-after: always;
                }}
                @page {{
                    margin: 15mm;
                    size: A4;
                }}
                @media print {{
                    body {{
                        margin: 0;
                    }}
                        h1, h2, h3 {{
                        page-break-after: avoid;
                    }}
                        table, figure, img {{
                        page-break-inside: avoid;
                    }}
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """

        # Convert HTML to PDF using xhtml2pdf
        with open(output_pdf_path, "wb") as pdf_file:
            pisa_status = pisa.CreatePDF(styled_html, dest=pdf_file)

        if pisa_status.err:
            logger.error(f"PDF conversion had errors: {pisa_status.err}")
            return False

        logger.info(f"Successfully converted markdown to PDF: {output_pdf_path}")
        return True

    except Exception as e:
        logger.error(f"Error converting markdown to PDF: {e}")
        return False


def _load_custom_template(template_path: str, template_name: str) -> str:
    """Load a custom template from the specified path."""
    # Check if this is a package resource path (e.g., "warehouse_report:templates")
    if ":" in template_path:
        package_name, resource_dir = template_path.split(":", 1)
        try:
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


async def _fetch_cv_metadata(
    report_input: TemplateReportGenInput,
    behavior_tool: Any | None,
) -> str:
    """Fetch CV metadata (behavior data) and add counts to alert metadata."""
    cv_metadata_str = ""
    behavior_data_result = None

    if behavior_tool:
        behavior_data_result = await _fetch_behavior_data(
            behavior_tool,
            report_input.alert_sensor_id,
            report_input.alert_from_timestamp,
            report_input.alert_to_timestamp,
        )
        cv_metadata_str = behavior_data_result["cv_metadata"]
        logger.info(f"CV metadata fetched: {cv_metadata_str[:200]}...")

        # Add people and vehicle counts to alert metadata
        report_input.alert_metadata["people_count"] = behavior_data_result["people_count"]
        report_input.alert_metadata["vehicle_count"] = behavior_data_result["vehicle_count"]

    return cv_metadata_str


async def _fetch_proximity_data(
    report_input: TemplateReportGenInput,
    frames_enhanced_tool: Any | None,
) -> float | None:
    """Fetch proximity threshold and add to alert metadata."""
    proximity_threshold = None
    if frames_enhanced_tool:
        proximity_threshold = await _fetch_proximity_threshold(
            frames_enhanced_tool,
            report_input.alert_sensor_id,
            report_input.alert_from_timestamp,
            report_input.alert_to_timestamp,
        )
    return proximity_threshold


async def _fetch_geolocation_data(
    report_input: TemplateReportGenInput,
    geolocation_tool: Any | None,
) -> dict[str, Any]:
    """Extract location from alert metadata and fetch geolocation information."""
    geolocation_data: dict[str, Any] = {}

    if not geolocation_tool:
        logger.warning("Geolocation tool not configured, skipping geolocation data fetch")
        return geolocation_data

    try:
        location_str = report_input.alert_metadata.get("info", {}).get("location")
        if not location_str:
            logger.warning(
                f"No location information found in alert metadata. Info field: {report_input.alert_metadata.get('info')}"
            )
            return geolocation_data

        # Parse the location string "latitude,longitude,elevation" to extract latitude and longitude
        location_parts = location_str.split(",")
        if len(location_parts) < 2:
            logger.warning(f"Invalid location format: {location_str}")
            return geolocation_data

        latitude = float(location_parts[0])
        longitude = float(location_parts[1])
        logger.info(f"latitude: {latitude}, longitude: {longitude}")

        geo_result = await geolocation_tool.ainvoke(
            input={
                "latitude": latitude,
                "longitude": longitude,
            }
        )

        geolocation_data = geo_result.model_dump()
        logger.info(f"Geolocation data: {geolocation_data}")

    except Exception as e:
        logger.error(f"Failed to fetch geolocation data: {e}", exc_info=True)

    return geolocation_data


def _extract_primary_object_id(alert_metadata: dict[str, Any]) -> Any:
    """
    Resolve primaryObjectId from the incident API shape.

    OpenAPI models ``info`` as a free-form string map; warehouse fixtures store
    ``info.primaryObjectId``. Also accept ``info.primary_object_id`` and a
    top-level ``primaryObjectId`` if present.
    """
    info = alert_metadata.get("info")
    if isinstance(info, dict):
        for key in ("primaryObjectId", "primary_object_id"):
            if info.get(key) is not None:
                return info.get(key)
    return alert_metadata.get("primaryObjectId")


def _extract_object_ids_from_incident(alert_metadata: dict) -> list[str]:
    """
    Extract object IDs from incident metadata.

    Looks for:
    - objectIds field (list of IDs)
    - info.primaryObjectId / info.primary_object_id (single ID)

    Args:
        alert_metadata: The incident metadata dictionary

    Returns:
        List of unique object IDs as strings
    """
    object_ids = set()

    # Extract from objectIds field
    if alert_metadata.get("objectIds"):
        if isinstance(alert_metadata["objectIds"], list):
            object_ids.update(alert_metadata["objectIds"])
        else:
            object_ids.add(alert_metadata["objectIds"])

    primary_id = _extract_primary_object_id(alert_metadata)
    if primary_id is not None:
        object_ids.add(primary_id)

    result = [str(oid) for oid in object_ids if oid is not None]
    logger.info(f"Extracted object IDs from incident: {result}")
    return result


def _build_authoritative_incident_facts(
    alert_metadata: dict[str, Any],
    alert_sensor_id: str,
    alert_from_timestamp: str,
    alert_to_timestamp: str,
) -> dict[str, Any]:
    """Select incident fields that must not be inferred or rewritten by the report LLM."""
    place = alert_metadata.get("place")
    incident_id = alert_metadata.get("Id") or alert_metadata.get("id")
    facts: dict[str, Any] = {
        "Id": incident_id,
        "category": alert_metadata.get("category"),
        "timestamp": alert_metadata.get("timestamp") or alert_from_timestamp,
        "end": alert_metadata.get("end") or alert_to_timestamp,
        "sensorId": alert_metadata.get("sensorId") or alert_sensor_id,
        "objectIds": alert_metadata.get("objectIds"),
        "primaryObjectId": _extract_primary_object_id(alert_metadata),
        "place.name": place.get("name") if isinstance(place, dict) else None,
    }
    # Optional counts only when a caller already set them. The report path does not
    # attach people_count or vehicle_count, and a failed behavior lookup must not
    # become an authoritative 0. Never derive either count from len(objectIds).
    for count_key in ("people_count", "vehicle_count"):
        if count_key in alert_metadata and alert_metadata[count_key] is not None:
            facts[count_key] = alert_metadata[count_key]
    return {key: value for key, value in facts.items() if value is not None}


def _normalize_report_model_output(content: str) -> str:
    """Drop closed thinking, then keep every complete fenced block."""
    normalized = content.strip()
    if think_match := _THINK_CLOSE_RE.search(normalized):
        normalized = normalized[think_match.end() :].strip()
    fenced_blocks = [match.group(1).strip() for match in _FENCED_BLOCK_RE.finditer(normalized)]
    fenced_blocks = [block for block in fenced_blocks if block]
    if fenced_blocks:
        return "\n\n".join(fenced_blocks)
    # A lone opening fence (with an optional info string) or a leftover closing fence.
    normalized = re.sub(r"^```[\w-]*[ \t]*\n?", "", normalized)
    normalized = re.sub(r"\n?```\s*$", "", normalized)
    return normalized.strip()


def _body_without_resources(content: str) -> str:
    """Return report body with any Resources section removed."""
    match = _RESOURCES_HEADING_RE.search(content)
    if not match:
        return content.strip()
    return content[: match.start()].strip()


def _parse_markdown_table_fields(content: str) -> dict[str, str]:
    """
    Parse Field/Value markdown table rows from arbitrary report content.

    Supports rows that omit a trailing pipe, e.g. ``| **Sensor ID** | Camera_01``.
    Uses ``lstrip`` only so blank values made of trailing spaces are preserved.
    """
    lines = content.splitlines()
    header_line_indexes = {
        index
        for index, line in enumerate(lines[:-1])
        if line.lstrip().startswith("|") and _TABLE_SEPARATOR_RE.match(lines[index + 1].lstrip().rstrip())
    }
    fields: dict[str, str] = {}
    for index, line in enumerate(lines):
        if index in header_line_indexes:
            continue
        stripped = line.lstrip()
        if not stripped.startswith("|") or _TABLE_SEPARATOR_RE.match(stripped.rstrip()):
            continue
        # Only strip a trailing delimiter pipe when the row has label + value + trailing "|".
        # Two-pipe rows like `| **Label** |    ` must keep the blank value cell.
        inner = stripped[1:]
        if stripped.count("|") >= 3 and inner.rstrip().endswith("|"):
            inner = inner.rstrip()[:-1]
        cells = [cell.strip() for cell in inner.split("|")]
        if len(cells) < 2:
            continue
        label = _normalize_report_label(cells[0])
        if not label or label in {"field", "value"}:
            continue
        fields.setdefault(label, cells[1].strip())
    return fields


def _normalize_report_label(label: str) -> str:
    """Compare labels ignoring markdown emphasis, bullets, trailing colons, and case."""
    cleaned = re.sub(r"[*_`]", "", label)
    cleaned = re.sub(r"^[ \t]*-[ \t]*", "", cleaned)
    cleaned = cleaned.strip().rstrip(":").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.casefold()


def _normalize_section_title(title: str) -> str:
    """Section titles use the label rules, plus a trailing parenthetical such as ``(1)``."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", _normalize_report_label(title)).strip()


def _extract_labeled_field_values(content: str) -> dict[str, str]:
    """Collect labeled values from markdown tables and one-line ``**Label**`` prose."""
    fields = _parse_markdown_table_fields(content)
    for match in _PROSE_LABELED_VALUE_RE.finditer(content):
        label = _normalize_report_label(match.group(1))
        value = match.group(2).strip()
        if label and label not in fields:
            fields[label] = value
    return fields


def _field_value_is_resolved(value: str) -> tuple[bool, str | None]:
    """Return (ok, failure_reason_suffix) for a single field value."""
    if _UNFILLED_PLACEHOLDER_RE.search(value):
        return False, "unresolved_placeholder"
    cleaned = _BLANK_RENDERED_VALUE_RE.sub(" ", value)
    # "-", "<br>", and "&nbsp;" render as a blank PDF cell.
    if re.fullmatch(r"[\s\-]*", cleaned):
        return False, "empty_required_field"
    # Explicit Unknown/N/A is allowed for this individual field only.
    return True, None


def _validate_required_report_fields(body: str, required_fields: list[str], fail: Any) -> None:
    """Enforce only explicitly configured field labels; Unknown/N/A is per-field."""
    if not required_fields:
        return
    fields = _extract_labeled_field_values(body)
    for label in required_fields:
        configured = label.strip()
        key = _normalize_report_label(configured)
        if not key:
            continue
        if key not in fields:
            fail(f"missing_required_field:{configured}")
            continue
        ok, reason = _field_value_is_resolved(fields[key])
        if not ok:
            fail(f"{reason}:{configured}")


def _clean_heading_title(raw: str) -> str:
    return _TRAILING_HEADING_HASHES_RE.sub("", raw).strip()


def _iter_headings(content: str) -> list[tuple[int, int, int, str]]:
    """Return (start, end, level, title) for ATX and setext headings, in order."""
    headings: list[tuple[int, int, int, str]] = []
    for match in _ATX_HEADING_RE.finditer(content):
        title = _clean_heading_title(match.group(2))
        if title:
            headings.append((match.start(), match.end(), len(match.group(1)), title))
    occupied = {start for start, _end, _level, _title in headings}
    for match in _SETEXT_HEADING_RE.finditer(content):
        if match.start() in occupied:
            continue
        title = match.group("title").strip()
        if not title:
            continue
        level = 1 if match.group("underline") == "=" else 2
        headings.append((match.start(), match.end(), level, title))
    headings.sort(key=lambda item: item[0])
    return headings


def _markdown_sections(content: str) -> dict[str, str]:
    """Map normalized heading titles to the body that stays inside that heading.

    Any following heading of the same or higher level closes the section, so an
    empty required section cannot absorb the next sibling's text or N/A. A
    lower-level heading, such as ``### Person 1`` under ``## People Involved``,
    stays inside.
    """
    headings = _iter_headings(content)
    sections: dict[str, str] = {}
    for index, (_start, end, level, title) in enumerate(headings):
        key = _normalize_section_title(title)
        if not key:
            continue
        body_end = len(content)
        for later_start, _later_end, later_level, _later_title in headings[index + 1 :]:
            if later_level <= level:
                body_end = later_start
                break
        sections.setdefault(key, content[end:body_end])
    return sections


def _section_contains_table(section_body: str) -> bool:
    return any(line.lstrip().startswith("|") for line in section_body.splitlines())


def _section_body_is_supported(section_body: str) -> tuple[bool, str | None]:
    """A required section needs resolved content or an explicit Unknown/N/A in that section."""
    if _UNFILLED_PLACEHOLDER_RE.search(section_body):
        return False, "unresolved_placeholder"
    if _section_contains_table(section_body):
        # Trailing prose must not fill a blank table. Header rows are not values.
        if _SECTION_UNKNOWN_RE.search(section_body):
            return True, None
        values = _parse_markdown_table_fields(section_body).values()
        if any(_field_value_is_resolved(value)[0] for value in values):
            return True, None
        return False, "empty_required_section"
    if _SECTION_UNKNOWN_RE.search(section_body):
        return True, None
    cleaned = re.sub(r"(?m)^#{1,6}[ \t]*.*$", " ", section_body)
    cleaned = re.sub(r"(?m)^\|.*$", " ", cleaned)
    cleaned = re.sub(r"[|*_`>#-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False, "empty_required_section"
    return True, None


def _validate_required_report_sections(body: str, required_sections: list[str], fail: Any) -> None:
    """Enforce only explicitly configured section headings."""
    if not required_sections:
        return
    configured = [title.strip() for title in required_sections if title.strip()]
    sections = _markdown_sections(body)
    for title in configured:
        key = _normalize_section_title(title)
        if key not in sections:
            fail(f"missing_required_section:{title}")
            continue
        ok, reason = _section_body_is_supported(sections[key])
        if not ok:
            fail(f"{reason}:{title}")


def _validate_report_body(
    content: str,
    *,
    required_report_fields: list[str] | None = None,
    required_report_sections: list[str] | None = None,
    response_len: int | None = None,
) -> None:
    """
    Validate normalized report Markdown before Resources append or artifact save.

    Always rejects empty, whitespace-only, thinking-only, residual/unclosed thinking,
    resources-only bodies, and unfilled ``{identifier}`` placeholders. Optional
    ``required_report_fields`` and ``required_report_sections`` enforce only explicitly
    configured labels and headings.
    Template titles are not assumed to make every heading mandatory. Structural
    completeness is not factual accuracy.
    """
    body_len = len(content)
    effective_response_len = len(content) if response_len is None else response_len

    def _fail(reason: str) -> None:
        raise ReportContentValidationError(
            reason,
            response_len=effective_response_len,
            body_len=body_len,
        )

    if not content or not content.strip():
        _fail("empty_or_whitespace_body")

    if _THINK_OPEN_RE.search(content) or _THINK_CLOSE_RE.search(content):
        _fail("residual_thinking_markup")

    body = _body_without_resources(content)
    if not body:
        _fail("resources_only_body")

    # A raw {identifier} is never valid output. Prose braces with spaces do not match.
    if _UNFILLED_PLACEHOLDER_RE.search(body):
        _fail("unresolved_placeholder")

    _validate_required_report_fields(body, required_report_fields or [], _fail)
    _validate_required_report_sections(body, required_report_sections or [], _fail)


def _append_resources_section(content: str, image_url: str | None, video_url: str | None) -> str:
    """Append media Resources after a validated report body."""
    if not (image_url or video_url):
        return content

    updated = content
    updated += "\n\n##Resources\n\n"
    if image_url:
        updated += f"**Incident Snapshot:** ![Incident Snapshot]({image_url})\n\n"
    if video_url:
        # FIX: URL is placed in its own paragraph (\n\n) so text-align:justify
        # does not stretch the space between the label and URL in the PDF.
        updated += f"**Incident Video:**\n\n{video_url}\n\n"
    return updated


async def _run_vlm_analysis(
    report_input: TemplateReportGenInput,
    vlm_tool: Any,
    config: TemplateReportGenConfig,
    object_ids: list[str] | None = None,
) -> list[str]:
    """Run VLM analysis tasks on the video."""
    vlm_tasks = []
    for vlm_prompt in config.vlm_prompts:
        logger.info(f"Running VLM task for prompt: {vlm_prompt}")

        # Format prompt with object_ids if the placeholder exists.
        enhanced_prompt = vlm_prompt
        if "{object_ids}" in vlm_prompt:
            object_ids_str = ", ".join(object_ids) if object_ids else "none provided"
            enhanced_prompt = vlm_prompt.replace("{object_ids}", object_ids_str)
        enhanced_prompt = f"{enhanced_prompt}\n\n{_REPORT_VLM_GROUNDING_INSTRUCTION}"

        vlm_input: dict[str, Any] = {
            "sensor_id": report_input.alert_sensor_id,
            "user_prompt": enhanced_prompt,
            "start_timestamp": report_input.alert_from_timestamp,
            "end_timestamp": report_input.alert_to_timestamp,
        }

        # Add object_ids for video overlay (always pass if available)
        if object_ids:
            vlm_input["object_ids"] = object_ids

        # Add vlm_reasoning if specified
        if report_input.vlm_reasoning is not None:
            vlm_input["vlm_reasoning"] = report_input.vlm_reasoning

        vlm_tasks.append(vlm_tool.ainvoke(input=vlm_input))

    vlm_results = await asyncio.gather(*vlm_tasks)
    logger.debug(f"VLM results: {vlm_results}")
    return vlm_results


async def _fetch_media_urls_for_report(
    report_input: TemplateReportGenInput,
    picture_url_tool: Any,
    video_url_tool: Any | None,
    config: TemplateReportGenConfig,
    object_ids: list[str] | None = None,
) -> tuple[str, str | None]:
    """Fetch picture and video URLs for the report."""
    picture_url_results = await picture_url_tool.ainvoke(
        input={
            "sensor_id": report_input.alert_sensor_id,
            "start_time": report_input.alert_from_timestamp,
        }
    )
    logger.info(f"Picture URL results: {picture_url_results.image_url}")

    # Determine video URL based on the tool being used
    video_url = None
    if "s3" in config.picture_url_tool:
        video_url = picture_url_results.video_url
        logger.info(f"Video URL from S3: {video_url}")
    elif video_url_tool is not None:
        logger.info(f"Using video URL tool to get video URL: {config.video_url_tool}")
        extended_end_time = extend_timestamp(report_input.alert_from_timestamp, report_input.alert_to_timestamp)
        video_url_input: dict[str, Any] = {
            "sensor_id": report_input.alert_sensor_id,
            "start_time": report_input.alert_from_timestamp,
            "end_time": extended_end_time,
        }

        # Add object_ids if provided
        if object_ids:
            video_url_input["object_ids"] = object_ids
            logger.info(f"Passing object IDs to video URL tool: {object_ids}")

        video_url_results = await video_url_tool.ainvoke(input=video_url_input)
        video_url = video_url_results.video_url
        logger.info(f"Video URL from VST: {video_url}")

    return picture_url_results.image_url, video_url


async def _save_markdown_to_object_store(
    markdown_content: str,
    filename: str,
    object_store: Any,
    config: TemplateReportGenConfig,
    sensor_id: str = "",
) -> tuple[str, int]:
    """Save markdown content to object store."""
    content_bytes = markdown_content.encode("utf-8")
    file_size = len(content_bytes)

    timestamp = datetime.now()
    metadata = {
        "timestamp": timestamp.strftime("%Y%m%d_%H%M%S"),
        "generated_at": timestamp.isoformat(),
        "file_size": str(file_size),
        "content_type": "text/markdown",
    }

    # Include sensor_id prefix in object store key if provided
    object_store_key = f"{sensor_id}/{filename}" if sensor_id else filename

    object_store_item = ObjectStoreItem(data=content_bytes, content_type="text/markdown", metadata=metadata)
    await object_store.upsert_object(object_store_key, object_store_item)
    logger.info(f"Markdown report saved to object store: {object_store_key}")

    # Get HTTP URL using universal method
    http_url = _get_object_store_url(object_store, object_store_key, config)

    return http_url, file_size


async def _save_pdf_to_object_store(
    markdown_content: str,
    filename: str,
    pdf_filename: str,
    object_store: Any,
    config: TemplateReportGenConfig,
    sensor_id: str = "",
) -> tuple[str, int]:
    """Generate PDF from markdown and save to object store. Returns URL and size."""
    pdf_file_size = 0
    pdf_url = ""

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_md_path = os.path.join(temp_dir, filename)
        temp_pdf_path = os.path.join(temp_dir, pdf_filename)

        # Replace public URLs with private IPs for image URLs before PDF generation
        pdf_markdown_content = _replace_public_urls_with_private(
            markdown_content, config.vst_internal_url, config.vst_external_url
        )

        # Log the complete markdown content before saving to temp file
        logger.debug("=" * 80)
        logger.debug("MARKDOWN CONTENT BEFORE PDF GENERATION (with internal IPs)")
        logger.debug("=" * 80)
        logger.debug(pdf_markdown_content)
        logger.debug("=" * 80)
        logger.debug("END OF MARKDOWN CONTENT")
        logger.debug("=" * 80)

        # Write markdown to temp file and convert to PDF
        with open(temp_md_path, "w", encoding="utf-8") as f:
            f.write(pdf_markdown_content)

        if _convert_markdown_to_pdf(temp_md_path, temp_pdf_path):
            with open(temp_pdf_path, "rb") as f:
                pdf_bytes = f.read()
            pdf_file_size = len(pdf_bytes)

            timestamp = datetime.now()
            pdf_object_store_item = ObjectStoreItem(
                data=pdf_bytes,
                content_type="application/pdf",
                metadata={
                    "timestamp": timestamp.strftime("%Y%m%d_%H%M%S"),
                    "generated_at": timestamp.isoformat(),
                    "file_size": str(pdf_file_size),
                    "content_type": "application/pdf",
                },
            )

            # Include sensor_id prefix in object store key if provided
            pdf_object_store_key = f"{sensor_id}/{pdf_filename}" if sensor_id else pdf_filename
            await object_store.upsert_object(pdf_object_store_key, pdf_object_store_item)

            # Get HTTP URL using universal method
            pdf_url = _get_object_store_url(object_store, pdf_object_store_key, config)

            logger.info(f"PDF report saved to object store: {pdf_object_store_key}")
        else:
            logger.warning("Failed to generate PDF report")

    return pdf_url, pdf_file_size


async def _fetch_behavior_data(
    behavior_tool: Any,
    sensor_id: str,
    from_timestamp: str,
    to_timestamp: str,
) -> dict[str, Any]:
    """Fetch behavior data for people and vehicles.

    Args:
        behavior_tool: The behavior MCP tool
        sensor_id: Sensor ID to query
        from_timestamp: Start timestamp in ISO format
        to_timestamp: End timestamp in ISO format

    Returns:
        Dictionary with 'people_count', 'vehicle_count', and 'cv_metadata'. Counts are
        None when the lookup fails so an outage is not reported as zero people or vehicles.
    """
    try:
        logger.info("Fetching behavior data")
        behavior_results = await behavior_tool.ainvoke(
            input={
                "sensorId": sensor_id,
                "place": "",
                "objectId": "",
                "objectType": "",
                "fromTimestamp": from_timestamp,
                "toTimestamp": to_timestamp,
                "queryString": "",
            }
        )
        logger.debug(f"Behavior results received: {behavior_results}")

        if isinstance(behavior_results, str):
            behavior_data = json.loads(behavior_results)
        else:
            behavior_data = behavior_results

        # Count unique objects by type
        people_ids = set()
        vehicle_ids = set()

        if behavior_data.get("behaviors"):
            for behavior in behavior_data["behaviors"]:
                if behavior.get("object"):
                    obj = behavior["object"]
                    obj_id = obj.get("id")
                    obj_type = obj.get("type", "Unknown")

                    if obj_id:
                        if obj_type.lower() == "person":
                            people_ids.add(obj_id)
                        else:
                            # Everything non-person is considered a vehicle
                            vehicle_ids.add(obj_id)

        people_count = len(people_ids)
        vehicle_count = len(vehicle_ids)

        # Convert the entire API response to a formatted JSON string for the VLM
        cv_metadata_str = json.dumps(behavior_data, indent=2)

        logger.info(f"Counted {people_count} people and {vehicle_count} vehicles")
        return {
            "people_count": people_count,
            "vehicle_count": vehicle_count,
            "cv_metadata": cv_metadata_str,
        }

    except Exception as e:
        logger.warning(f"Failed to fetch behavior data: {e}")
        return {
            "people_count": None,
            "vehicle_count": None,
            "cv_metadata": "No CV metadata available",
        }


async def _fetch_proximity_threshold(
    frames_enhanced_tool: Any,
    sensor_id: str,
    from_timestamp: str,
    to_timestamp: str,
) -> float | None:
    """Fetch proximity detection threshold from enhanced frame analytics.

    Args:
        frames_enhanced_tool: The frames enhanced MCP tool
        sensor_id: Sensor ID to query
        from_timestamp: Start timestamp in ISO format
        to_timestamp: End timestamp in ISO format

    Returns:
        Proximity threshold value (in meters) if found, None otherwise
    """
    try:
        logger.info("Fetching enhanced frame data for proximity threshold")
        frames_enhanced_results = await frames_enhanced_tool.ainvoke(
            input={
                "sensorId": sensor_id,
                "fromTimestamp": from_timestamp,
                "toTimestamp": to_timestamp,
                "maxResultSize": 25,
            }
        )
        logger.info(f"Frames enhanced results: {frames_enhanced_results}")
        if isinstance(frames_enhanced_results, str):
            frames_data = json.loads(frames_enhanced_results)
        else:
            frames_data = frames_enhanced_results
        if frames_data.get("enhancedFrames"):
            for frame in frames_data["enhancedFrames"]:
                if frame.get("socialDistancing"):
                    proximity_threshold = frame["socialDistancing"].get("threshold")
                    if proximity_threshold is not None:
                        logger.info(f"Extracted proximity threshold: {proximity_threshold}")
                        return float(proximity_threshold)

        logger.warning("No proximity threshold found in enhanced frames")
        return None

    except Exception as e:
        logger.warning(f"Failed to fetch proximity data from frames_enhanced: {e}")
        return None


async def _format_custom_report(
    vlm_results: list[str],
    alert_metadata: dict[str, Any],
    alert_sensor_id: str,
    alert_from_timestamp: str,
    alert_to_timestamp: str,
    template_path: str,
    template_name: str,
    report_prompt: str,
    llm: Any,
    image_url: str | None = None,
    video_url: str | None = None,
    agent_version: str = "v1.0.0",
    llm_reasoning: bool | None = None,
    required_report_fields: list[str] | None = None,
    required_report_sections: list[str] | None = None,
) -> str:
    """Format custom report using LLM to extract information from messages and populate template."""
    template_content = _load_custom_template(template_path, template_name)

    # Substitute the template into the report_prompt, but escape template placeholders
    # so they don't get treated as prompt variables
    escaped_template = template_content.replace("{", "{{").replace("}", "}}")
    formatted_system_prompt = report_prompt.format(template=escaped_template, agent_version=agent_version)
    formatted_system_prompt = f"{formatted_system_prompt}\n\n{_REPORT_LLM_GROUNDING_INSTRUCTION}"
    authoritative_incident_facts = _build_authoritative_incident_facts(
        alert_metadata,
        alert_sensor_id,
        alert_from_timestamp,
        alert_to_timestamp,
    )

    # Append thinking tag to system prompt if applicable
    thinking_tag = get_thinking_tag(llm, llm_reasoning)
    if thinking_tag:
        formatted_system_prompt = f"{formatted_system_prompt}\n{thinking_tag}"

    prompt_template = ChatPromptTemplate.from_messages(
        [
            ("system", formatted_system_prompt),
            (
                "user",
                "Authoritative incident facts (copy these values exactly):\n\n"
                "{authoritative_incident_facts}\n\n"
                "Video understanding results (use only for observations directly supported by "
                "visible or audible evidence):\n\n"
                "{vlm_results}\n\n"
                "Full alert metadata:\n\n{alert_metadata}",
            ),
        ],
    )

    # Bind LLM with reasoning kwargs if applicable
    llm_kwargs = get_llm_reasoning_bind_kwargs(llm, llm_reasoning)
    bound_llm = llm.bind(**llm_kwargs) if llm_kwargs else llm

    chain = prompt_template | bound_llm
    try:
        response = await chain.ainvoke(
            {
                "vlm_results": vlm_results,
                "alert_metadata": alert_metadata,
                "authoritative_incident_facts": json.dumps(
                    authoritative_incident_facts,
                    sort_keys=True,
                    ensure_ascii=False,
                ),
            }
        )
    except Exception as e:
        logger.error("LLM report generation failed: %s", type(e).__name__)
        raise ValueError(f"Failed to generate custom report with LLM: {e}") from e

    raw_content = "" if response.content is None else str(response.content)
    content = _normalize_report_model_output(raw_content)

    try:
        _validate_report_body(
            content,
            required_report_fields=required_report_fields,
            required_report_sections=required_report_sections,
            response_len=len(raw_content),
        )
    except ReportContentValidationError as e:
        logger.warning(
            "Report body validation failed: reason=%s response_len=%d body_len=%d",
            e.reason,
            e.response_len,
            e.body_len,
        )
        raise

    return _append_resources_section(content, image_url, video_url)


@register_function(config_type=TemplateReportGenConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def template_report_gen(config: TemplateReportGenConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Tool for generating a report using a template, saving it to an object store, and providing HTTP URLs for easy access."""

    # Get the object store client
    object_store = await builder.get_object_store_client(config.object_store)
    vlm_tool = await builder.get_tool(config.video_understanding_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    llm = await builder.get_llm(config.llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    picture_url_tool = await builder.get_tool(config.picture_url_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
    video_url_tool = None
    if "s3" not in config.picture_url_tool and config.video_url_tool is not None:
        video_url_tool = await builder.get_tool(config.video_url_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    geolocation_tool = None
    if config.geolocation_tool:
        geolocation_tool = await builder.get_tool(config.geolocation_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    async def _template_report_gen(report_input: TemplateReportGenInput) -> TemplateReportGenOutput:
        """
        This tool generates a report using a template, saves it to an object store,
        and provides HTTP URLs for easy access. It can also optionally save local copies.
        """

        if not config.llm_name:
            raise ValueError("llm_name must be configured when template_type='custom'")

        logger.info(f"Input: {report_input}")

        # Extract object IDs from incident metadata
        object_ids = _extract_object_ids_from_incident(report_input.alert_metadata)

        # Fetch geolocation data
        geolocation_data = await _fetch_geolocation_data(report_input, geolocation_tool)
        report_input.alert_metadata["geolocation"] = geolocation_data

        # Run VLM analysis on video
        try:
            vlm_results = await _run_vlm_analysis(report_input, vlm_tool, config, object_ids)
        except Exception as e:
            raise ValueError(f"Failed to run VLM analysis: {e}") from e

        # Fetch picture and video URLs
        image_url, video_url = await _fetch_media_urls_for_report(
            report_input, picture_url_tool, video_url_tool, config, object_ids
        )

        # Format the report using LLM
        if not config.template_path or not config.template_name:
            raise ValueError("template_path and template_name are required for template report generation")
        markdown_content = await _format_custom_report(
            vlm_results=vlm_results,
            alert_metadata=report_input.alert_metadata,
            alert_sensor_id=report_input.alert_sensor_id,
            alert_from_timestamp=report_input.alert_from_timestamp,
            alert_to_timestamp=report_input.alert_to_timestamp,
            template_path=config.template_path,
            template_name=config.template_name,
            report_prompt=config.report_prompt,
            llm=llm,
            image_url=image_url,
            video_url=video_url,
            agent_version=config.agent_version,
            llm_reasoning=report_input.llm_reasoning,
            required_report_fields=config.required_report_fields,
            required_report_sections=config.required_report_sections,
        )

        # Generate filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"agent_report_{timestamp}.md"
        pdf_filename = filename.replace(".md", ".pdf")

        # Extract sensor_id for object store path prefix
        sensor_id = report_input.alert_sensor_id.lower() if config.use_sensor_id_prefix_for_object_store_path else ""

        # Save markdown to object store
        http_url, file_size = await _save_markdown_to_object_store(
            markdown_content, filename, object_store, config, sensor_id
        )

        # Generate and save PDF to object store
        pdf_url, pdf_file_size = await _save_pdf_to_object_store(
            markdown_content, filename, pdf_filename, object_store, config, sensor_id
        )

        # Save local copies
        local_md_path = ""
        local_pdf_path = ""
        if config.save_local_copy:
            # Create sensor-specific directory
            local_dir = os.path.join(config.output_dir, sensor_id)
            Path(local_dir).mkdir(parents=True, exist_ok=True)

            # Save markdown file locally
            local_md_path = os.path.join(local_dir, filename)
            with open(local_md_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)
            logger.info(f"Local markdown report saved to: {local_md_path}")

            # Save PDF file locally if it was generated
            if pdf_url and pdf_file_size > 0:
                local_pdf_path = os.path.join(local_dir, pdf_filename)
                if _convert_markdown_to_pdf(local_md_path, local_pdf_path):
                    logger.info(f"Local PDF report saved to: {local_pdf_path}")
                else:
                    logger.warning("Failed to save local PDF copy")

        # Create summary
        logger.info(f"Report saved to object store and available at: {http_url}")
        if pdf_url:
            logger.info(f"PDF report available at: {pdf_url}")
        summary = f"Report saved successfully. \nMarkdown: {http_url}" + (f"\nPDF: {pdf_url}" if pdf_url else "")

        return TemplateReportGenOutput(
            http_url=http_url,
            pdf_url=pdf_url,
            object_store_key=filename,
            summary=summary,
            file_size=file_size,
            pdf_file_size=pdf_file_size,
            content=markdown_content,
            image_url=image_url,
            video_url=video_url,
        )

    function_info = FunctionInfo.create(
        single_fn=_template_report_gen,
        description=_template_report_gen.__doc__,
        input_schema=TemplateReportGenInput,
        single_output_schema=TemplateReportGenOutput,
    )

    yield function_info
