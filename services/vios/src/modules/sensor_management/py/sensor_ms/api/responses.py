# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Response class enforcing the C++ parity quirk: JSON body, text/plain Content-Type.

The legacy C++ sensor service emits all JSON responses with `Content-Type: text/plain`
(swagger.yaml top note). Clients parse the body as JSON and ignore the header. We must
match this byte-for-byte, so the body is JSON-serialized but the media type is text/plain.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


class TextPlainJSONResponse(JSONResponse):
    """JSON-encoded body served with Content-Type: text/plain (C++ parity)."""

    media_type = "text/plain"
