"""Tool factory: independent observability + error isolation per tool.

Every tool logs start/success/error with duration, and converts exceptions
into a structured {"error": ...} result so one failing tool cannot crash the
agent loop or leak stack traces into the conversation.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

logger = logging.getLogger("ava.agent.tools")


def observable_tool(
    name: str,
    description: str,
    args_schema: type[BaseModel],
    func: Callable[..., dict],
) -> StructuredTool:
    def wrapped(**kwargs: Any) -> dict:
        started = time.perf_counter()
        logger.info("tool.start name=%s args=%s", name, _summarize(kwargs))
        try:
            result = func(**kwargs)
            duration_ms = int((time.perf_counter() - started) * 1000)
            result_count = len(result.get("results", [])) if isinstance(result, dict) else None
            logger.info(
                "tool.ok name=%s duration_ms=%d result_count=%s",
                name,
                duration_ms,
                result_count,
            )
            return result
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception("tool.error name=%s duration_ms=%d", name, duration_ms)
            return {
                "error": {
                    "tool": name,
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "duration_ms": duration_ms,
                }
            }

    return StructuredTool.from_function(
        func=wrapped,
        name=name,
        description=description,
        args_schema=args_schema,
        parse_output=False,
    )


def _summarize(kwargs: Any) -> str:
    parts = []
    for key, value in kwargs.items():
        text = str(value)
        if len(text) > 60:
            text = text[:57] + "..."
        parts.append(f"{key}={text}")
    return " ".join(parts)
