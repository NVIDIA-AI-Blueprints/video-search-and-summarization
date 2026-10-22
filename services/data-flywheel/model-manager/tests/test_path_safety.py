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

"""Containment tests for the model-name/version values that become directories.

``{repo}/{model_name}/{version}/`` is built from request body fields, and the
Model Manager both creates that directory and — during cleanup — removes it with
``shutil.rmtree``. So a value that does not stay put is an arbitrary-directory
write or delete reachable from a request body.

Both layers of the defence are exercised here, deliberately as separate cases.
The schema layer is what a caller hits; the activation layer is what still holds
when something constructs a request object directly or a future schema is
relaxed. A single test through the API would pass with either layer removed and
so would prove neither.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import app
from src.adapters.aoi.activation import ExplicitActivation
from src.api.schemas import ApplyRequest, CleanupRequest, PullRequest, UnloadRequest
from src.config import settings
from src.core.errors import ModelManagerError

# Values that must never reach a filesystem join. Each breaks
# ``{repo}/{name}/{version}`` differently: traversal climbs out, an absolute
# value discards the root, a separator smuggles a level in, and an empty value
# removes one — pointing a per-version delete at the whole model's directory.
# Percent-encoded traversal (``..%2f..``) is deliberately absent: these arrive as
# JSON body values and nothing decodes them.
UNSAFE = [
    "..",
    "../..",
    "../../etc",
    ".",
    "/etc",
    "/etc/passwd",
    "a/b",
    "a\\b",
    "",
    "sub/../../escape",
]


# ---------------------------------------------------------------- schema layer
@pytest.mark.parametrize("value", UNSAFE)
@pytest.mark.parametrize("field", ["model_name", "version"])
def test_cleanup_request_rejects_unsafe_path_components(field, value):
    """Cleanup is the destructive verb, so its fields are pinned hardest."""
    payload = {"model_name": "m", "version": "1", field: value}
    with pytest.raises(ValidationError):
        CleanupRequest(**payload)


@pytest.mark.parametrize("value", UNSAFE)
@pytest.mark.parametrize("field", ["model_name", "version"])
def test_unload_request_rejects_unsafe_path_components(field, value):
    """Unload deletes nothing, but ``version`` is still joined into
    ``{repo}/{name}/{version}`` by the inventory read it performs.
    """
    payload = {"model_name": "m", "version": "1", field: value}
    with pytest.raises(ValidationError):
        UnloadRequest(**payload)


@pytest.mark.parametrize("value", UNSAFE)
@pytest.mark.parametrize("field", ["model_name", "version"])
def test_pull_request_rejects_unsafe_path_components(field, value):
    """Pull creates the directory, so it needs the same constraint as cleanup.

    Constraining only the destructive verb would leave a staging call able to
    write an artifact anywhere the process can reach.
    """
    payload = {
        "model_name": "m",
        "version": "1",
        "artifact_uri": "file:///a",
        "format": "onnx",
        "sha256": "abc",
        field: value,
    }
    with pytest.raises(ValidationError):
        PullRequest(**payload)


@pytest.mark.parametrize("value", UNSAFE)
@pytest.mark.parametrize("field", ["model_name", "version"])
def test_apply_request_rejects_unsafe_path_components(field, value):
    """Apply does not touch the filesystem today, but it names the same version.

    Left unconstrained it would be the one way to get a hostile value into the
    manager, ready for whichever future code path joins it into a path.
    """
    payload = {"model_name": "m", "version": "1", "control": "full_switch", field: value}
    with pytest.raises(ValidationError):
        ApplyRequest(**payload)


@pytest.mark.parametrize(
    ("name", "version"),
    [("aoi-secondary-inspection", "1.4.0"), ("changenet_classify", "2"), ("m", "v1.0.0-rc.1")],
)
def test_any_value_safe_as_a_directory_name_is_accepted_here(name, version):
    """This layer asks only "is it safe as a path", not "will a backend serve it".

    Values like ``1.4.0`` and ``v1.0.0-rc.1`` are accepted deliberately even though
    the AOI backend requires an integer version. ``schemas.py`` is the frozen,
    backend-agnostic contract and the adapter registry exists so other serving
    backends can sit behind it, so a Triton-specific rule belongs in the AOI
    adapter — where ``test_aoi_adapter.py`` pins it — and not here.
    """
    assert CleanupRequest(model_name=name, version=version).version == version


def test_unload_endpoint_rejects_traversal_with_422_before_reaching_the_manager():
    """The same boundary rejection over real HTTP, for the new route.

    ``raise_server_exceptions=False`` so a route that rejects for the wrong
    reason shows up as a 500 rather than as a test error.
    """
    response = TestClient(app, raise_server_exceptions=False).post(
        "/v1/models/unload", json={"model_name": "m", "version": "../../etc"}
    )
    assert response.status_code == 422


def test_cleanup_endpoint_rejects_traversal_with_422_before_reaching_the_manager():
    """The rejection happens at the boundary, so no handler code runs at all.

    Asserted over real HTTP because that is the path an attacker has; a
    unit-level schema test cannot show that the app wires the constraint in.
    """
    response = TestClient(app).post(
        "/v1/models/cleanup", json={"model_name": "m", "version": "../../etc"}
    )
    assert response.status_code == 422


# ------------------------------------------------------------ activation layer
@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A model repo with a populated version and a sibling directory outside it.

    The outsider is what the assertions are really about: it stands in for
    anything on the volume that a traversal could reach, and every case below
    checks it is still there afterwards.
    """
    repo_root = tmp_path / "repo"
    (repo_root / "m" / "1").mkdir(parents=True)
    (repo_root / "m" / "1" / "model.pth").write_bytes(b"weights")
    outsider = tmp_path / "outside"
    outsider.mkdir()
    (outsider / "precious.txt").write_bytes(b"do not delete")
    monkeypatch.setattr(settings, "model_repo_path", str(repo_root))
    return repo_root, outsider


