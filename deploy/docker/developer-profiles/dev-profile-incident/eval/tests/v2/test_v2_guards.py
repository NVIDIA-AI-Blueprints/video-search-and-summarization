# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Guards: import boundaries between layers, the offline network block, and V1's frozen artifacts."""

from __future__ import annotations

import ast
import json
import socket
import threading
from pathlib import Path

import pytest

from evalv2.core import frozen_v1
from evalv2.core.netguard import NetworkBlocked, no_network

PACKAGE = Path(__file__).resolve().parents[2] / "evalv2"
NETWORK_MODULES = {"httpx", "requests", "socket", "urllib.request", "aiohttp", "http.client", "boto3"}
V1_MODULES = {p.stem for p in PACKAGE.parent.glob("*.py")}  # eval's flat V1 modules


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _modules(subpackage: str) -> list[Path]:
    root = PACKAGE / subpackage
    return sorted(root.rglob("*.py")) if root.exists() else []


def test_core_imports_nothing_above_it_and_no_v1_module():
    for path in _modules("core"):
        for name in _imports(path):
            assert not name.startswith(("evalv2.adapters", "evalv2.tasks", "evalv2.cli")), (path.name, name)
            assert name.split(".")[0] not in V1_MODULES, (path.name, name)
            assert name.split(".")[0] not in {"httpx", "requests", "boto3"}, (path.name, name)


def test_adapters_never_import_tasks_the_cli_request_builders_or_v1_modules():
    for path in _modules("adapters"):
        for name in _imports(path):
            assert not name.startswith(("evalv2.tasks", "evalv2.cli", "evalv2.requests")), (path.name, name)
            assert name.split(".")[0] not in V1_MODULES, (path.name, name)


def test_request_builders_never_import_adapters_or_tasks():
    for path in _modules("requests"):
        for name in _imports(path):
            assert not name.startswith(("evalv2.adapters", "evalv2.tasks", "evalv2.cli")), (path.name, name)


def test_tasks_reach_adapters_only_through_the_base_protocol():
    for path in _modules("tasks"):
        for name in _imports(path):
            assert not name.startswith("evalv2.cli"), (path.name, name)
            if name.startswith("evalv2.adapters"):
                assert name == "evalv2.adapters.base", (path.name, name)


def test_datasets_stay_below_review_tasks_and_adapters_and_reach_v1_only_for_ground_truth():
    for path in _modules("datasets"):
        for name in _imports(path):
            assert not name.startswith(("evalv2.adapters", "evalv2.tasks", "evalv2.cli", "evalv2.review")), (
                path.name, name)
            assert name.split(".")[0] not in NETWORK_MODULES, (path.name, name)
            if name.split(".")[0] in V1_MODULES | {"eval_gt_schema"}:
                assert path.name == "ground_truth.py", (path.name, name)


def test_review_imports_no_model_client_task_or_v1_module():
    for path in _modules("review"):
        for name in _imports(path):
            assert not name.startswith(("evalv2.adapters", "evalv2.tasks", "evalv2.cli")), (path.name, name)
            assert name.split(".")[0] not in NETWORK_MODULES | V1_MODULES, (path.name, name)


V1_BRIDGES = {"tasks/incident_objects/v1.py", "tasks/chat/v1.py"}


def test_only_the_v1_bridge_reaches_v1_modules_from_tasks_and_verify():
    paths = _modules("tasks") + [PACKAGE / "verify_v1.py"]
    for path in paths:
        for name in _imports(path):
            if name.split(".")[0] in V1_MODULES | {"eval_run", "eval_run_lib", "eval_experiment", "eval_vlm_client"}:
                assert path.relative_to(PACKAGE).as_posix() in V1_BRIDGES, (path.name, name)
    assert not (_imports(PACKAGE / "tasks" / "judging.py") & NETWORK_MODULES)


