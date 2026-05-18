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

"""
Report Generation Tool for uploaded videos.

Generates reports for uploaded videos without Video Analytics MCP infrastructure.
Handles VLM prompt sanitization, video analysis, and report formatting.
"""

import asyncio
from collections import OrderedDict
from collections.abc import AsyncGenerator
from datetime import datetime
from datetime import timedelta
import json
import logging
import os
import re
import tempfile
from typing import Any
from typing import NamedTuple
import urllib.parse

try:
    import markdown
    from xhtml2pdf import pisa

    PDF_CONVERSION_AVAILABLE = True
except ImportError:
    PDF_CONVERSION_AVAILABLE = False


from nat.builder.builder import Builder
from nat.builder.context import Context
from nat.builder.context import ContextState
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.component_ref import FunctionRef
from nat.data_models.component_ref import ObjectStoreRef
from nat.data_models.function import FunctionBaseConfig
from nat.data_models.interactive import HumanPromptText
from nat.data_models.interactive import InteractionResponse
from nat.object_store.models import ObjectStoreItem
from pydantic import BaseModel
from pydantic import Field

from vss_agents.tools.lvs_video_understanding import LVSStatus
from vss_agents.tools.vst.timeline import get_timeline
from vss_agents.tools.vst.utils import get_stream_id
from vss_agents.tools.vst.video_clip import get_video_url
from vss_agents.utils.reasoning_parsing import parse_reasoning_content
from vss_agents.utils.time_convert import datetime_to_iso8601
from vss_agents.utils.time_convert import iso8601_to_datetime

logger = logging.getLogger(__name__)


CHUNK_TIMESTAMP_PROMPT = """
    All events from the video should fall within the time range:
    START_TIME: {start_time}s
    END_TIME: {end_time}s
"""


def _get_object_store_url(object_store: Any, filename: str, config: "VideoReportGenConfig") -> str:
    """
    Get HTTP URL for a file from any object store type.

    Supports:
    - S3/MinIO object store (construct URL from endpoint)
    - in_memory and other stores (use NAT file server /static/ endpoint)

    Args:
        object_store: The object store instance
        filename: The file key/name
        config: The Video report gen config

    Returns:
        str: HTTP URL to access the file
    """
    # S3/MinIO object store - construct URL from attributes
    if hasattr(object_store, "endpoint_url") and hasattr(object_store, "bucket_name"):
        endpoint = object_store.endpoint_url
        bucket = object_store.bucket_name
        endpoint = endpoint.rstrip("/")
        return f"{endpoint}/{bucket}/{filename}"

    # For in_memory and other stores - use NAT's /static/ endpoint from config
    base_url = config.base_url.rstrip("/")
    return f"{base_url}/{filename}"


def _divide_video_into_chunks(
    duration_seconds: float,
    chunk_duration_seconds: int = 60,
) -> list[tuple[float, float]]:
    """
    Divide a video timeframe into chunks.

    Args:
        duration_seconds: Duration of the video in seconds
        chunk_duration_seconds: Duration of each chunk in seconds

    Returns:
        List of (chunk_start, chunk_end) tuples in seconds(offset from the start of the video)
    """
    if chunk_duration_seconds <= 0:
        raise ValueError(
            f"Video Analysis Report: chunk_duration_seconds must be positive, got {chunk_duration_seconds}"
        )

    chunks: list[tuple[float, float]] = []
    current_start: float = 0.0

    while current_start < duration_seconds:
        current_end = min(current_start + chunk_duration_seconds, duration_seconds)
        chunks.append(
            (
                current_start,
                current_end,
            )
        )
        current_start = current_end

    return chunks


