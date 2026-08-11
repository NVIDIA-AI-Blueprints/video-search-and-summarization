# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import ast
from contextlib import suppress
import re
from typing import Any
import uuid

from langchain_core.exceptions import LangChainException


class ReActOutputParserError(ValueError, LangChainException):
    def __init__(
        self,
        observation: str | None = None,
        missing_action: bool = False,
        missing_action_input: bool = False,
        final_answer_and_action: bool = False,
    ) -> None:
        self.observation = observation
        self.missing_action = missing_action
        self.missing_action_input = missing_action_input
        self.final_answer_and_action = final_answer_and_action


def parse_function_calls(text: str) -> list[dict[str, Any]]:
    """
    Parse a list of function calls from a string like:
    "[video_caption(file_path='...', start_timestamp=5, ...), video_caption(file_path='...', start_timestamp=5, ...)]"
    """
    # Extract all function name and parameters matches

    text = text.strip()
    pattern = r"(\w+)\((.*?)\)"
    matches = re.findall(pattern, text)

    if not matches:
        raise ReActOutputParserError(
            observation=f"No function calls found in the output: {text}",
        )

    parsed_calls = []

    for function_name, params_str in matches:
        # Parse parameters
        params = {}
        if params_str.strip():
            # Split by comma, but be careful with quoted strings and nested structures
            param_pairs = []
            current_param = ""
            in_quotes = False
            quote_char = None
            brace_count = 0
            bracket_count = 0
            paren_count = 0

            for char in params_str:
                if char in ["'", '"'] and (not in_quotes or char == quote_char):
                    if not in_quotes:
                        in_quotes = True
                        quote_char = char
                    else:
                        in_quotes = False
                        quote_char = None
                elif not in_quotes:
                    if char == "{":
                        brace_count += 1
                    elif char == "}":
                        brace_count -= 1
                    elif char == "[":
                        bracket_count += 1
                    elif char == "]":
                        bracket_count -= 1
                    elif char == "(":
                        paren_count += 1
                    elif char == ")":
                        paren_count -= 1
                    elif char == "," and brace_count == 0 and bracket_count == 0 and paren_count == 0:
                        param_pairs.append(current_param.strip())
                        current_param = ""
                        continue

                current_param += char

            if current_param.strip():
                param_pairs.append(current_param.strip())

            # Parse each parameter
            for param in param_pairs:
                if "=" in param:
                    key, value = param.split("=", 1)
                    key = key.strip()
                    value = value.strip()

                    # Parse the value
                    if (value.startswith("'") and value.endswith("'")) or (
                        value.startswith('"') and value.endswith('"')
                    ):
                        value = value[1:-1]  # Remove quotes
                    else:
                        # Try to parse as Python literal first
                        with suppress(ValueError, SyntaxError):
                            value = ast.literal_eval(value)
                        # If that fails and it looks like JSON, try JSON parsing
                        if isinstance(value, str) and (value.startswith("{") or value.startswith("[")):
                            try:
                                import json

                                value = json.loads(value)
                            except (json.JSONDecodeError, ValueError):
                                pass  # Keep as string if JSON parsing fails

                    params[key] = value

        parsed_calls.append({"name": function_name, "args": params, "id": str(uuid.uuid4())})

    return parsed_calls
