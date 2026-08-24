from pydantic import BaseModel, Field

from app.tools.base import observable_tool
from app.tools.common import format_timecode, make_citation


class TimestampReferenceInput(BaseModel):
    start_ms: int = Field(description="Start of the cited range, milliseconds")
    end_ms: int = Field(default=0, description="End of the range; 0 means same as start")
    video_id: str = Field(description="Video the timestamps belong to")
    quote: str = Field(default="", description="Short supporting text for the citation")


def _get_timestamp_reference_impl(
    start_ms: int, end_ms: int, video_id: str, quote: str = ""
) -> dict:
    citation = make_citation(video_id, start_ms, end_ms, quote=quote)
    return {
        "citation": citation,
        "readable_range": f"{format_timecode(start_ms)} - {format_timecode(citation['end_ms'])}",
    }


get_timestamp_reference = observable_tool(
    name="get_timestamp_reference",
    description=(
        "Normalize and validate a timestamp range into a well-formed citation "
        "object with a human-readable timecode."
    ),
    args_schema=TimestampReferenceInput,
    func=_get_timestamp_reference_impl,
)