def _remove_som_markers(prompt: str) -> str:
    """
    Remove Set-of-Mark (SOM) markers from VLM prompts.

    SOM markers are used in Video Analytics MCP mode to reference specific tracked objects,
    but are not applicable for Video(uploaded) Report mode where videos lack object tracking data.

    Removes:
    - {object_ids} placeholder
    - Sentences mentioning "object ids" or "object IDs"
    - Any remaining object ID references

    Args:
        prompt: The original VLM prompt

    Returns:
        Cleaned prompt without SOM markers
    """
    # Remove {object_ids} placeholder
    cleaned = re.sub(r"\{object_ids\}", "", prompt)

    # Remove sentences mentioning object IDs
    cleaned = re.sub(
        r"Focus only on.*?object ids[^.]*\.\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"Include only.*?object ids[^.]*\.\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Clean up any extra whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


def _replace_public_urls_with_private(
    markdown_content: str, vst_internal_url: str | None, vst_external_url: str | None
) -> str:
    """
    Replace external (public) URLs in image tags with internal (private) IP URLs for PDF generation.

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

    logger.info(f"Replacing external URL '{external_base}' with internal URL '{internal_base}' in image URLs for PDF")

    # Replace URLs in image tags only (both <img src="..." and ![alt](url) formats)
    # Pattern 1: <img src="URL" ...>
    def replace_img_src(match: re.Match[str]) -> str:
        full_match = match.group(0)
        url = match.group(1)

        # Replace external base with internal base if found
        if external_base in url:
            new_url = url.replace(external_base, internal_base)
            return full_match.replace(url, new_url)

        return full_match

    # Replace in <img src="..." tags
    result = re.sub(r'<img\s+src="([^"]+)"', replace_img_src, markdown_content)

    # Pattern 2: ![alt](URL) - markdown image syntax
    def replace_md_img(match: re.Match[str]) -> str:
        full_match = match.group(0)
        url = match.group(2)

        # Replace external base with internal base if found
        if external_base in url:
            new_url = url.replace(external_base, internal_base)
            return full_match.replace(url, new_url)

        return full_match

    # Replace in ![alt](url) format
    result = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_md_img, result)

    return result


def _convert_markdown_to_pdf(markdown_file_path: str, output_pdf_path: str) -> bool:
    """Convert markdown file to PDF using Python packages."""
    if not PDF_CONVERSION_AVAILABLE:
        logger.warning(
            "Video Analysis Report: PDF conversion not available. Install 'markdown' and 'xhtml2pdf' packages."
        )
        return False

    try:
        # Read markdown file
        with open(markdown_file_path, encoding="utf-8") as f:
            markdown_content = f.read()

        # Convert markdown to HTML
        html_content = markdown.markdown(markdown_content, extensions=["tables", "fenced_code"])

        # Add professional CSS styling with NVIDIA branding
        styled_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                * {{ box-sizing: border-box; }}
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
                p {{ margin: 0.6em 0; text-align: justify; }}
                /* FIX: Long URLs in <a> tags could not wrap, causing xhtml2pdf
                   to stretch inter-word spaces on justified lines (e.g. the
                   "Video Playback:" label and URL). word-break/overflow-wrap
                   allow URLs to break across lines for proper PDF layout. */
                a {{
                    word-break: break-all;
                    overflow-wrap: break-word;
                }}
                ul {{
                    margin: 0.5em 0;
                    padding-left: 1.5em;
                }}
                li {{
                    margin: 0.3em 0;
                    padding: 0;
                }}
                img {{
                    max-width: 400px;
                    height: auto;
                    display: block;
                    margin: 0.5em auto;
                    border-radius: 2px;
                    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """

        # Convert HTML to PDF
        with open(output_pdf_path, "wb") as pdf_file:
            pisa_status = pisa.CreatePDF(styled_html, dest=pdf_file)

        if pisa_status.err:
            logger.error(f"Video Analysis Report: PDF conversion had errors: {pisa_status.err}")
            return False

        logger.info(f"Successfully converted markdown to PDF: {output_pdf_path}")
        return True

    except Exception as e:
        logger.error(f"Video Analysis Report: Error converting markdown to PDF: {e}")
        return False


class VideoReportGenConfig(FunctionBaseConfig, name="video_report_gen"):
    """Configuration for Video(uploaded) Report generation tool."""

    object_store: ObjectStoreRef = Field(
        ...,
        description="Reference to the object store for serving files via HTTP",
    )

    base_url: str = Field(
        default="http://localhost:8000/static",
        description="Base URL for file server (used for in_memory and other non-S3 object stores)",
    )

    video_understanding_tool: FunctionRef = Field(
        ...,
        description="Name of the video understanding tool to use for short videos",
    )

    lvs_video_understanding_tool: str | None = Field(
        default=None,
        description="Name of the LVS video understanding tool to use for long videos. If None, LVS is disabled.",
    )

    lvs_video_length: int = Field(
        default=60,
        description="Minimum length of a video in seconds to use LVS for analysis. If the video duration is longer than this value, LVS will be used for analysis.",
    )
    vlm_prompt: str = Field(
        default="Describe in detail what is happening in this video, including all visible people, objects, actions, and environmental conditions.",
        description="Prompt to query the VLM for video understanding. SOM markers will be automatically removed.",
    )
    normalize_timestamps: bool = Field(
        default=True,
        description="Normalize timestamps in the VLM response content to absolute video time, set to true for CR1",
    )
    chunk_duration_seconds: int = Field(
        default=60,
        description="Duration of each video chunk in seconds for parallel processing.",
    )
    max_duration_for_chunking: int = Field(
        default=300,
        description="Maximum duration of a video in seconds for chunking.",
    )

    video_url_tool: FunctionRef | None = Field(
        default=None,
        description="Tool to get video playback URL by sensor ID (optional)",
    )

    picture_url_tool: FunctionRef | None = Field(
        default=None,
        description="Tool to get snapshot picture URL by sensor ID and timestamp (optional)",
    )

    vst_internal_url: str | None = Field(
        default=None,
        description="Internal VST URL for API calls (e.g., 'http://${INTERNAL_IP}:30888'). If not provided, uses VST_INTERNAL_URL env var.",
    )

    vst_external_url: str | None = Field(
        default=None,
        description="External VST URL for client-facing URLs (e.g., 'http://${EXTERNAL_IP}:30888'). If not provided, uses VST_EXTERNAL_URL env var.",
    )

    disable_audio: bool = Field(
        default=True,
        description="When True, injected VST clip URLs use disableAudio=true (audio stripped). "
        "Set False for audio-capable VLMs (e.g. Nemotron Omni); align with vst.video_clip.disable_audio.",
    )

    # HITL Configuration (optional - if not set, HITL is disabled)
    hitl_enabled: bool = Field(
        default=False,
        description="Enable HITL for VLM prompt confirmation before report generation.",
    )

    hitl_vlm_prompt_template: str | None = Field(
        default=None,
        description="HITL template for collecting/confirming VLM prompt from user. If None and hitl_enabled=True, uses a default template.",
    )

    hitl_prompt_llm: str | None = Field(
        default=None,
        description="LLM to use for AI-assisted prompt generation (/generate and /refine commands). If None, AI features disabled.",
    )

    hitl_generate_system_prompt: str = Field(
        default="""You are a prompt engineer specializing in video analysis.
Your task is to create a clear, detailed prompt for a Vision Language Model (VLM) that will analyze video footage.

Requirements for the generated prompt:
- Be specific about what to look for in the video
- Include instructions to describe events with timestamps in chronological[Xs-Ys] format
- Focus on the user's described scenario/goals
- Keep the prompt concise but comprehensive

Output ONLY the VLM prompt, no explanations or preamble.""",
        description="System prompt for the /generate command. User's description will be appended.",
    )

    hitl_refine_system_prompt: str = Field(
        default="""You are a prompt engineer specializing in video analysis.
Your task is to modify an existing VLM prompt based on the user's instructions.

Requirements:
- Preserve the timestamp format [Xs-Ys] requirement
- Incorporate the user's requested changes
- Keep the prompt structure clear and actionable
- Output ONLY the modified prompt, no explanations

Current prompt to modify:
{current_prompt}

User's modification request:""",
        description="System prompt for the /refine command. Contains {current_prompt} placeholder.",
    )


class VideoReportGenInput(BaseModel):
    """Input for Video(uploaded) Report generation."""

    sensor_id: str = Field(
        ...,
        description="VST sensor ID (filename of uploaded video, e.g., 'warehouse_01.mp4')",
    )
    user_query: str = Field(
        ...,
        description="The user's question or analysis request for this video",
    )
    vlm_reasoning: bool | None = Field(
        default=None,
        description="Enable VLM reasoning mode for video analysis",
    )
    model_config = {
        "extra": "forbid",
    }


class VideoReportGenOutput(BaseModel):
    """Output from Video(uploaded) Report generation."""

    http_url: str | None = Field(default=None, description="HTTP URL to access the markdown report file")
    pdf_url: str | None = Field(default=None, description="HTTP URL to access the PDF report file (if generated)")
    object_store_key: str | None = Field(default=None, description="Key/filename in the object store")
    summary: str | None = Field(default=None, description="Brief summary of the report (or cancellation message)")
    file_size: int = Field(default=0, description="Size of the markdown report file in bytes")
    pdf_file_size: int = Field(default=0, description="Size of the PDF report file in bytes")
    content: str | None = Field(default=None, description="The actual markdown content of the generated report")
    video_url: str | None = Field(default=None, description="The URL of the video playback")
    hitl_prompts: dict | None = Field(default=None, description="HITL prompts used for the report")


async def _save_markdown_to_object_store(
    markdown_content: str,
    filename: str,
    object_store: Any,
    config: VideoReportGenConfig,
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
        "report_type": "video report",
    }

    object_store_item = ObjectStoreItem(data=content_bytes, content_type="text/markdown", metadata=metadata)
    await object_store.upsert_object(filename, object_store_item)
    logger.info(f"Markdown report saved to object store: {filename}")

    # Get HTTP URL
    http_url = _get_object_store_url(object_store, filename, config)

    return http_url, file_size


async def _save_pdf_to_object_store(
    markdown_content: str,
    filename: str,
    pdf_filename: str,
    object_store: Any,
    config: VideoReportGenConfig,
) -> tuple[str | None, int]:
    """Generate PDF from markdown and save to object store. Returns URL and size."""
    pdf_file_size = 0
    pdf_url = None

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
                    "report_type": "video report",
                },
            )
            await object_store.upsert_object(pdf_filename, pdf_object_store_item)

            # Get HTTP URL
            pdf_url = _get_object_store_url(object_store, pdf_filename, config)

            logger.info(f"PDF report saved to object store: {pdf_filename}")
        else:
            logger.warning("Video Analysis Report: Failed to generate PDF report")

    return pdf_url, pdf_file_size


class TimestampMatch(NamedTuple):
    """Parsed timestamp from VLM response content."""

    position: int  # Character position in content string
    seconds: float  # Timestamp in seconds


