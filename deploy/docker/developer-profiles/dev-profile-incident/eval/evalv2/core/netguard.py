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

"""Block network access during offline scoring.

``no_network()`` makes any attempt to connect a socket to a non-loopback address raise ``NetworkBlocked``, so a
scorer that tries to call a model, a judge or an embedder fails loudly instead of spending money or quietly
re-scoring with fresh calls. Loopback (and Unix sockets) stay allowed for local test servers.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class NetworkBlocked(RuntimeError):
    """A connection to a non-loopback address was attempted while the network guard was on."""


def _is_loopback(host: Any) -> bool:
    if host in ("localhost", "localhost.localdomain", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check(address: Any) -> None:
    if isinstance(address, (str, bytes)):  # a Unix socket path
        return
    host = address[0] if isinstance(address, tuple) and address else address
    if not _is_loopback(host):
        raise NetworkBlocked(f"network access to {host!r} is blocked during offline scoring")


@contextmanager
def no_network() -> Iterator[None]:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def connect(self: socket.socket, address: Any) -> Any:
        _check(address)
        return original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        _check(address)
        return original_connect_ex(self, address)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        _check(address)
        return original_create_connection(address, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = original_create_connection  # type: ignore[assignment]
