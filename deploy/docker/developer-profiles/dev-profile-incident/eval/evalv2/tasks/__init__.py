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
"""Evaluation dimensions: one package per task, each a runner plus scorers (registered in ``base.TASKS``)."""

from evalv2.tasks.base import TASKS, PredictionRecord, Scorer, ScoreRecord, TaskRunner, WorkItem, register_task

__all__ = ["TASKS", "PredictionRecord", "ScoreRecord", "Scorer", "TaskRunner", "WorkItem", "register_task"]
