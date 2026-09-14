# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the file-backed NemoClaw human interaction broker."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
import pytest

from vss_agents.orchestrator.interaction_broker import AskUserQuestionInput
from vss_agents.orchestrator.interaction_broker import ask_user_question
from vss_agents.orchestrator.interaction_broker import prepare_interaction_broker

INTERACTION_ID = UUID("12345678-1234-4234-8234-123456789abc")


def _request() -> AskUserQuestionInput:
    return AskUserQuestionInput.model_validate(
        {
            "interaction_id": str(INTERACTION_ID),
            "questions": [
                {
                    "question_id": "profile",
                    "header": "Profile",
                    "prompt": "Which deployment profile?",
                    "options": [
                        {"label": "Base", "description": "Dense captioning"},
                        {"label": "Search", "description": "Agentic search"},
                    ],
                    "allow_other": False,
                }
            ],
            "timeout_seconds": 30,
        }
    )


@pytest.mark.asyncio
async def test_blocks_until_response_and_cleans_spool(tmp_path: Path) -> None:
    task = asyncio.create_task(ask_user_question(tmp_path, _request()))
    request_path = tmp_path / f"{INTERACTION_ID}.request.json"
    response_path = tmp_path / f"{INTERACTION_ID}.response.json"
    for _ in range(100):
        if request_path.exists():
            break
        await asyncio.sleep(0.01)
    assert json.loads(request_path.read_text())["questions"][0]["question_id"] == "profile"
    response_path.write_text(
        json.dumps(
            {
                "version": 1,
                "interaction_id": str(INTERACTION_ID),
                "answers": {"profile": ["Search"]},
            }
        )
    )

    assert await task == {
        "status": "answered",
        "interaction_id": str(INTERACTION_ID),
        "answers": {"profile": ["Search"]},
    }
    assert not request_path.exists()
    assert not response_path.exists()
    assert json.loads((tmp_path / f"{INTERACTION_ID}.ack.json").read_text()) == {
        "version": 1,
        "interaction_id": str(INTERACTION_ID),
        "status": "answered",
        "answers": {"profile": ["Search"]},
    }


@pytest.mark.asyncio
async def test_rejects_reused_pending_interaction_id(tmp_path: Path) -> None:
    request_path = tmp_path / f"{INTERACTION_ID}.request.json"
    request_path.write_text("{}")
    with pytest.raises(RuntimeError, match="already pending"):
        await ask_user_question(tmp_path, _request())


def test_normalizes_question_text_at_the_broker_boundary() -> None:
    request = AskUserQuestionInput.model_validate(
        {
            "interaction_id": str(INTERACTION_ID),
            "questions": [
                {
                    "question_id": "profile",
                    "header": " Profile ",
                    "prompt": " Which profile? ",
                    "options": [{"label": " Base "}, {"label": " Search "}],
                }
            ],
        }
    )

    assert request.questions[0].header == "Profile"
    assert request.questions[0].prompt == "Which profile?"
    assert [option.label for option in request.questions[0].options] == ["Base", "Search"]


@pytest.mark.asyncio
async def test_expires_and_leaves_terminal_acknowledgement(tmp_path: Path) -> None:
    request = _request().model_copy(update={"timeout_seconds": 0})

    result = await ask_user_question(tmp_path, request)

    assert result["status"] == "expired"
    acknowledgement = json.loads((tmp_path / f"{INTERACTION_ID}.ack.json").read_text())
    assert acknowledgement["status"] == "expired"
    assert not (tmp_path / f"{INTERACTION_ID}.request.json").exists()


@pytest.mark.asyncio
async def test_rejects_invalid_response_and_acknowledges_failure(tmp_path: Path) -> None:
    task = asyncio.create_task(ask_user_question(tmp_path, _request()))
    request_path = tmp_path / f"{INTERACTION_ID}.request.json"
    for _ in range(100):
        if request_path.exists():
            break
        await asyncio.sleep(0.01)
    (tmp_path / f"{INTERACTION_ID}.response.json").write_text(
        json.dumps(
            {
                "version": 1,
                "interaction_id": str(INTERACTION_ID),
                "answers": {"profile": ["not-an-option"]},
            }
        )
    )

    with pytest.raises(RuntimeError, match="requires a declared option"):
        await task
    acknowledgement = json.loads((tmp_path / f"{INTERACTION_ID}.ack.json").read_text())
    assert acknowledgement["status"] == "rejected"
    assert "requires a declared option" in acknowledgement["error"]


def test_prepares_private_setgid_spool_and_cleans_stale_files(tmp_path: Path) -> None:
    broker_dir = tmp_path / "broker"
    prepare_interaction_broker(broker_dir, now_ms=1_000)
    assert broker_dir.stat().st_mode & 0o7777 == 0o2770

    stale_request = broker_dir / f"{INTERACTION_ID}.request.json"
    stale_response = broker_dir / f"{INTERACTION_ID}.response.json"
    stale_request.write_text(
        json.dumps(
            {
                "interaction_id": str(INTERACTION_ID),
                "expires_at_ms": 999,
            }
        )
    )
    stale_response.write_text("{}")

    prepare_interaction_broker(broker_dir, now_ms=1_000)
    assert not stale_request.exists()
    assert not stale_response.exists()
    acknowledgement = json.loads((broker_dir / f"{INTERACTION_ID}.ack.json").read_text())
    assert acknowledgement["status"] == "cancelled"


def test_rejects_invalid_question_shape() -> None:
    with pytest.raises(ValidationError):
        AskUserQuestionInput.model_validate(
            {
                "interaction_id": str(INTERACTION_ID),
                "questions": [
                    {
                        "question_id": "Bad ID",
                        "prompt": "Pick one",
                        "options": [{"label": "Only"}],
                    }
                ],
            }
        )
