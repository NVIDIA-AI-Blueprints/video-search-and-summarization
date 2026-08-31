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

"""Reading a transport credential from somewhere that is not the config file.

Not specific to any broker. The problem it solves is a property of how this
service is deployed rather than of what it connects to: the only place a plain
``password`` key can come from is the rendered service config, which is a
ConfigMap under Helm and a bind-mounted file under Compose, and neither of those
is a secret. Any transport that authenticates has the same problem and would
otherwise grow its own copy of this.
"""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def resolve_secret(cfg: Dict[str, Any], name: str,
                   component: str = "Redis") -> Optional[str]:
    """Read a credential from a file or environment variable before the config.

    ``<name>_file`` reads a mounted Secret and ``<name>_env`` reads an injected
    environment variable, so the credential never has to appear in non-secret
    configuration.

    Precedence is file, then environment, then the inline value. The inline key
    still works — existing deployments and local runs depend on it — but it is
    last so adding a Secret to one overrides it without also having to blank it.

    A named source that yields nothing falls back, because the value it would
    fall back *to* may well be the working credential. What it may not do is
    fall back to nothing: asking for a Secret and then connecting with no
    credential at all turns a missing mount into an authentication error on the
    first command, several layers from the mount that caused it. That case
    raises.

    Args:
        cfg: Resolved connection settings for one component.
        name: Credential key, e.g. ``password``.
        component: How to name the transport in messages, and — lowercased —
            the config prefix to quote back. Only ever reaches an operator's
            log, so that a message says which connection failed to authenticate
            when a deployment has more than one.

    Raises:
        ValueError: A ``_file`` or ``_env`` source was named, produced no
            value, and there is no other credential to use.
    """
    problems = []

    path = cfg.get(f"{name}_file")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                # Trailing newline: `kubectl create secret --from-literal` and
                # `echo > file` both add one, and the server would reject it as
                # part of the password.
                secret = handle.read().strip()
            if secret:
                return secret
            problems.append(f"{name}_file '{path}' is empty")
        except OSError as exc:
            problems.append(f"{name}_file '{path}' could not be read: {exc}")

    env_name = cfg.get(f"{name}_env")
    if env_name:
        secret = (os.environ.get(str(env_name)) or "").strip()
        if secret:
            if problems:
                logger.warning(
                    "%s %s: %s; using %s_env instead",
                    component, name, "; ".join(problems), name,
                )
            return secret
        problems.append(f"{name}_env names '{env_name}' but it is unset or empty")

    inline = cfg.get(name) or None
    if problems:
        if inline:
            logger.warning(
                "%s %s: %s; falling back to the inline value",
                component, name, "; ".join(problems),
            )
        else:
            prefix = component.lower()
            raise ValueError(
                f"{component} {name} is unavailable: {'; '.join(problems)}. "
                f"Connecting without it would fail at the first command with an "
                f"authentication error naming neither. Fix the source, or remove "
                f"{prefix}.{name}_file / {prefix}.{name}_env if this instance "
                f"needs no {name}."
            )
    return inline