def test_reference_tooling_imports_no_model_client_task_or_v1_module():
    for path in _modules("references"):
        for name in _imports(path):
            assert not name.startswith(("evalv2.adapters", "evalv2.tasks", "evalv2.cli")), (path.name, name)
            assert name.split(".")[0] not in NETWORK_MODULES | V1_MODULES, (path.name, name)


def test_crop_and_dashboard_scoring_is_offline_and_v1_free():
    for path in _modules("tasks/crops") + _modules("tasks/dashboard"):
        for name in _imports(path):
            assert name.split(".")[0] not in NETWORK_MODULES | V1_MODULES, (path.name, name)
            assert not name.startswith(("evalv2.adapters", "evalv2.cli")), (path.name, name)


def test_the_sql_harness_reaches_no_network_client_v1_module_or_task():
    for path in _modules("sql"):
        for name in _imports(path):
            assert name.split(".")[0] not in NETWORK_MODULES | V1_MODULES, (path.name, name)
            assert not name.startswith(("evalv2.adapters", "evalv2.tasks", "evalv2.cli")), (path.name, name)


def test_scorers_never_import_a_network_client():
    for path in _modules("tasks"):
        if path.stem.startswith("scorer"):
            assert not (_imports(path) & NETWORK_MODULES), path


def test_the_boundary_check_itself_sees_imports(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("import httpx\nfrom evalv2.tasks import base\nfrom . import sibling\n")
    assert _imports(sample) == {"httpx", "evalv2.tasks"}


def test_offline_scoring_cannot_reach_the_network():
    with no_network(), pytest.raises(NetworkBlocked, match="192.0.2.1"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.1)
    with no_network():
        sock = socket.socket()
        with pytest.raises(NetworkBlocked):
            sock.connect(("203.0.113.5", 80))
        sock.close()


def test_loopback_stays_allowed_under_the_guard():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    accepted = threading.Thread(target=lambda: server.accept()[0].close(), daemon=True)
    accepted.start()
    with no_network():
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    accepted.join(timeout=2)
    server.close()


def test_the_guard_restores_the_socket_functions():
    before = (socket.socket.connect, socket.create_connection)
    with no_network():
        pass
    assert (socket.socket.connect, socket.create_connection) == before


def test_v1_frozen_artifacts_are_unchanged():
    assert frozen_v1.drift() == []
    assert len(frozen_v1.recorded_hashes()) >= 80


def test_frozen_guard_detects_changed_new_and_removed_files(tmp_path):
    root = tmp_path / "profile"
    (root / "eval/experiments/configs").mkdir(parents=True)
    (root / "eval/experiments/configs/a.json").write_text("{}")
    (root / "eval/experiments/configs/b.json").write_text("{}")
    hashes = tmp_path / "hashes.json"
    hashes.write_text(json.dumps({"files": frozen_v1.current_hashes(root)}))
    assert frozen_v1.drift(root, hashes) == []
    (root / "eval/experiments/configs/a.json").write_text('{"edited": true}')
    (root / "eval/experiments/configs/b.json").unlink()
    (root / "eval/experiments/configs/c.json").write_text("{}")
    assert frozen_v1.drift(root, hashes) == [
        "frozen V1 file removed: eval/experiments/configs/b.json",
        "new file under a frozen V1 path: eval/experiments/configs/c.json",
        "frozen V1 file changed: eval/experiments/configs/a.json"]


def test_no_evalv2_source_file_is_ignored_by_git():
    """An ignored source file passes every local test but is missing from a fresh checkout (it happened once: the
    repository's "*checkpoint.*" rule dropped evalv2/core/checkpoint.py from the pull request)."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    files = [str(p) for p in sorted(PACKAGE.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
    done = subprocess.run(["git", "check-ignore", "--no-index", *files], capture_output=True, text=True,
                          cwd=PACKAGE, check=False)
    if done.returncode == 128:
        pytest.skip(f"not a git checkout: {done.stderr.strip()}")
    assert done.stdout.strip() == "", f"ignored by git, so missing from a checkout: {done.stdout.split()}"
