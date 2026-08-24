"""Video analysis agent: LangGraph ReAct loop over AWS Bedrock."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_aws import ChatBedrock
from langgraph.prebuilt import create_react_agent

from app.config import get_settings
from app.prompts.system import SYSTEM_PROMPT
from app.tools import ALL_TOOLS, parse_citations


def _build_graph():
    settings = get_settings()
    llm = ChatBedrock(
        model_id=settings.bedrock_model_id,
        region=settings.aws_region,
        model_kwargs={"temperature": 0.0, "max_tokens": 2048},
    )
    return create_react_agent(llm, ALL_TOOLS, prompt=SYSTEM_PROMPT)


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


def run_agent(question: str, video_ids: list[str]) -> dict[str, Any]:
    graph = _get_graph()
    context = (
        f"Videos available for this question: {video_ids or 'none provided; ask the user which video'}."
    )
    state = {"messages": [("user", f"{context}\n\nQuestion: {question}")]}
    result = graph.invoke(state)

    messages = result.get("messages", [])
    answer = ""
    if messages:
        final = messages[-1]
        answer = getattr(final, "content", "")
        if isinstance(answer, list):
            answer = "".join(part.get("text", "") for part in answer if isinstance(part, dict))

    citations: list[dict] = []
    for message in messages:
        content = getattr(message, "content", "")
        if isinstance(content, str) and getattr(message, "type", "") == "tool":
            try:
                import json

                payload = json.loads(content)
                citations.extend(payload.get("citations", []))
            except (ValueError, TypeError):
                continue
    citations.extend(parse_citations(answer))

    deduped: list[dict] = []
    seen: set[tuple] = set()
    for citation in citations:
        key = (citation.get("video_id"), citation.get("start_ms"), citation.get("end_ms"))
        if key not in seen:
            seen.add(key)
            deduped.append(citation)

    return {"answer": answer.strip(), "citations": deduped}


async def run_agent_async(question: str, video_ids: list[str]) -> dict[str, Any]:
    return await asyncio.to_thread(run_agent, question, video_ids)
