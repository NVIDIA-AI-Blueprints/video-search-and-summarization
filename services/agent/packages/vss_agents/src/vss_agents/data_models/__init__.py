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
from abc import ABC

from langchain_core.output_parsers import PydanticOutputParser


class ParserMixin(ABC):
    _output_parser: PydanticOutputParser | None = None

    @classmethod
    def get_output_parser(cls) -> PydanticOutputParser:
        """Get the output parser for the model."""
        if not cls._output_parser:
            cls._output_parser = PydanticOutputParser(pydantic_object=cls)
        return cls._output_parser