def _parse_timestamps(content: str) -> list[TimestampMatch]:
    """
    Parse timestamps from content in [Xs-Ys] format.

    Matches: [5.2s-8.0s] or [15s - 20s] etc.
    Uses midpoint of the span for snapshot.

    Returns list of TimestampMatch with position and midpoint seconds.
    """
    matches: list[TimestampMatch] = []

    # [Xs-Ys] format
    pattern = re.compile(
        r"(?:\*\*\s*)?"  # optional leading ** and spaces
        r"\[\s*"  # literal [
        r"(\d+(?:\.\d+)?)(?:s)?"  # group 1: start time
        r"\s*-\s*"
        r"(\d+(?:\.\d+)?)(?:s)?"  # group 2: end time
        r"\s*\]"  # literal ]
        r"(?:\s*\*\*)?"  # optional trailing ** and spaces
    )
    for match in re.finditer(pattern, content):
        start_seconds = float(match.group(1))
        end_seconds = float(match.group(2))
        midpoint = (start_seconds + end_seconds) / 2
        matches.append(TimestampMatch(position=match.start(), seconds=midpoint))

    return matches


def _normalize_chunk_timestamps(content: str, chunk_start: float, chunk_end: float) -> str:
    """
    Normalize timestamps in VLM response content by adding chunk offset.

    VLM returns timestamps relative to the chunk (starting from 0s).
    This function:
    1. Finds the max end timestamp in the content
    2. Computes ratio = max_end / chunk_duration
    3. If ratio > 1, scales all timestamps down by the ratio
    4. Adds chunk_start offset to convert to absolute video time

    Args:
        content: VLM response content with relative timestamps in [Xs-Ys] format
        chunk_start: Start time of the chunk in seconds (offset to add)
        chunk_end: End time of the chunk in seconds (for ratio calculation)

    Returns:
        Content with timestamps normalized to absolute video time
    """
    # [Xs-Ys] format
    pattern = re.compile(
        r"(?:\*\*\s*)?"  # optional leading ** and spaces
        r"\[\s*"  # literal [
        r"(\d+(?:\.\d+)?)(?:s)?"  # group 1: start time
        r"\s*-\s*"
        r"(\d+(?:\.\d+)?)(?:s)?"  # group 2: end time
        r"\s*\]"  # literal ]
        r"(?:\s*\*\*)?"  # optional trailing ** and spaces
    )

    # First pass: find all timestamps and the max end value
    matches_data: list[tuple[re.Match, float, float]] = []
    max_end_sec = 0.0
    for match in re.finditer(pattern, content):
        start_sec = float(match.group(1))
        end_sec = float(match.group(2))
        matches_data.append((match, start_sec, end_sec))
        max_end_sec = max(max_end_sec, end_sec)
    chunk_duration = chunk_end - chunk_start
    if not matches_data or chunk_duration <= 0:
        return content
    # Compute normalization ratio
    ratio = max_end_sec / chunk_duration
    should_normalize = ratio > 1.0

    if should_normalize:
        logger.info(
            f"Normalizing chunk timestamps: max_end_sec={max_end_sec:.1f}s, "
            f"chunk_duration={chunk_duration:.1f}s, ratio={ratio:.2f}"
        )

    # Second pass: replace timestamps with normalized values
    result = content
    for match, start_sec, end_sec in matches_data:
        # Scale timestamps if ratio differs significantly from 1.0

        if should_normalize:
            start_sec /= ratio
            end_sec /= ratio

        # Add chunk offset to convert to absolute time
        abs_start = chunk_start + start_sec
        abs_end = chunk_start + end_sec
        replacement = f"[{abs_start:.1f}s-{abs_end:.1f}s]"
        result = result.replace(match.group(0), replacement, 1)
    return result


def _filter_short_duration_from_markdown(content: str, min_duration_seconds: float = 2.0) -> str:
    """
    Filter out sentences/lines containing timestamp ranges with duration less than the specified threshold.

    Parses markdown content for [Xs-Ys] timestamp patterns, calculates duration,
    and removes lines describing events shorter than min_duration_seconds.

    Args:
        content: Markdown content with timestamps in [Xs-Ys] format
        min_duration_seconds: Minimum event duration in seconds (default: 2.0)

    Returns:
        Filtered markdown content with short duration events removed
    """
    if not content:
        return content

    # Pattern to match [Xs-Ys] timestamps
    timestamp_pattern = re.compile(r"\[\s*(\d+(?:\.\d+)?)(?:s)?\s*-\s*(\d+(?:\.\d+)?)(?:s)?\s*\]")

    # Process line by line
    lines = content.split("\n")
    filtered_lines = []

    for line in lines:
        # Find all timestamps in the line
        matches = list(timestamp_pattern.finditer(line))

        if not matches:
            # No timestamps, keep the line
            filtered_lines.append(line)
            continue

        # Check if any timestamp in this line has sufficient duration
        has_valid_duration = False
        for match in matches:
            start_time = float(match.group(1))
            end_time = float(match.group(2))
            duration = end_time - start_time

            if duration >= min_duration_seconds:
                has_valid_duration = True
                break

        if has_valid_duration:
            filtered_lines.append(line)
        else:
            # Log the filtered line for debugging
            duration = float(matches[0].group(2)) - float(matches[0].group(1))
            line_preview = line.strip()[:100]
            logger.info(
                f"Filtered out short duration line (duration={duration:.1f}s < {min_duration_seconds:.1f}s): "
                f"{line_preview}"
            )

    return "\n".join(filtered_lines)


def _mmss_to_iso(time_str: str, ref_timestamp: str) -> str:
    """
    Convert MM:SS or Xs to ISO 8601 timestamp by adding offset to reference timestamp.

    Args:
        time_str: Time string in "MM:SS" format (e.g., "01:30") or "Xs" format (e.g., "5.2s")
        ref_timestamp: Reference timestamp in ISO 8601 format to add the offset to

    Returns:
        ISO timestamp string with offset added to ref_timestamp
    """
    if time_str.endswith("s"):
        # Seconds format from LVS (e.g., "5.2s")
        total_seconds = int(float(time_str[:-1]))
    else:
        # MM:SS format from regular VLM
        parts = time_str.split(":")
        minutes = int(parts[0])
        seconds = int(parts[1])
        total_seconds = minutes * 60 + seconds

    # Parse reference timestamp and add offset
    ref_dt = iso8601_to_datetime(ref_timestamp)
    result_dt = ref_dt + timedelta(seconds=total_seconds)

    return datetime_to_iso8601(result_dt)


