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

"""Throughput and latency benchmark for the VSS search pipeline.

Each module owns one stage and depends only on the ones below it, so a stage can
be read, tested or replaced without the rest:

``models``     dataclasses and the CSV column orders
``stats``      percentiles, without numpy
``console``    stdout plus the on-disk transcript
``budget``     the shared per-query sample allocator
``cli``        finding the vss CLI, building its argv, reading its NDJSON
``decompose``  query -> search arguments, via the deployment's own LLM
``routing``    decomposition -> which of the five CLI paths to run
``worker``     one load-generating thread
``plan``       config -> list of runs, and how big each one is
``resources``  Elasticsearch counts, Prometheus, pod logs
``corpus``     ingest and the gates that prove the index is populated
``lock``       cluster-wide single-run lock
``run``        one sweep cell, start to artifacts

The search interface is the ``vss`` CLI. The agent's ``POST /api/v1/search``
was removed from ``services/agent`` outright, and the agent does not shell out
to the CLI either -- they are separate surfaces -- so ``decompose`` and
``routing`` exist to do in this script what the agent used to do internally.

This package is self-contained on purpose. The accuracy harness next door
(``benchmark-video-search``) measures a different thing with a different corpus,
and a shared helper between the two would couple a change in one to a silent
change in the other's numbers.
"""

from __future__ import annotations

import importlib.util
import sys

#: Import name -> distribution name, for the only two third-party packages
#: this benchmark uses. Kept in step with the PEP 723 block at the top of
#: ``run_throughput.py``, which is what ``uv run`` reads.
THIRD_PARTY = {"yaml": "pyyaml", "requests": "requests"}


def require_third_party() -> None:
    """Refuse to start when a dependency is missing, naming all of them.

    Checked here because every module that needs one of these is imported
    through this package, so one check covers the entrypoint and the tests.
    Reporting the whole list at once matters more than it looks: discovering
    them one import failure at a time means one fix, one re-run, one more
    failure.

    Deliberately reports instead of installing. This script exists to measure
    a machine, and installing packages into that machine partway through a run
    changes what is being measured; a ``--user`` install would also shadow
    those packages for every other tool on the node, which is not a side
    effect a benchmark should have. ``uv run`` does the same job hermetically.
    """
    missing = sorted(
        dist for mod, dist in THIRD_PARTY.items()
        if importlib.util.find_spec(mod) is None
    )
    if not missing:
        return
    print(
        f"Missing Python package(s): {', '.join(missing)}\n"
        "\n"
        "Run the benchmark through uv, which builds a throwaway environment\n"
        "from the dependencies declared at the top of run_throughput.py:\n"
        "\n"
        "    uv run scripts/run_throughput.py --dry-run\n"
        "\n"
        "Or install them into whichever interpreter you are using:\n"
        "\n"
        f"    python3 -m pip install {' '.join(missing)}\n"
        "\n"
        "Not installed automatically on purpose: this script measures a\n"
        "machine, and changing that machine's packages mid-run changes what\n"
        "is being measured.",
        file=sys.stderr,
    )
    raise SystemExit(2)


require_third_party()
