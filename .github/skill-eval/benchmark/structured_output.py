# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Normalize structured JSON returned by an LLM."""

from __future__ import annotations

import json
import re
import sys


# Matches a JSON payload wrapped in an optional `json` Markdown fence.
_JSON_FENCE = re.compile(
    r"```(?:json)?[ \t]*\r?\n"
    r"(?P<payload>.*?)"
    r"\r?\n```",
    re.DOTALL | re.IGNORECASE,
)


def extract_json_payload(response: str) -> str:
    """Extract one unambiguous JSON payload from an LLM response."""
    text = response.strip()

    # Prefer the expected format: the entire response is raw JSON.
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # Otherwise, accept exactly one Markdown-fenced JSON payload.
    matches = list(_JSON_FENCE.finditer(text))
    if len(matches) == 1:
        match = matches[0]

        # Allow explanatory text before the fence, but nothing after it.
        if text[match.end():].strip():
            raise ValueError("unexpected content after JSON payload")

        # Return only the JSON inside the fence for domain validation.
        return match.group("payload").strip()
    if matches:
        raise ValueError("response must contain exactly one JSON payload")

    # Finally, accept one JSON object that consumes the response suffix.
    decoder = json.JSONDecoder()
    suffixes: list[str] = []
    for start, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            _, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not text[start + consumed :].strip():
            suffixes.append(text[start : start + consumed])

    if len(suffixes) != 1:
        raise ValueError("response must contain exactly one JSON payload")
    return suffixes[0]


def main() -> None:
    """Normalize one JSON-encoded response read from standard input."""
    response = json.loads(sys.stdin.read())
    if not isinstance(response, str):
        raise TypeError("structured response must be a string")
    print(extract_json_payload(response))


if __name__ == "__main__":
    main()
