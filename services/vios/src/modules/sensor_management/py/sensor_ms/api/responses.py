# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Response class enforcing the C++ parity quirk: JSON body, text/plain Content-Type.

The legacy C++ sensor service emits all JSON responses with `Content-Type: text/plain`
(swagger.yaml top note). Clients parse the body as JSON and ignore the header, so the body is
JSON-serialized but the media type is text/plain.

The C++ service serializes with jsoncpp's StreamWriterBuilder (default: tab indentation and
`"key" : value` separators), i.e. pretty-printed output. We mirror that formatting here so the
human-readable shape matches the C++ responses (Starlette's default would emit compact single-line
JSON). Whitespace is not semantically significant -- clients parse the JSON either way.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse


class TextPlainJSONResponse(JSONResponse):
    """Pretty-printed JSON body served with Content-Type: text/plain (C++ jsoncpp parity)."""

    media_type = "text/plain"

    def render(self, content: Any) -> bytes:
        # indent="\t" + " : " key separator matches jsoncpp StreamWriterBuilder defaults.
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent="\t",
            separators=(",", " : "),
        ).encode("utf-8")
