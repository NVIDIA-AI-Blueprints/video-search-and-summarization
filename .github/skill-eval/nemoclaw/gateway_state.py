# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Claim a per-leg NemoClaw namespace without modifying existing registries."""

import json
from pathlib import Path
import re
import socket
import sys


def claim(owner: str, ports: list[int], home: Path) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("invalid eval gateway owner")
    if len(ports) != 3 or len(set(ports)) != 3 or any(not 1024 <= p <= 65535 for p in ports):
        raise ValueError("gateway, dashboard and relay need three distinct non-privileged ports")
    if ports[0] == 8080:
        raise ValueError("eval gateway must not use the shared default registry on port 8080")
    root = home / ".nemoclaw" / "gateways" / str(ports[0])
    if root.resolve() != root.absolute():
        raise ValueError("eval gateway namespace must not contain symlinks")
    receipt = root / "skill-eval-owner.json"
    expected = {"owner": owner, "ports": ports}
    if receipt.exists():
        if json.loads(receipt.read_text()) != expected:
            raise ValueError("gateway namespace belongs to a different evaluation")
        return
    if root.exists() and any(root.iterdir()):
        raise ValueError("gateway namespace already contains unowned NemoClaw state")
    # The coordinator holds the worker lock through the entire leg. Refuse an
    # occupied port rather than stopping a listener whose ownership is unknown.
    listeners = []
    try:
        for port in ports:
            listener = socket.socket()
            listeners.append(listener)
            listener.bind(("0.0.0.0", port))
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with receipt.open("x") as output:
            receipt.chmod(0o600)
            json.dump(expected, output)
    finally:
        for listener in listeners:
            listener.close()


if __name__ == "__main__":
    claim(sys.argv[1], [int(value) for value in sys.argv[2:]], Path.home())
    print("NemoClaw eval gateway namespace claimed")
