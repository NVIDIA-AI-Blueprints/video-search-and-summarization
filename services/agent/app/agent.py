"""Video analysis agent: LangGraph ReAct loop over AWS Bedrock."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_aws import ChatBedrock
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.models import AgentAnswer, coerce_citations
from app.prompts.system import SYSTEM_PROMPT
from app.tools import ALL_TOOLS

logger = logging.getLogger("ava.agent")

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        settings = get_settings()
        llm = ChatBedrock(
            model_id=settings.bedrock_model_id,
            region=settings.aws_region,
            model_kwargs={"temperature": 0.0, "max_tokens": 2048},
        )
        _graph = create_react_agent(llm, ALL_TOOLS, prompt=SYSTEM_PROMPT)
    return _graph


def _extract_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def run_agent(question: str, video_ids: list[str]) -> AgentAnswer:
    graph = _get_graph()
    context = (
        f"Videos available for this question: {video_ids or 'none provided; ask the user which video'}."
    )
    state = {"messages": [("user", f"{context}\n\nQuestion: {question}")]}
    result = graph.invoke(state)

    messages = result.get("messages", [])
    answer = _extract_text(messages[-1]) if messages else ""

    citations: list[dict] = []
    for message in messages:
        if getattr(message, "type", "") != "tool":
            continue
        try:
            payload = json.loads(_extract_text(message))
            citations.extend(payload.get("citations", []))
        except (ValueError, TypeError):
            continue
    citations.extend(_parse_inline_citations(answer))

    return AgentAnswer(answer=answer.strip(), citations=coerce_citations(citations))


async def run_agent_async(question: str, video_ids: list[str]) -> AgentAnswer:
    return await asyncio.to_thread(run_agent, question, video_ids)


def _parse_inline_citations(text: str) -> list[dict]:
    marker = text.lower().rfind("citations:")
    if marker == -1:
        return []
    import re

    found = []
    for match in re.finditer(r"\{[^{}]*video_id[^{}]*\}", text[marker:]):
        try:
            item = json.loads(match.group(0).replace("'", '"'))
            if "video_id" in item:
                found.append(item)
        except (ValueError, TypeError):
            continue
    return found


logger.debug("agent module loaded")