async def _inject_video_clips(
    content: str,
    sensor_id: str,
    vst_internal_url: str | None,
    vst_external_url: str | None,
    disable_audio: bool = True,
) -> str:
    """
    Parse timestamps from content and inject video clip links.

    For each timestamp range [Xs-Ys] found:
    1. Parse start and end times
    2. Generate video clip URL using VST
    3. Inject [Watch Clip] link right after the timestamp

    Args:
        content: Markdown content with timestamps in [Xs-Ys] format
        sensor_id: Video sensor ID
        vst_internal_url: VST internal URL
        vst_external_url: VST external URL

    Returns:
        Content with [Watch Clip] links injected after timestamps

    Note:
        Video clip durations may be slightly longer than the timestamp range due to
        VST aligning clips to video keyframes (I-frames) for proper playback.
        For example, [120s-130s] (10s) may result in a 12-13 second clip.
    """
    if not (vst_internal_url and vst_external_url):
        logger.debug("Video Analysis Report: VST URLs not configured, skipping video clip injection")
        return content

    # Pattern to match [Xs-Ys] timestamps
    pattern = re.compile(
        r"(\[\s*"  # group 1: opening bracket and spaces
        r"(\d+(?:\.\d+)?)(?:s)?"  # group 2: start time
        r"\s*-\s*"
        r"(\d+(?:\.\d+)?)(?:s)?"  # group 3: end time
        r"\s*\])"  # closing bracket
    )

    matches = list(pattern.finditer(content))
    if not matches:
        logger.debug("Video Analysis Report: No timestamps found in content for video clip injection")
        return content

    try:
        stream_id = await get_stream_id(sensor_id, vst_internal_url)
    except Exception as e:
        logger.warning(f"Failed to get stream_id for video clips: {e}")
        return content

    # Process matches in reverse order to preserve positions
    result_content = content
    for match in reversed(matches):
        start_time = float(match.group(2))
        end_time = float(match.group(3))

        try:
            logger.info(f"Generating video clip URL for [{start_time}s-{end_time}s]")
            clip_url = await get_video_url(
                stream_id=stream_id,
                start_time=start_time,
                end_time=end_time,
                vst_internal_url=vst_internal_url,
                disable_audio=disable_audio,
            )
            # Replace internal URL with external URL for client access
            clip_url = f"{vst_external_url}{urllib.parse.urlparse(clip_url).path}"
            video_clip_link = f" [[Watch Clip]({clip_url})]"

            # Inject right after the timestamp
            insert_pos = match.end()
            result_content = result_content[:insert_pos] + video_clip_link + result_content[insert_pos:]
        except Exception as e:
            logger.warning(f"Failed to generate video clip URL for [{start_time}s-{end_time}s]: {e}")
            continue

    return result_content


async def _inject_snapshots(
    content: str,
    sensor_id: str,
    picture_url_tool: Any,
) -> str:
    """
    Parse timestamps from content, fetch snapshots, and inject images after the sentence.

    For each timestamp span found:
    1. Extract the midpoint of the timestamp span
    2. Call picture_url_tool to get snapshot at that time
    3. Find the next period after the timestamp
    4. Insert image markdown after that period

    Args:
        content: VLM markdown content with normalized timestamps
        sensor_id: Video sensor ID
        picture_url_tool: Tool to fetch snapshot URLs

    Returns:
        Content with snapshot images injected
    """
    if not picture_url_tool:
        logger.warning("Video Analysis Report: No picture_url_tool configured, skipping snapshot injection")
        return content
    timestamps = _parse_timestamps(content)

    if not timestamps:
        logger.warning("Video Analysis Report: No timestamps found in VLM response for snapshot injection")
        return content

    image_urls = await asyncio.gather(
        *[
            picture_url_tool.ainvoke(
                input={
                    "sensor_id": sensor_id,
                    "start_time": ts.seconds,
                }
            )
            for ts in timestamps
        ]
    )
    result_content = content
    for ts, image_url in reversed(list(zip(timestamps, image_urls, strict=False))):
        # Format seconds to readable string for alt text
        mins = int(ts.seconds) // 60
        secs = int(ts.seconds) % 60
        time_str = f"{mins:02d}:{secs:02d}"
        # Use HTML img tag for size control with minimal spacing
        # Add style to control margins for PDF rendering
        image_md = (
            f'\n\n<img src="{image_url.image_url}" alt="Snapshot at {time_str}" width="400" style="margin: 10px 0;">\n'
        )
        result_content = result_content[: ts.position] + image_md + result_content[ts.position :]
    return result_content


