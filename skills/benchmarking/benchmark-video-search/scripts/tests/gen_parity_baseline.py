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

"""Capture the golden parity fixture from the CURRENT (pre-refactor) runner.

Run this ONCE before the refactor and commit the result::

    python3 scripts/tests/gen_parity_baseline.py

It drives ``run_eval_flows.run_evaluation`` with the shared fake backend and
decomposer, strips wall-clock fields, and writes ``fixtures/parity_baseline.json``.
After the refactor, ``test_pipeline_parity.py`` must reproduce this fixture.

Regenerate it ONLY when the output shape is intentionally changed (e.g. Phase 3
adds a ``scoring`` block); record that reason in the commit message.
"""

# isort: skip_file

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# scripts/tests -> scripts, so `import run_eval_flows` and `import flows` work.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _parity_harness as h
import run_eval_flows as rf

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "parity_baseline.json"


def main() -> None:
        with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                h.build_dataset(tmp_path)
                h.register_dataset()
                output = rf.run_evaluation(
                        query_backend=h.make_backend(),
                        data_dir=tmp_path,
                        dataset=h.DATASET,
                        subset=h.SUBSET,
                        output_file=str(tmp_path / "out.json"),
                        run_name="parity",
                        concurrency=1,
                        upload_stats=None,
                        ingest_description={
                                "flow": "fake-ingest",
                                "ingest_proof": "none",
                        },
                        readiness=None,
                        index_probe=None,
                        cleared=None,
                        vst_url=None,
                        decomposer=h.make_decomposer(),
                        decompose_fallback=None,
                        tolerate_unanswered=1,
                        use_dataset_decompositions=True,
                )
        cleaned = h.strip_nondeterministic(output)
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(cleaned, indent=2))
        print(f"wrote {FIXTURE}")
        print(
                f"  queries scored: {cleaned['summary']['total_queries']}, "
                f"attempted: {cleaned['summary']['queries_attempted']}"
        )


if __name__ == "__main__":
        main()
