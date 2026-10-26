# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Namespace admission and portable benchmark source identity."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks import identity as bundle_source
from benchmarks.common import environment, import_origins, software_hash

ROOT = Path(__file__).resolve().parents[1]


def test_checkout_identity_binds_shell_and_recipe():
    import vss

    assert vss.__file__ is None
    assert (
        import_origins(ROOT, required=("ingestion", "summarization"))["mode"]
        == "checkout"
    )
    files = {n: bundle_source.raw(ROOT / n) for n in bundle_source.source_names(ROOT)}
    assert bundle_source.software_sha(files) == software_hash(ROOT)
    assert environment().source_identity_version == bundle_source.IDENTITY_VERSION
    for name in ("benchmarks/run_perf.sh", "benchmarks/matrix/recipe.toml"):
        changed = dict(files)
        changed[name] += b"\n# drift\n"
        assert bundle_source.software_sha(changed) != software_hash(ROOT)


@pytest.mark.parametrize(
    "name",
    [
        "src/vss/observability/context.py",
        "src/vss/video_processing/execution.py",
        "packages/ingestion/src/vss/ingestion/service.py",
        "packages/summarization/src/vss/summarization/service.py",
        *bundle_source.PROJECT_INPUTS,
        *bundle_source.GUARD_INPUTS,
        "packages/ingestion/native/cuda_ipc_broker/cuda_stream_sync.h",
    ],
)
def test_component_or_packaging_mutation_invalidates_identity(name):
    files = {
        key: bundle_source.raw(ROOT / key) for key in bundle_source.source_names(ROOT)
    }
    before = bundle_source.software_sha(files)
    files[name] += b"\n# identity mutation\n"
    assert bundle_source.software_sha(files) != before


def test_symlinked_source_ancestor_is_rejected(tmp_path, monkeypatch):
    import benchmarks.common as common

    real = tmp_path / "real"
    real.mkdir()
    (real / "module.py").write_text("pass\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/vss").symlink_to(real, target_is_directory=True)
    for name in (*bundle_source.PROJECT_INPUTS, *bundle_source.GUARD_INPUTS):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    monkeypatch.setattr(common, "import_origins", lambda _: {"mode": "checkout"})
    with pytest.raises(ValueError, match="source_symlink_ancestor"):
        software_hash(tmp_path)


@pytest.mark.parametrize("monolith", [False, True])
def test_stale_monolith_or_unexpected_namespace_portion_is_rejected(tmp_path, monolith):
    extra = tmp_path / "vss"
    if monolith:
        shutil.copytree(
            ROOT / "src/vss", extra, ignore=shutil.ignore_patterns("__pycache__")
        )
        (extra / "__init__.py").write_text("# old regular package\n")
    else:
        extra.mkdir()
        (extra / "unadmitted.py").write_text("pass\n")
    code = (
        "from pathlib import Path; from benchmarks.common import import_origins; "
        f"import_origins(Path({str(ROOT)!r}))"
    )
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert (
        "namespace_package_required" if monolith else "mixed_namespace_origins"
    ) in result.stderr


def test_hashing_without_unix_open_flags_preserves_binary_and_symlink_guards(
    tmp_path, monkeypatch
):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)
    source = tmp_path / "input.py"
    source.write_bytes(b"a\r\nb\x1a\x00")
    assert bundle_source.raw(source) == b"a\r\nb\x1a\x00"
    link = tmp_path / "link.py"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="regular_file_required"):
        bundle_source.raw(link)
    assert len(software_hash(ROOT)) == 64
