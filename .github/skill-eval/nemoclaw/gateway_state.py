# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Claim a per-leg NemoClaw namespace without modifying existing registries."""

import argparse
import errno
import json
from pathlib import Path
import re
import socket
import subprocess


class NamespaceUnavailable(ValueError):
    """A valid candidate belongs to another job or has unowned state."""


def network_policy(owner: str, ports: list[int], home: Path, *, remove=False) -> None:
    """Allow the owned gateway port from Docker/loopback, preserving host policy."""
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("invalid eval gateway owner")
    if len(ports) != 3 or len(set(ports)) != 3 or any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or ports[0] == 8080:
        raise ValueError("invalid eval gateway ports")
    root = home / ".nemoclaw/gateways" / str(ports[0])
    if root.resolve() != root.absolute():
        raise ValueError("eval gateway namespace must not contain symlinks")
    namespace = root / "skill-eval-owner.json"
    if namespace.is_symlink() or json.loads(namespace.read_text()) != {"owner": owner, "ports": ports}:
        raise ValueError("gateway network policy requires the owned namespace")
    receipt = root / "network-policy.json"
    chain = "SE-NC-" + owner[:20]
    expected = {"owner": owner, "chain": chain, "port": ports[0]}
    if receipt.is_symlink() or (receipt.exists() and json.loads(receipt.read_text()) != expected):
        raise ValueError("gateway network policy has a different owner")
    if remove and not receipt.exists():
        return

    def iptables(*args, check=True):
        result = subprocess.run(
            ["sudo", "-n", "iptables", "-w", "5", *args],
            capture_output=True, text=True, timeout=30,
        )
        if check and result.returncode:
            raise RuntimeError("Cannot reconcile owned gateway network policy")
        return result

    jump = ("INPUT", "-p", "tcp", "--dport", str(ports[0]), "-j", chain)
    rules = [("-i", interface, "-j", "ACCEPT") for interface in ("lo", "docker0", "br+")]
    rules.append(("-j", "RETURN"))  # Other traffic keeps the existing INPUT/UFW policy.
    state = iptables("-S", chain, check=False)
    if state.returncode not in (0, 1):
        raise RuntimeError("Cannot inspect owned gateway network policy")
    if not receipt.exists():
        if state.returncode == 0:
            raise ValueError("Refusing to adopt an unowned gateway network policy")
        # Intent precedes mutation so interrupted installation remains removable.
        with receipt.open("x") as output:
            receipt.chmod(0o600)
            json.dump(expected, output)
    if state.returncode == 1:
        if remove:
            receipt.unlink()
            return
        iptables("-N", chain)
    linked = iptables("-C", *jump, check=False)
    if linked.returncode not in (0, 1):
        raise RuntimeError("Cannot inspect owned gateway network policy")
    if remove:
        if linked.returncode == 0:
            iptables("-D", *jump)
        iptables("-F", chain)
        iptables("-X", chain)
        receipt.unlink()
    elif linked.returncode == 1:
        if state.returncode == 0:
            iptables("-F", chain)
        for rule in rules:
            iptables("-A", chain, *rule)
        iptables("-I", *jump)
    else:
        for rule in rules:
            iptables("-C", chain, *rule)


def cleanup_network_policy(owner: str, home: Path) -> None:
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("invalid eval gateway owner")
    for receipt in (home / ".nemoclaw/gateways").glob("*/skill-eval-owner.json"):
        if receipt.is_symlink():
            continue
        try:
            value = json.loads(receipt.read_text())
        except ValueError:
            continue  # Foreign malformed registries are never adopted or changed.
        if isinstance(value, dict) and value.get("owner") == owner:
            network_policy(owner, value.get("ports", []), home, remove=True)


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
            raise NamespaceUnavailable("gateway namespace belongs to a different evaluation")
        return
    if root.exists() and any(root.iterdir()):
        raise NamespaceUnavailable("gateway namespace already contains unowned NemoClaw state")
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


def allocate(owner: str, preferred: list[int], home: Path, *, exact=False) -> list[int]:
    if not re.fullmatch(r"[a-f0-9]{64}", owner):
        raise ValueError("invalid eval gateway owner")
    if len(preferred) != 3 or len(set(preferred)) != 3 or any(not 1024 <= p <= 65535 for p in preferred) or preferred[0] == 8080:
        raise ValueError("invalid eval gateway ports")
    if exact:
        claim(owner, preferred, home)
        return preferred
    used = set()
    roots = home / ".nemoclaw/gateways"
    if roots.exists():
        if roots.resolve() != roots.absolute():
            raise ValueError("eval gateway namespaces must not contain symlinks")
        for root in sorted(roots.iterdir()):
            if not root.name.isdecimal():
                continue
            if root.is_symlink():
                used.add(int(root.name))
                continue
            if not root.is_dir() or not any(root.iterdir()):
                continue
            used.add(int(root.name))
            receipt = root / "skill-eval-owner.json"
            if not receipt.is_file() or receipt.is_symlink():
                continue
            try:
                recorded = json.loads(receipt.read_text())
            except ValueError:
                continue  # Preserve malformed foreign state; never adopt it.
            if not isinstance(recorded, dict):
                continue
            ports = recorded.get("ports")
            if not isinstance(ports, list) or any(type(p) is not int for p in ports):
                continue
            if recorded.get("owner") == owner:
                if not ports or str(ports[0]) != root.name:
                    raise ValueError("inconsistent owned gateway receipt")
                claim(owner, ports, home)
                return ports  # Retry keeps the previously selected namespace.
            used.update(ports)
    start = int(owner[:8], 16) % 3000
    candidates = [preferred] + [
        [21000 + 3 * ((start + offset) % 3000) + n for n in range(3)]
        for offset in range(3000)
    ]
    for ports in candidates:
        if used.intersection(ports):
            continue
        try:
            claim(owner, ports, home)
        except NamespaceUnavailable:
            continue
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise  # Permission, disk and transport errors are not collisions.
            continue
        return ports
    raise NamespaceUnavailable("no unused eval gateway namespace available")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("owner")
    parser.add_argument("ports", type=int, nargs="*")
    parser.add_argument("--allocate", action="store_true")
    parser.add_argument("--exact", action="store_true")
    parser.add_argument("--cleanup-firewall", action="store_true")
    args = parser.parse_args()
    if args.cleanup_firewall:
        cleanup_network_policy(args.owner, Path.home())
    elif args.allocate:
        ports = allocate(args.owner, args.ports, Path.home(), exact=args.exact)
        print("NEMOCLAW_GATEWAY_ALLOCATION=" + json.dumps({"owner": args.owner, "ports": ports}))
    else:
        claim(args.owner, args.ports, Path.home())
        network_policy(args.owner, args.ports, Path.home())
        artifact = Path("/logs/artifacts/nemoclaw/gateway_namespace.json")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps({"owner": args.owner, "ports": args.ports, "registry_isolated": True}, indent=2) + "\n")
        print("NemoClaw eval gateway namespace claimed")
