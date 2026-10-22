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

"""ITS (Intelligent Transportation Systems) pipeline — ``its_job``.

The DAG itself lives in :mod:`src.tasks.pipelines.deft_pipeline`; this class adds
only the job type it serves.

A SIBLING of :class:`~src.tasks.pipelines.aoi_pipeline.AOIPipeline`, not a
subclass of it — matching ``DeftITSEvaluator``, which subclasses the neutral
``DeftEvaluator``. That distinction is load-bearing one layer over:
``DeftBackendProvider._is_aoi_family`` tests ``issubclass(cls,
DeftAOIEvaluator)``.
"""

from typing import ClassVar

from src.job_types import JobType
from src.tasks.pipelines.deft_pipeline import DeftPipeline


class ITSPipeline(DeftPipeline):
    """Pipeline orchestrator for the ITS flywheel.

    Runs the shared 8-stage DEFT DAG unchanged; everything that differs for ITS
    is resolved downstream from ``use_case``.
    """

    job_type: ClassVar[JobType] = JobType.ITS
