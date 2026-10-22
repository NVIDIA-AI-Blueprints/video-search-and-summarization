# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""AOI (VSS image-model) pipeline — ``aoi_job``.

The DAG itself lives in :mod:`src.tasks.pipelines.deft_pipeline`; this class adds
only the job type it serves.
"""

from typing import ClassVar

from src.job_types import JobType
from src.tasks.pipelines.deft_pipeline import DeftPipeline


class AOIPipeline(DeftPipeline):
    """Pipeline orchestrator for the AOI (VSS) image-model flywheel.

    Runs the shared 8-stage DEFT DAG unchanged — see
    :class:`~src.tasks.pipelines.deft_pipeline.DeftPipeline`.
    """

    job_type: ClassVar[JobType] = JobType.AOI