@pytest.mark.parametrize("version", ["..", "../../outside", "/etc", "a/b", ""])
def test_delete_refuses_a_version_that_escapes_the_repo_root(repo, version):
    """``delete`` ends in ``rmtree``, so this is the case that actually destroys.

    Includes the empty version because it is the subtlest: it collapses the join
    to the model's own directory, so a request to retire one version would take
    every version with it.
    """
    repo_root, outsider = repo
    activation = ExplicitActivation(inference=object())

    with pytest.raises(ModelManagerError) as excinfo:
        activation.delete("m", version)

    assert excinfo.value.code == "bad_request"
    assert excinfo.value.retryable is False
    assert excinfo.value.http_status == 400
    assert (outsider / "precious.txt").exists(), "a path outside the repo root was deleted"
    assert (repo_root / "m" / "1" / "model.pth").exists()


@pytest.mark.parametrize("name", ["..", "../..", "/etc", "a/b", ""])
def test_delete_refuses_a_model_name_that_escapes_the_repo_root(repo, name):
    repo_root, outsider = repo
    activation = ExplicitActivation(inference=object())

    with pytest.raises(ModelManagerError) as excinfo:
        activation.delete(name, "1")

    assert excinfo.value.code == "bad_request"
    assert (outsider / "precious.txt").exists()
    assert (repo_root / "m" / "1" / "model.pth").exists()


def test_delete_refuses_a_version_directory_symlinked_out_of_the_repo(repo):
    """The case no string check can catch: a valid-looking name that resolves out.

    Both sides are resolved before the containment test precisely so a symlinked
    version directory cannot be used to hand ``rmtree`` a target elsewhere.
    """
    repo_root, outsider = repo
    (repo_root / "m" / "escape").symlink_to(outsider, target_is_directory=True)
    activation = ExplicitActivation(inference=object())

    with pytest.raises(ModelManagerError) as excinfo:
        activation.delete("m", "escape")

    assert excinfo.value.code == "bad_request"
    assert (outsider / "precious.txt").exists()


@pytest.mark.parametrize("version", ["..", "../../outside", "/etc", "a/b", ""])
def test_stage_refuses_to_create_a_directory_outside_the_repo_root(repo, version):
    """Refused before the ``mkdir``, so nothing is created and nothing downloaded."""
    repo_root, outsider = repo
    activation = ExplicitActivation(inference=object())

    with pytest.raises(ModelManagerError) as excinfo:
        activation.stage_and_load("m", version, "file:///nonexistent", "abc")

    assert excinfo.value.code == "bad_request"
    assert (outsider / "precious.txt").exists()
    assert sorted(p.name for p in repo_root.iterdir()) == ["m"]
    assert sorted(p.name for p in (repo_root / "m").iterdir()) == ["1"]


def test_delete_still_removes_a_legitimate_version(repo):
    """The guard must not break the operation it protects."""
    repo_root, _ = repo
    ExplicitActivation(inference=object()).delete("m", "1")
    assert not (repo_root / "m" / "1").exists()
    assert (repo_root / "m").exists()
