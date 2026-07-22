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

# Import evaluate_patch module to auto-apply the patch
from . import evaluate_patch  # noqa: F401
from .customized_qa_evaluator.register import register_customized_qa_evaluator
from .customized_trajectory_evaluator.register import register_customized_trajectory_evaluator
from .report_evaluator.register import register_report_evaluator

__all__ = [
    "register_customized_qa_evaluator",
    "register_customized_trajectory_evaluator",
    "register_report_evaluator",
]
