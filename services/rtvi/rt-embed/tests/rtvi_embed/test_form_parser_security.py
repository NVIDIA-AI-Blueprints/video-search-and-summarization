# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for urlencoded form parsing limits."""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import FastAPI, Form
from fastapi.testclient import TestClient


def _form_client() -> TestClient:
    app = FastAPI()

    @app.post("/files")
    async def add_file(
        purpose: Annotated[str, Form()],
        media_type: Annotated[str, Form()],
        filename: Annotated[str, Form()],
    ) -> dict[str, str]:
        return {
            "purpose": purpose,
            "media_type": media_type,
            "filename": filename,
        }

    return TestClient(app)


def test_urlencoded_form_is_accepted():
    client = _form_client()

    response = client.post(
        "/files",
        data={
            "purpose": "vision",
            "media_type": "video",
            "filename": "/tmp/input.mp4",
        },
    )

    assert response.status_code == 200


def test_urlencoded_form_rejects_too_many_fields():
    client = _form_client()
    fields = [
        ("purpose", "vision"),
        ("media_type", "video"),
        ("filename", "/tmp/input.mp4"),
    ]
    fields.extend((f"unused_{index}", "x") for index in range(1000))

    response = client.post(
        "/files",
        content=urlencode(fields),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400


def test_semicolon_only_urlencoded_body_is_not_split_into_fields():
    client = _form_client()

    response = client.post(
        "/files",
        content="a;" * 5000,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 422