def _clean_vlm_response(vlm_response: str) -> str:
    """
    Clean and validate the VLM markdown response.

    Removes code block wrappers, thinking/answer tags, and extracts
    the actual markdown report content.
    """
    cleaned = vlm_response.strip()

    # Remove <think>...</think> blocks (with closing tag)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    # If response starts with <think> but no closing tag, find the first markdown heading
    if cleaned.strip().lower().startswith("<think>"):
        # Find the first markdown heading (# at start of line)
        heading_match = re.search(r"^#+ ", cleaned, re.MULTILINE)
        if heading_match:
            cleaned = cleaned[heading_match.start() :].strip()
        else:
            # Just remove the <think> tag
            cleaned = re.sub(r"^<think>\s*", "", cleaned, flags=re.IGNORECASE)

    # If there's a </think> tag, delete everything before it (including the tag itself)
    # This handles cases where LLM outputs thinking without opening <think> tag
    think_end_match = re.search(r"</think>", cleaned, flags=re.IGNORECASE)
    if think_end_match:
        # Keep everything after the </think> tag
        cleaned = cleaned[think_end_match.end() :].strip()

    # Check for <answer> tags and extract content within them
    answer_match = re.search(r"<answer>(.*?)</answer>", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if answer_match:
        cleaned = answer_match.group(1).strip()
    else:
        # Remove <answer> and </answer> tags if present but not properly paired
        cleaned = re.sub(r"</?answer>", "", cleaned, flags=re.IGNORECASE)

    # Clean up whitespace
    cleaned = cleaned.strip()

    # Remove markdown code block wrappers (do this after think tag removal)
    # Handle various code block types: ```markdown, ```plaintext, ```text, ```
    code_block_prefixes = ["```markdown", "```plaintext", "```text", "```"]
    for prefix in code_block_prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return cleaned


def _filter_short_events(events: list[dict | Any], min_duration_seconds: float = 2.0) -> list[dict | Any]:
    """
    Filter out events with duration less than the specified threshold.

    Events shorter than min_duration_seconds are removed to reduce noise in reports.
    Events with invalid or missing timestamps are kept as-is.

    Args:
        events: List of event dictionaries with start_time and end_time fields
        min_duration_seconds: Minimum event duration in seconds (default: 2.0)

    Returns:
        List of events with duration >= min_duration_seconds
    """
    filtered_events = []
    for event in events:
        if isinstance(event, dict):
            start_time = event.get("start_time", "N/A")
            end_time = event.get("end_time", "N/A")
            # Skip events with invalid times or duration less than threshold
            if start_time != "N/A" and end_time != "N/A":
                try:
                    duration = float(end_time) - float(start_time)
                    if duration >= min_duration_seconds:
                        filtered_events.append(event)
                    else:
                        logger.info(
                            f"Filtered out short event (duration={duration:.1f}s < {min_duration_seconds:.1f}s): "
                            f"{event.get('description', '')[:50]}"
                        )
                except (ValueError, TypeError):
                    # If we can't parse times, keep the event
                    filtered_events.append(event)
            else:
                # If times are missing, keep the event
                filtered_events.append(event)
        else:
            # Non-dict events are kept as-is
            filtered_events.append(event)
    return filtered_events


def _format_lvs_response(lvs_response: str) -> str:
    """
    Format the LVS video understanding tool response into a readable markdown template.

    The lvs_video_understanding tool returns JSON like:
    {
        "video_summary": "...",
        "events": [...],
        "hitl_prompts": {
            "scenario": "...",
            "events": [...],
            "objects_of_interest": [...]
        },
        "lvs_backend_response": {...}
    }

    Note: The LVS backend service itself only returns video_summary and events.
    The hitl_prompts are added by the lvs_video_understanding tool wrapper.
    Video clip links are injected later by _inject_video_clips in the main workflow.

    Args:
        lvs_response: JSON string from LVS tool
    """
    try:
        lvs_data = json.loads(lvs_response)

        # Extract fields
        video_summary = lvs_data.get("video_summary", "")
        events = lvs_data.get("events", [])

        # Clean thinking tags from video_summary
        video_summary = _clean_vlm_response(video_summary)

        # Build formatted output
        formatted_lines = []

        if video_summary:
            formatted_lines.extend(
                [
                    "**Video Summary:**",
                    "",
                    video_summary,
                    "",
                ]
            )

        if events:
            # Filter out events that are less than 2 seconds in duration
            filtered_events = _filter_short_events(events, min_duration_seconds=2.0)

            if filtered_events:
                event_count = len(filtered_events)
                formatted_lines.extend(
                    [
                        "**Events:**",
                        "",
                        f"{event_count} event(s) were detected in the video. See details below.",
                        "",
                    ]
                )
                for event in filtered_events:
                    if isinstance(event, dict):
                        start_time = event.get("start_time", "N/A")
                        end_time = event.get("end_time", "N/A")
                        description = event.get("description", "")
                        # Clean thinking tags from description
                        description = _clean_vlm_response(description)
                        formatted_lines.append(f"- **[{start_time}s - {end_time}s]**: {description}")
                    else:
                        formatted_lines.append(f"- {event}")
            else:
                formatted_lines.append("*No events detected.*")
        else:
            formatted_lines.append("*No events detected.*")

        return "\n".join(formatted_lines)

    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Video Analysis Report: Failed to parse LVS response as JSON: {e}, returning raw response")
        return lvs_response


def _create_report_header(
    sensor_id: str,
    user_query: str,
    hitl_prompts: dict | None = None,
) -> str:
    """
    Create the standard report header with metadata.

    Args:
        sensor_id: The video sensor ID
        user_query: The user's analysis request
        hitl_prompts: Optional HITL prompts dict (scenario, events, objects_of_interest) from LVS
    """
    now = datetime.now()
    report_date = now.strftime("%Y-%m-%d")
    report_time = now.strftime("%H:%M:%S")
    report_timestamp = now.strftime("%Y%m%d_%H%M%S")
    vss_agent_version = os.getenv("VSS_AGENT_VERSION", "dev")

    report_lines = [
        "# Video Analysis Report",
        "",
        "## Basic Information",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Report Identifier** | vss_report_{report_timestamp} |",
        f"| **Date of Analysis** | {report_date} |",
        f"| **Time of Analysis** | {report_time} |",
        f"| **Reporting AI Agent** | vss_agent {vss_agent_version} |",
        f"| **Video Source** | {sensor_id} |",
        f"| **Analysis Request** | {user_query} |",
    ]
    if hitl_prompts:
        # Add HITL prompts to Basic Information table
        scenario = hitl_prompts.get("scenario", "")
        if scenario:
            report_lines.append(f"| **Prompt - Scenario** | {scenario} |")

        events_list = hitl_prompts.get("events", [])
        if events_list:
            report_lines.append(f"| **Prompt - Events of Interest** | {', '.join(events_list)} |")

        objects_list = hitl_prompts.get("objects_of_interest", [])
        if objects_list:
            report_lines.append(f"| **Prompt - Objects of Interest** | {', '.join(objects_list)} |")

    report_lines.append("")  # Close table with empty line

    report_lines.extend(
        [
            "## Analysis Results",
            "",
            "",
        ]
    )

    return "\n".join(report_lines)


@register_function(config_type=VideoReportGenConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def video_report_gen(config: VideoReportGenConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Video(uploaded) Report Generation Tool.

    Generates comprehensive video analysis reports for uploaded videos without Video Analytics MCP.
    Handles VLM prompt sanitization, video analysis, and optional template-based formatting.
    """

    # Load tools
    object_store = await builder.get_object_store_client(config.object_store)
    video_understanding_tool = await builder.get_tool(
        config.video_understanding_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN
    )

    # Load LVS tool if configured (optional)
    lvs_video_understanding_tool = None
    if config.lvs_video_understanding_tool is not None:
        try:
            lvs_video_understanding_tool = await builder.get_tool(
                config.lvs_video_understanding_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN
            )
        except ValueError as e:
            logger.warning(
                f"Video Analysis Report: LVS tool '{config.lvs_video_understanding_tool}' not found, LVS features will be disabled: {e}"
            )
            lvs_video_understanding_tool = None

    video_url_tool = None
    if config.video_url_tool:
        video_url_tool = await builder.get_tool(config.video_url_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    picture_url_tool = None
    if config.picture_url_tool:
        picture_url_tool = await builder.get_tool(config.picture_url_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN)

    # Load HITL LLM if configured (for /generate and /refine commands)
    hitl_llm = None
    if config.hitl_prompt_llm:
        try:
            hitl_llm = await builder.get_llm(config.hitl_prompt_llm, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
            logger.info(f"HITL LLM loaded: {config.hitl_prompt_llm}")
        except Exception as e:
            logger.warning(f"Failed to load HITL LLM '{config.hitl_prompt_llm}': {e}. AI prompt generation disabled.")
            hitl_llm = None

    # HITL state: maps thread_id -> vlm_prompt (persisted per conversation)
    # Uses OrderedDict as LRU cache to prevent unbounded memory growth.
    #
    # max_conversations: Maximum number of conversation states to retain.
    # - Each entry stores ~1-2KB (thread_id + prompt string)
    # - At 1000 conversations: ~1-2MB memory footprint
    # - Oldest entries are evicted when limit is exceeded (LRU policy)
    # - Operators can adjust this value based on expected concurrent users
    #   and available memory. For high-traffic deployments, consider 500-2000.
    max_conversations = 1000
    vlm_prompt_state: OrderedDict[str, str] = OrderedDict()

    def _store_prompt(thread_id: str, prompt: str) -> None:
        """Store a prompt for a thread, evicting oldest entries if over capacity."""
        # If key exists, remove it first to update insertion order (LRU behavior)
        if thread_id in vlm_prompt_state:
            vlm_prompt_state.move_to_end(thread_id)
        vlm_prompt_state[thread_id] = prompt

        # Evict oldest entries if over capacity
        while len(vlm_prompt_state) > max_conversations:
            evicted_id, _ = vlm_prompt_state.popitem(last=False)
            logger.debug(f"Evicted prompt state for thread {evicted_id} (LRU capacity: {max_conversations})")

    def _get_prompt(thread_id: str) -> str | None:
        """Get a prompt for a thread, updating access order (LRU behavior)."""
        if thread_id in vlm_prompt_state:
            vlm_prompt_state.move_to_end(thread_id)
            return vlm_prompt_state[thread_id]
        return None

    # Default HITL template if not provided in config
    default_hitl_vlm_prompt_template = """**VLM Prompt for Report Generation**

**OPTIONS:**

• Press Submit (empty) → Approve and generate report

• Type a new prompt → Use it directly

• Type `/generate <description>` → AI creates a prompt based on your description

• Type `/refine <instructions>` → AI modifies the current prompt

• Type `/cancel` → Cancel report generation

Enter your choice or press Submit to keep current value:"""

    async def _prompt_user_input(prompt_text: str, required: bool = True, placeholder: str = "") -> str | None:
        """Prompt user for input using HITL with option to cancel via /cancel.

        Args:
            prompt_text: The prompt text to show to the user
            required: Whether the input is required
            placeholder: Placeholder text for the input field

        Returns:
            str: User's input text, or None if user cancelled
        """
        nat_context = Context.get()
        user_input_manager = nat_context.user_interaction_manager

        human_prompt = HumanPromptText(text=prompt_text, required=required, placeholder=placeholder)

        response: InteractionResponse = await user_input_manager.prompt_user_input(human_prompt)

        # Check if user cancelled - content will be None when cancelled
        if response.content is None:
            logger.info("User cancelled HITL prompt")
            return None

        # Check if content.text is None (another possible cancel indicator)
        if hasattr(response.content, "text") and response.content.text is None:
            logger.info("User cancelled HITL prompt")
            return None

        # Return raw text (no strip) so caller can treat only truly empty input as "approve default"
        return response.content.text  # type: ignore

    def _wrap_text_at_words(text: str, words_per_line: int = 12) -> str:
        """
        Insert newlines to wrap text at approximately the specified number of words per line.

        Preserves existing newlines and only wraps within continuous text segments.

        Args:
            text: The text to wrap
            words_per_line: Number of words before inserting a newline (default: 12)

        Returns:
            str: Text with newlines inserted for wrapping
        """
        if not text:
            return text

        # Split by existing newlines to preserve them
        lines = text.split("\n")
        wrapped_lines = []

        for line in lines:
            if not line.strip():
                # Preserve empty lines
                wrapped_lines.append(line)
                continue

            words = line.split()
            if len(words) <= words_per_line:
                wrapped_lines.append(line)
                continue

            # Wrap long lines
            current_line_words = []
            for word in words:
                current_line_words.append(word)
                if len(current_line_words) >= words_per_line:
                    wrapped_lines.append(" ".join(current_line_words))
                    current_line_words = []

            # Add remaining words
            if current_line_words:
                wrapped_lines.append(" ".join(current_line_words))

        return "\n".join(wrapped_lines)

    async def _llm_generate_prompt(description: str) -> str:
        """Generate a VLM prompt using LLM based on user's description.

        Raises:
            ValueError: If LLM is not configured or if LLM call/response processing fails.
        """
        if not hitl_llm:
            raise ValueError("AI prompt generation not available. Configure hitl_prompt_llm in config.")

        from langchain_core.messages import HumanMessage
        from langchain_core.messages import SystemMessage

        messages = [
            SystemMessage(content=config.hitl_generate_system_prompt),
            HumanMessage(content=description),
        ]

        # Call LLM with error handling
        try:
            response = await hitl_llm.ainvoke(messages)
        except Exception as e:
            logger.error(f"LLM prompt generation failed during ainvoke: {type(e).__name__}: {e}")
            raise ValueError(f"Failed to generate prompt: LLM call failed - {e}") from e

        # Process response with error handling - use shared reasoning parser
        try:
            _, generated = parse_reasoning_content(response)
            generated = (generated or "").strip()
            # Wrap text for better readability in UI
            generated = _wrap_text_at_words(generated)
        except Exception as e:
            logger.error(f"LLM prompt generation failed during response processing: {type(e).__name__}: {e}")
            raise ValueError(f"Failed to generate prompt: response processing failed - {e}") from e

        logger.info(f"LLM generated prompt: {generated[:100]}...")
        return generated

    async def _llm_refine_prompt(current_prompt: str, instructions: str) -> str:
        """Refine existing prompt using LLM based on user's instructions.

        Raises:
            ValueError: If LLM is not configured or if LLM call/response processing fails.
        """
        if not hitl_llm:
            raise ValueError("AI prompt refinement not available. Configure hitl_prompt_llm in config.")

        from langchain_core.messages import HumanMessage
        from langchain_core.messages import SystemMessage

        # Replace {current_prompt} placeholder in system prompt
        system_prompt = config.hitl_refine_system_prompt.replace("{current_prompt}", current_prompt)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=instructions),
        ]

        # Call LLM with error handling
        try:
            response = await hitl_llm.ainvoke(messages)
        except Exception as e:
            logger.error(f"LLM prompt refinement failed during ainvoke: {type(e).__name__}: {e}")
            raise ValueError(f"Failed to refine prompt: LLM call failed - {e}") from e

        # Process response with error handling - use shared reasoning parser
        try:
            _, refined = parse_reasoning_content(response)
            refined = (refined or "").strip()
            # Wrap text for better readability in UI
            refined = _wrap_text_at_words(refined)
        except Exception as e:
            logger.error(f"LLM prompt refinement failed during response processing: {type(e).__name__}: {e}")
            raise ValueError(f"Failed to refine prompt: response processing failed - {e}") from e

        logger.info(f"LLM refined prompt: {refined[:100]}...")
        return refined

    async def _collect_hitl_vlm_prompt(current_prompt: str | None) -> str | None:
        """
        Collect/confirm VLM prompt via HITL with support for /generate and /refine commands.

        Flow:
        1. Show current prompt
        2. User can: approve (empty), edit directly, /generate, /refine, or /cancel
        3. If /generate or /refine, show result and loop for approval
        4. Plain text or empty = final answer (no loop)

        Args:
            current_prompt: Current prompt from state (if any)

        Returns:
            str: The confirmed or updated VLM prompt, or None if cancelled
        """
        logger.info("Starting HITL VLM prompt collection workflow")

        hitl_template = config.hitl_vlm_prompt_template or default_hitl_vlm_prompt_template

        # Track the working prompt and its source
        working_prompt = current_prompt or config.vlm_prompt
        prompt_source = "CURRENTLY SET" if current_prompt else "DEFAULT"
        error_message = ""  # Error message to display to user (cleared after each prompt)

        while True:
            # Build the display text, including any error message from previous iteration
            if error_message:
                prompt_text = f"**⚠️ ERROR:** {error_message}\n\n**{prompt_source}:**\n```\n{working_prompt}\n```\n\n{hitl_template}"
                error_message = ""  # Clear after displaying
            else:
                prompt_text = f"**{prompt_source}:**\n```\n{working_prompt}\n```\n\n{hitl_template}"

            user_input = await _prompt_user_input(
                prompt_text,
                required=False,
                placeholder="Enter prompt, /generate, /refine, /cancel, or press Submit to approve",
            )

            # User clicked Cancel button
            if user_input is None:
                logger.info("User cancelled report generation")
                return None

            # Only truly empty input = approve (do not strip before check; space-only is not approval)
            if user_input == "":
                logger.info(f"User approved {prompt_source.lower()} prompt")
                return working_prompt

            stripped = user_input.strip()
            # Handle /cancel command
            if stripped.lower() == "/cancel":
                logger.info("User cancelled report generation via /cancel command")
                return None

            # Handle /generate command
            if stripped.lower().startswith("/generate "):
                description = stripped[10:].strip()
                if not description:
                    logger.warning("Empty description for /generate, prompting again")
                    error_message = "Please provide a description after /generate"
                    continue
                try:
                    working_prompt = await _llm_generate_prompt(description)
                    prompt_source = "AI-GENERATED"
                    continue  # Loop to show generated prompt for approval
                except ValueError as e:
                    logger.error(f"Failed to generate prompt: {e!s}")
                    error_message = f"Failed to generate prompt: {e!s}"
                    continue

            # Handle /refine command
            if stripped.lower().startswith("/refine "):
                instructions = stripped[8:].strip()
                if not instructions:
                    logger.warning("Empty instructions for /refine, prompting again")
                    error_message = "Please provide instructions after /refine"
                    continue
                try:
                    working_prompt = await _llm_refine_prompt(working_prompt, instructions)
                    prompt_source = "AI-REFINED"
                    continue  # Loop to show refined prompt for approval
                except ValueError as e:
                    logger.error(f"Failed to refine prompt: {e!s}")
                    error_message = f"Failed to refine prompt: {e!s}"
                    continue

            # Whitespace-only = not valid; re-prompt
            if not stripped:
                error_message = (
                    "Input is empty or whitespace. Press Submit with no text to approve the default, or enter a prompt."
                )
                continue

            # Plain text = use directly (no further approval needed)
            logger.info(f"User provided custom prompt: {stripped[:100]}...")
            return stripped

    async def _video_report_gen(report_input: VideoReportGenInput) -> VideoReportGenOutput:
        """
        Generate a video analysis report for uploaded videos (Video(uploaded) Report mode).

        This tool:
        1. Sanitizes VLM prompts (removes SOM markers)
        2. Calls video_understanding tool for each prompt
        3. Formats results using optional template and LLM
        4. Saves markdown and PDF to object store
        5. Returns URLs and metadata
        """
        logger.info(f"Generating report for sensor '{report_input.sensor_id}'")
        logger.info(f"User query: {report_input.user_query}")

        # Decide which video understanding tool to use based on user's explicit request
        selected_tool = video_understanding_tool  # Default to regular tool
        tool_name = "video_understanding"
        lvs_fallback_warning = ""

        # Use LVS only if explicitly requested by user

        # based on config, use lvs if video duration is longer than config.lvs_video_length
        stream_id = await get_stream_id(report_input.sensor_id, config.vst_internal_url)
        start_timestamp, end_timestamp = await get_timeline(stream_id, config.vst_internal_url)
        start_dt = iso8601_to_datetime(start_timestamp)
        end_dt = iso8601_to_datetime(end_timestamp)
        duration_seconds = (end_dt - start_dt).total_seconds()
        if duration_seconds > config.lvs_video_length:
            if lvs_video_understanding_tool is not None:
                selected_tool = lvs_video_understanding_tool
                tool_name = "lvs_video_understanding"
                logger.info(f"Using LVS tool (video duration {duration_seconds:.1f}s > {config.lvs_video_length}s)")
            else:
                logger.warning(
                    "Video Analysis Report: LVS tool is not configured. "
                    f"Falling back to standard video_understanding tool. for video duration {duration_seconds:.1f}s > {config.lvs_video_length}s"
                )
                lvs_fallback_warning = (
                    f"⚠️ **Note:** Input video {report_input.sensor_id} is {duration_seconds:.1f}s long. \n"
                    f"Please use Long video Summarization' for videos longer than {config.lvs_video_length}s.\n\n"
                )
        else:
            logger.info(
                f"Using standard video_understanding tool (video duration {duration_seconds:.1f}s <= {config.lvs_video_length}s)"
            )

        # Step 2: Determine prompt and chunks based on tool selection
        chunks: list[tuple[float, float]] | None = None  # Only used for standard VLM
        clean_prompt = None  # Track the VLM prompt for report header
        if tool_name == "lvs_video_understanding":
            # LVS tool manages its own prompts via HITL - no chunking needed
            logger.info("Using LVS tool (prompts managed by HITL workflow)")

            # Step 3: Run LVS analysis on entire video
            vlm_input: dict[str, str | bool] = {
                "sensor_id": report_input.sensor_id,
            }

            # Add vlm_reasoning if specified
            if report_input.vlm_reasoning is not None:
                vlm_input["vlm_reasoning"] = report_input.vlm_reasoning

            try:
                vlm_results = [await selected_tool.ainvoke(input=vlm_input)]
            except Exception as e:
                logger.exception(f"Video Analysis Report: Failed to run LVS analysis: {e}")
                raise ValueError(
                    f"Video Analysis Report: Failed to analyze video '{report_input.sensor_id}': {e}"
                ) from e
        else:
            # Standard VLM: divide video into chunks and process in parallel

            # HITL: Collect/confirm VLM prompt if enabled
            if config.hitl_enabled:
                thread_id = ContextState.get().conversation_id.get()
                current_prompt = _get_prompt(thread_id)
                resolved_prompt = await _collect_hitl_vlm_prompt(current_prompt)

                # Check if user cancelled
                if resolved_prompt is None:
                    logger.info("Report generation cancelled by user")
                    return VideoReportGenOutput(
                        summary="Report generation was cancelled by the user.",
                        http_url=None,
                        pdf_url=None,
                        object_store_key=None,
                        file_size=0,
                        pdf_file_size=0,
                        content=None,
                        video_url=None,
                    )

                _store_prompt(thread_id, resolved_prompt)
                logger.info(f"[PROMPT LOADED] video_report_gen.vlm_prompt from HITL: '{resolved_prompt[:100]}...'")
                clean_prompt = _remove_som_markers(resolved_prompt)
            else:
                logger.info(f"[PROMPT LOADED] video_report_gen.vlm_prompt from CONFIG: '{config.vlm_prompt[:100]}...'")
                clean_prompt = _remove_som_markers(config.vlm_prompt)

            stream_id = await get_stream_id(report_input.sensor_id, config.vst_internal_url)
            start_timestamp, end_timestamp = await get_timeline(stream_id, config.vst_internal_url)
            start_dt = iso8601_to_datetime(start_timestamp)
            end_dt = iso8601_to_datetime(end_timestamp)
            duration_seconds = (end_dt - start_dt).total_seconds()

            if duration_seconds > config.max_duration_for_chunking:
                # Video too long for chunking - process as single chunk
                logger.warning(
                    f"Video duration ({duration_seconds:.1f}s) exceeds chunking threshold ({config.max_duration_for_chunking}s). "
                    f"Processing entire video as single chunk. Quality may be degraded."
                )
                chunks = [(0.0, duration_seconds)]
            else:
                # Divide video into chunks
                chunks = _divide_video_into_chunks(
                    duration_seconds,
                    config.chunk_duration_seconds,
                )
                logger.info(f"Divided video into {len(chunks)} chunks of {config.chunk_duration_seconds}s each")

            # Step 3: Run VLM analysis tasks in parallel (one per chunk)
            logger.info(f"Running {len(chunks)} VLM analysis tasks with {tool_name}")

            # FIX: The video understanding tool has two input modes:
            #   - stream_mode=true  -> VideoUnderstandingInput (start_timestamp: str, ISO 8601)
            #   - stream_mode=false -> VideoUnderstandingInputNonStream (start_timestamp: float, seconds offset)
            # Previously, ISO strings were always passed regardless of the tool's mode,
            # which caused a "could not convert string to float" validation error when
            # the tool was configured with stream_mode=false (e.g. dev-profile-base).
            # We now inspect the tool's input schema to detect which format it expects.
            uses_float_timestamps = True
            tool_args_schema = getattr(selected_tool, "args_schema", None)
            if tool_args_schema and hasattr(tool_args_schema, "model_fields"):
                ts_field = tool_args_schema.model_fields.get("start_timestamp")
                if ts_field:
                    field_type = ts_field.annotation
                    uses_float_timestamps = field_type is float or (
                        hasattr(field_type, "__args__") and float in field_type.__args__
                    )

            chunk_process_start_time = datetime.now()
            vlm_tasks = []
            for chunk_idx, (chunk_start, chunk_end) in enumerate(chunks):
                vlm_prompt = (
                    clean_prompt
                    + "\n\n"
                    + CHUNK_TIMESTAMP_PROMPT.format(start_time=0, end_time=chunk_end - chunk_start)
                )

                if uses_float_timestamps:
                    # Non-stream mode: pass float offsets (seconds since beginning of stream)
                    chunk_vlm_input: dict[str, Any] = {
                        "sensor_id": report_input.sensor_id,
                        "start_timestamp": chunk_start,
                        "end_timestamp": chunk_end,
                        "user_prompt": vlm_prompt,
                    }
                else:
                    # Stream mode: convert chunk offsets to ISO timestamp strings
                    chunk_start_dt = start_dt + timedelta(seconds=chunk_start)
                    chunk_end_dt = start_dt + timedelta(seconds=chunk_end)
                    chunk_vlm_input = {
                        "sensor_id": report_input.sensor_id,
                        "start_timestamp": datetime_to_iso8601(chunk_start_dt),
                        "end_timestamp": datetime_to_iso8601(chunk_end_dt),
                        "user_prompt": vlm_prompt,
                    }

                # Add vlm_reasoning if specified
                if report_input.vlm_reasoning is not None:
                    chunk_vlm_input["vlm_reasoning"] = report_input.vlm_reasoning

                logger.info(f"Chunk {chunk_idx + 1}/{len(chunks)}: {chunk_start} to {chunk_end}")
                vlm_tasks.append(selected_tool.ainvoke(input=chunk_vlm_input))

            try:
                vlm_results = await asyncio.gather(*vlm_tasks)
                chunk_process_elapsed = (datetime.now() - chunk_process_start_time).total_seconds()
                logger.info(
                    f"Successfully completed {len(vlm_results)} VLM chunk analyses in {chunk_process_elapsed:.2f}s"
                )
            except Exception as e:
                logger.exception(f"Video Analysis Report: Failed to run VLM analysis: {e}")
                raise ValueError(
                    f"Video Analysis Report: Failed to analyze video '{report_input.sensor_id}': {e}"
                ) from e

        # Step 4: Create report with header and VLM analysis
        logger.info(f"Processing {tool_name} response")

        # Extract HITL prompts if using LVS (needed for header)
        hitl_prompts = None
        if tool_name == "lvs_video_understanding" and vlm_results:
            try:
                # Parse the first LVS result to extract HITL prompts
                lvs_data = json.loads(vlm_results[0])

                # Check if LVS was aborted by user
                if lvs_data.get("status") == LVSStatus.ABORTED.value:
                    logger.info("LVS analysis was aborted by user, returning aborted message")
                    return VideoReportGenOutput(
                        http_url=None,
                        summary=lvs_data.get("message", "Video analysis was cancelled by user."),
                    )

                hitl_prompts = lvs_data.get("hitl_prompts")

            except Exception as e:
                logger.warning(f"Failed to extract HITL prompts from LVS response: {e}")

        report_header = _create_report_header(
            report_input.sensor_id,
            report_input.user_query,
            hitl_prompts=hitl_prompts,
        )

        # Format results based on tool type
        if tool_name == "lvs_video_understanding":
            # Format LVS responses (video clip links will be injected later)
            vlm_content = "\n\n".join([_format_lvs_response(result) for result in vlm_results])
        else:
            # Normalize timestamps in each chunk result and combine
            # VLM returns timestamps relative to chunk (starting from 0s)
            # We need to offset them by chunk_start to get absolute video time
            assert chunks is not None  # chunks is always set for non-LVS tools
            normalized_results = []
            for (chunk_start, chunk_end), result in zip(chunks, vlm_results, strict=True):
                cleaned = _clean_vlm_response(result)
                if config.normalize_timestamps:
                    normalized = _normalize_chunk_timestamps(cleaned, chunk_start, chunk_end)
                else:
                    normalized = cleaned
                # Filter out short duration events from markdown
                filtered = _filter_short_duration_from_markdown(normalized, min_duration_seconds=2.0)
                normalized_results.append(filtered)
            vlm_content = "\n\n".join(normalized_results)

        # Step 4b: Inject snapshots for timestamps found in VLM response
        if picture_url_tool:
            vlm_content = await _inject_snapshots(
                vlm_content,
                report_input.sensor_id,
                picture_url_tool,
            )

        # Step 4c: Inject video clip links for timestamps found in VLM response
        if config.vst_internal_url and config.vst_external_url:
            vlm_content = await _inject_video_clips(
                vlm_content,
                report_input.sensor_id,
                config.vst_internal_url,
                config.vst_external_url,
                config.disable_audio,
            )

        markdown_content = report_header + vlm_content

        # Step 5: Fetch video URL
        video_url = None

        if video_url_tool:
            try:
                video_result = await video_url_tool.ainvoke(
                    input={
                        "sensor_id": report_input.sensor_id,
                    }
                )
                video_url = video_result.video_url
                logger.info(f"Video URL: {video_url}")
            except Exception as e:
                logger.warning(f"Video Analysis Report: Failed to fetch video URL: {e}")

        # Append video URL to report
        # FIX: The URL is placed in its own paragraph (separated by \n\n) instead of
        # inline with the label. When both were on the same line, the CSS
        # text-align:justify caused xhtml2pdf to stretch the space between "Video
        # Playback:" and the URL across the full page width in the PDF output.
        if video_url:
            markdown_content += "\n\n## Resources\n\n"
            markdown_content += f"**Video Playback:**\n\n{video_url}\n\n"

        # Step 6: Save reports to object store
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"vss_report_{timestamp_str}.md"
        pdf_filename = filename.replace(".md", ".pdf")

        # Save markdown
        http_url, file_size = await _save_markdown_to_object_store(markdown_content, filename, object_store, config)

        # Save PDF
        pdf_url, pdf_file_size = await _save_pdf_to_object_store(
            markdown_content, filename, pdf_filename, object_store, config
        )

        # Step 7: Create summary
        summary = ""
        if lvs_fallback_warning:
            summary += lvs_fallback_warning
        summary += f"Report generated for '{report_input.sensor_id}'.\n\n"

        logger.info(f"report generation complete: {http_url}")

        return VideoReportGenOutput(
            http_url=http_url,
            pdf_url=pdf_url,
            object_store_key=filename,
            summary=summary,
            file_size=file_size,
            pdf_file_size=pdf_file_size,
            content=markdown_content,
            video_url=video_url,
            hitl_prompts=hitl_prompts,
        )

    desc = _video_report_gen.__doc__ if _video_report_gen.__doc__ is not None else ""
    if config.lvs_video_understanding_tool is not None:
        desc += f"\nlvs is available. report agent will call lvs to generate a report for videos longer than {config.lvs_video_length}s.\n\n"

    function_info = FunctionInfo.create(
        single_fn=_video_report_gen,
        description=desc,
        input_schema=VideoReportGenInput,
        single_output_schema=VideoReportGenOutput,
    )

    yield function_info
