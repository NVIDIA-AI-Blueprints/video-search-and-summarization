import json

from pydantic import BaseModel

from app.tools import ALL_TOOLS, get_timestamp_reference
from app.tools.base import observable_tool
from app.tools.common import format_timecode


def test_all_tools_registered():
    names = {t.name for t in ALL_TOOLS}
    assert names == {"search_transcript", "search_visual_events", "retrieve_context", "get_timestamp_reference"}


def test_format_timecode():
    assert format_timecode(0) == "00:00:00.000"
    assert format_timecode(65_500) == "00:01:05.500"
    assert format_timecode(3_723_004) == "01:02:03.004"


def test_get_timestamp_reference_normalizes_range():
    result = get_timestamp_reference.invoke({"start_ms": 5000, "end_ms": 1000, "video_id": "v1", "quote": "hello world"})
    citation = result["citation"]
    assert citation["start_ms"] == 5000
    assert citation["end_ms"] == 5000  # end < start is corrected to start
    assert citation["timestamp"] == "00:00:05.000"
    assert result["readable_range"] == "00:00:05.000 - 00:00:05.000"


class _BoomInput(BaseModel):
    value: int = 0


def _boom(value: int = 0) -> dict:
    raise RuntimeError("aws is down")


boom_tool = observable_tool(name="boom", description="always fails", args_schema=_BoomInput, func=_boom)


def test_error_isolation_returns_structured_error():
    result = boom_tool.invoke({"value": 1})
    assert result["error"]["tool"] == "boom"
    assert result["error"]["type"] == "RuntimeError"
    assert "aws is down" in result["error"]["message"]


class _EchoInput(BaseModel):
    text: str = ""


def _echo(text: str = "") -> dict:
    return {"results": [text], "citations": []}


echo_tool = observable_tool(name="echo", description="echo", args_schema=_EchoInput, func=_echo)


def test_success_result_passthrough():
    assert echo_tool.invoke({"text": "hi"}) == {"results": ["hi"], "citations": []}


def test_tools_expose_schema():
    for tool in ALL_TOOLS:
        schema = tool.args_schema.model_json_schema()
        assert "properties" in json.dumps(schema)
