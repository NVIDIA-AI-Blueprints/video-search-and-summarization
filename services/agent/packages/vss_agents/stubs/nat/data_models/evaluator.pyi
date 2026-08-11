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

from typing import Any

from pydantic import BaseModel

from .common import BaseModelRegistryTag
from .common import TypedBaseModel

class EvalInputItem(BaseModel):
    id: Any
    input_obj: Any
    expected_output_obj: Any
    output_obj: Any = ...
    expected_trajectory: list[Any] = ...
    trajectory: list[Any] = ...
    full_dataset_entry: Any = ...

class EvalOutputItem(BaseModel):
    id: Any
    score: Any
    reasoning: Any
    error: str | None = ...

class EvalOutput(BaseModel):
    average_score: Any
    eval_output_items: list[Any]

class EvalInput(BaseModel):
    eval_input_items: list[EvalInputItem]

class EvaluatorBaseConfig(TypedBaseModel, BaseModelRegistryTag): ...
