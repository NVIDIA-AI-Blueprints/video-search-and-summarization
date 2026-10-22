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
"""Idempotent NMP 26.03 workspace bootstrap.

In NMP 26.03 a *workspace* is a first-class prerequisite resource: filesets,
deployments, customization jobs and evaluation jobs all live under
``/workspaces/<workspace>/...`` and the platform rejects operations (HTTP 422
"Workspace '<ws>' does not exist") until the workspace has been created.

DFW namespaces every resource under ``nmp_config.workspace`` but nothing on the
platform creates that workspace for us, so we ensure it exists at the earliest
NMP entry points (dataset upload and model deployment, which both run before
customization/evaluation in the workflow DAG).
"""

from __future__ import annotations

from typing import Any

from nemo_platform import APIStatusError

from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.nemo.workspace")


def ensure_workspace(nemo_client: Any, workspace: str) -> None:
    """Create the NMP workspace if it doesn't already exist (idempotent).

    ``exist_ok=True`` makes the SDK call a no-op when the workspace is already
    present; a 409/422 from older/edge behaviours is treated as "already exists"
    and any other error is surfaced.
    """
    try:
        nemo_client.workspaces.create(
            name=workspace,
            exist_ok=True,
            description=f"DFW workspace: {workspace}",
            wait_role_propagation=True,
        )
        logger.info(f"Ensured NMP workspace '{workspace}' exists")
    except APIStatusError as e:
        # 409 is unambiguous "already exists". A 422 can mean that too, but it also
        # covers invalid name/payload/permissions — only treat the duplicate case as
        # idempotent success so genuine failures still surface.
        status = getattr(e, "status_code", None)
        msg = str(getattr(e, "message", e)).lower()
        if status == 409 or (status == 422 and ("already exists" in msg or "duplicate" in msg)):
            logger.info(f"Workspace '{workspace}' already exists, reusing")
            return
        logger.error(f"Failed to ensure workspace '{workspace}': {e}")
        raise


def ensure_secret(nemo_client: Any, workspace: str, name: str, value: str) -> str:
    """Create-or-update a workspace secret holding ``value`` and return its name.

    NMP 26.03 eval ``ModelParam`` authenticates a remote model only via
    ``api_key_secret`` (a workspace SecretRef), never a raw key. Callers that hold
    the key directly (e.g. the LLM judge, which also uses the raw key for its own
    reachability check) materialise it into a workspace secret with this helper and
    pass the returned name as ``api_key_secret``. Idempotent: updates in place if the
    secret already exists so a rotated key is picked up.
    """
    try:
        nemo_client.secrets.create(
            workspace=workspace,
            name=name,
            data=value,
            description=f"DFW managed secret: {name}",
        )
        logger.info(f"Created workspace secret '{workspace}/{name}'")
    except APIStatusError as e:
        status = getattr(e, "status_code", None)
        msg = str(getattr(e, "message", e)).lower()
        if status == 409 or (status == 422 and ("already exists" in msg or "duplicate" in msg)):
            try:
                nemo_client.secrets.update(name=name, workspace=workspace, data=value)
                logger.info(f"Updated existing workspace secret '{workspace}/{name}'")
            except Exception as ue:
                logger.warning(f"Secret '{workspace}/{name}' exists; reusing (update failed: {ue})")
        else:
            logger.error(f"Failed to ensure workspace secret '{workspace}/{name}': {e}")
            raise
    return name
