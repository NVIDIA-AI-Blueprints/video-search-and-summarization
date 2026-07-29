#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate or atomically update one GPU client in a WireGuard config."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pathlib
import re
import subprocess
import tempfile
import time
import uuid

NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
KEY_PATTERN = re.compile(r"[A-Za-z0-9+/]{43}=")
ADDRESS_PATTERN = re.compile(
    r"10\.203\.142\.(?:10[0-9]|1[1-9][0-9]|2[0-4][0-9]|25[0-4])/32"
)
OPERATION_LOCK_TTL_SEC = 6 * 60 * 60
FLEET_LOCK_KEY = "__fleet_enrollment__"
BLOCK_PATTERN = re.compile(
    r"(?ms)^# BEGIN VSS CLIENT (?P<name>[A-Za-z0-9_.-]+)\n"
    r"(?P<body>.*?)"
    r"^# END VSS CLIENT (?P=name)\n?"
)


def peer_value(body: str, field: str) -> str:
    matches = re.findall(rf"(?m)^{re.escape(field)} = (\S+)$", body)
    if len(matches) != 1:
        raise SystemExit(f"client peer block requires exactly one {field}")
    return matches[0]


def validate(
    text: str,
    *,
    name: str,
    public_key: str,
    address: str,
) -> tuple[re.Match[str] | None, str | None, str | None]:
    blocks = list(BLOCK_PATTERN.finditer(text))
    begins = len(re.findall(r"(?m)^# BEGIN VSS CLIENT ", text))
    ends = len(re.findall(r"(?m)^# END VSS CLIENT ", text))
    if begins != len(blocks) or ends != len(blocks):
        raise SystemExit("WireGuard config contains a malformed client block")

    current: re.Match[str] | None = None
    current_key: str | None = None
    current_address: str | None = None
    for block in blocks:
        block_name = block.group("name")
        block_key = peer_value(block.group("body"), "PublicKey")
        block_address = peer_value(block.group("body"), "AllowedIPs")
        if block_name == name:
            if current is not None:
                raise SystemExit(f"duplicate WireGuard client name: {name}")
            current = block
            current_key = block_key
            current_address = block_address
            continue
        if block_address == address:
            raise SystemExit(
                f"overlay address {address} is already assigned to {block_name}"
            )
        if block_key == public_key:
            raise SystemExit(
                f"WireGuard public key is already assigned to {block_name}"
            )
    return current, current_key, current_address


def write_config(path: pathlib.Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".wg-vss.",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_operation_records(
    registry_path: pathlib.Path,
    *,
    now: int,
) -> dict[str, dict]:
    try:
        records = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        records = {}
    if not isinstance(records, dict):
        raise SystemExit("invalid WireGuard enrollment lock registry")
    return {
        worker: record
        for worker, record in records.items()
        if isinstance(record, dict)
        and isinstance(record.get("expires_at"), int)
        and record["expires_at"] > now
    }


def require_operation_owner(
    *,
    name: str,
    operation_id: str,
    registry_path: pathlib.Path,
) -> None:
    now = int(time.time())
    records = load_operation_records(registry_path, now=now)
    current = records.get(FLEET_LOCK_KEY)
    if (
        not current
        or current.get("operation_id") != operation_id
        or current.get("name") != name
    ):
        raise SystemExit(f"WireGuard enrollment lock is not owned for {name}")
    current["expires_at"] = now + OPERATION_LOCK_TTL_SEC
    write_config(
        registry_path,
        json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n",
    )


def peer_snapshot(
    path: pathlib.Path,
    *,
    name: str,
    public_key: str,
    address: str,
    operation_id: str,
    lock_path: pathlib.Path = pathlib.Path("/run/vss-wireguard-peer-update.lock"),
    registry_path: pathlib.Path = pathlib.Path(
        "/run/vss-wireguard-enrollment-locks.json"
    ),
) -> dict[str, str | bool]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require_operation_owner(
            name=name,
            operation_id=operation_id,
            registry_path=registry_path,
        )
        current, current_key, current_address = validate(
            path.read_text(encoding="utf-8"),
            name=name,
            public_key=public_key,
            address=address,
        )
        if current is None:
            return {"present": False}
        return {
            "present": True,
            "public_key": current_key or "",
            "address": current_address or "",
        }


def remove_runtime_peer(public_key: str, address: str, *, check: bool) -> None:
    subprocess.run(
        ["wg", "set", "wg-vss", "peer", public_key, "remove"],
        check=check,
    )
    removed_route = subprocess.run(
        ["ip", "route", "del", address, "dev", "wg-vss"],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and removed_route.returncode != 0:
        remaining = subprocess.run(
            ["ip", "route", "show", address, "dev", "wg-vss"],
            capture_output=True,
            check=True,
            text=True,
        )
        if remaining.stdout.strip():
            raise subprocess.CalledProcessError(
                removed_route.returncode,
                removed_route.args,
                output=removed_route.stdout,
                stderr=removed_route.stderr,
            )


def add_runtime_peer(public_key: str, address: str) -> None:
    subprocess.run(
        ["wg", "set", "wg-vss", "peer", public_key, "allowed-ips", address],
        check=True,
    )
    subprocess.run(
        ["ip", "route", "replace", address, "dev", "wg-vss"],
        check=True,
    )


def update_config(
    path: pathlib.Path,
    *,
    name: str,
    public_key: str,
    address: str,
    operation_id: str,
    expected_public_key: str | None = None,
    expected_address: str | None = None,
    remove: bool = False,
    lock_path: pathlib.Path = pathlib.Path("/run/vss-wireguard-peer-update.lock"),
    registry_path: pathlib.Path = pathlib.Path(
        "/run/vss-wireguard-enrollment-locks.json"
    ),
) -> None:
    if (expected_public_key is None) != (expected_address is None):
        raise SystemExit("expected peer requires both key and address")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require_operation_owner(
            name=name,
            operation_id=operation_id,
            registry_path=registry_path,
        )
        original = path.read_text(encoding="utf-8")
        current, old_key, old_address = validate(
            original,
            name=name,
            public_key=public_key,
            address=address,
        )
        if remove and current is None:
            return
        if remove and (old_key != public_key or old_address != address):
            raise SystemExit("refusing to remove a concurrently changed client peer")
        if not remove:
            if expected_public_key is None:
                if current is not None:
                    raise SystemExit("refusing to replace an unexpected client peer")
            elif (
                current is None
                or old_key != expected_public_key
                or old_address != expected_address
            ):
                raise SystemExit("client peer changed after its preimage snapshot")

        text = original
        if current is not None:
            text = (text[: current.start()] + text[current.end() :]).rstrip() + "\n"
        if not remove:
            text += (
                f"\n# BEGIN VSS CLIENT {name}\n"
                "[Peer]\n"
                f"PublicKey = {public_key}\n"
                f"AllowedIPs = {address}\n"
                f"# END VSS CLIENT {name}\n"
            )
        write_config(path, text)
        try:
            if old_key and old_address:
                remove_runtime_peer(old_key, old_address, check=True)
            if not remove:
                add_runtime_peer(public_key, address)
        except (OSError, subprocess.CalledProcessError):
            write_config(path, original)
            if not remove:
                remove_runtime_peer(public_key, address, check=False)
            if old_key and old_address:
                add_runtime_peer(old_key, old_address)
            raise


def restore_config(
    path: pathlib.Path,
    *,
    name: str,
    public_key: str,
    address: str,
    previous_public_key: str | None,
    previous_address: str | None,
    operation_id: str,
    lock_path: pathlib.Path = pathlib.Path("/run/vss-wireguard-peer-update.lock"),
    registry_path: pathlib.Path = pathlib.Path(
        "/run/vss-wireguard-enrollment-locks.json"
    ),
) -> None:
    if (previous_public_key is None) != (previous_address is None):
        raise SystemExit("rollback peer requires both key and address")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require_operation_owner(
            name=name,
            operation_id=operation_id,
            registry_path=registry_path,
        )
        original = path.read_text(encoding="utf-8")
        current, current_key, current_address = validate(
            original,
            name=name,
            public_key=public_key,
            address=address,
        )
        if previous_public_key and previous_address:
            validate(
                original,
                name=name,
                public_key=previous_public_key,
                address=previous_address,
            )
        if current is None:
            if previous_public_key is None:
                return
            raise SystemExit("refusing to restore over a missing client peer")
        if current_key == previous_public_key and current_address == previous_address:
            return
        if current_key != public_key or current_address != address:
            raise SystemExit("refusing to restore a concurrently changed client peer")

        text = (original[: current.start()] + original[current.end() :]).rstrip() + "\n"
        if previous_public_key and previous_address:
            text += (
                f"\n# BEGIN VSS CLIENT {name}\n"
                "[Peer]\n"
                f"PublicKey = {previous_public_key}\n"
                f"AllowedIPs = {previous_address}\n"
                f"# END VSS CLIENT {name}\n"
            )
        write_config(path, text)
        try:
            remove_runtime_peer(public_key, address, check=True)
            if previous_public_key and previous_address:
                add_runtime_peer(previous_public_key, previous_address)
        except (OSError, subprocess.CalledProcessError):
            write_config(path, original)
            if previous_public_key and previous_address:
                remove_runtime_peer(
                    previous_public_key,
                    previous_address,
                    check=False,
                )
            add_runtime_peer(public_key, address)
            raise


def update_operation_lock(
    *,
    name: str,
    operation_id: str,
    acquire: bool,
    lock_path: pathlib.Path = pathlib.Path("/run/vss-wireguard-peer-update.lock"),
    registry_path: pathlib.Path = pathlib.Path(
        "/run/vss-wireguard-enrollment-locks.json"
    ),
) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = int(time.time())
        records = load_operation_records(registry_path, now=now)
        current = records.get(FLEET_LOCK_KEY)
        if acquire:
            conflicting = [
                record
                for key, record in records.items()
                if key != FLEET_LOCK_KEY and record.get("operation_id") != operation_id
            ]
            if (
                current
                and (
                    current.get("operation_id") != operation_id
                    or current.get("name") != name
                )
            ) or conflicting:
                raise SystemExit("another WireGuard enrollment is already active")
            records = {
                FLEET_LOCK_KEY: {
                    "name": name,
                    "operation_id": operation_id,
                    "expires_at": now + OPERATION_LOCK_TTL_SEC,
                }
            }
        elif current:
            if (
                current.get("operation_id") != operation_id
                or current.get("name") != name
            ):
                raise SystemExit("refusing to unlock another enrollment")
            records.pop(FLEET_LOCK_KEY)
        elif any(
            record.get("operation_id") != operation_id for record in records.values()
        ):
            raise SystemExit("refusing to unlock another enrollment")
        write_config(
            registry_path,
            json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n",
        )


def renew_operation_lock(
    *,
    name: str,
    operation_id: str,
    lock_path: pathlib.Path = pathlib.Path("/run/vss-wireguard-peer-update.lock"),
    registry_path: pathlib.Path = pathlib.Path(
        "/run/vss-wireguard-enrollment-locks.json"
    ),
) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require_operation_owner(
            name=name,
            operation_id=operation_id,
            registry_path=registry_path,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=(
            "check",
            "snapshot",
            "apply",
            "remove",
            "restore",
            "lock",
            "renew",
            "unlock",
        ),
        required=True,
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--public-key")
    parser.add_argument("--address")
    parser.add_argument("--operation-id")
    parser.add_argument("--previous-public-key")
    parser.add_argument("--previous-address")
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=pathlib.Path("/etc/wireguard/wg-vss.conf"),
    )
    args = parser.parse_args()
    if not NAME_PATTERN.fullmatch(args.name):
        parser.error("invalid client name")
    try:
        args.operation_id = str(uuid.UUID(args.operation_id or ""))
    except ValueError:
        parser.error("operations require a valid operation ID")
    if args.mode not in {"lock", "renew", "unlock"}:
        if not args.public_key or not KEY_PATTERN.fullmatch(args.public_key):
            parser.error("invalid WireGuard public key")
        if not args.address or not ADDRESS_PATTERN.fullmatch(args.address):
            parser.error("invalid overlay address")
    if args.mode in {"apply", "restore"}:
        if bool(args.previous_public_key) != bool(args.previous_address):
            parser.error("peer mutation requires both previous peer fields or neither")
        if args.previous_public_key and not KEY_PATTERN.fullmatch(
            args.previous_public_key
        ):
            parser.error("invalid previous WireGuard public key")
        if args.previous_address and not ADDRESS_PATTERN.fullmatch(
            args.previous_address
        ):
            parser.error("invalid previous overlay address")
    return args


def main() -> None:
    args = parse_args()
    if args.mode in {"lock", "unlock"}:
        update_operation_lock(
            name=args.name,
            operation_id=args.operation_id,
            acquire=args.mode == "lock",
        )
        return
    if args.mode == "renew":
        renew_operation_lock(name=args.name, operation_id=args.operation_id)
        return
    if args.mode in {"check", "snapshot"}:
        snapshot = peer_snapshot(
            args.config,
            name=args.name,
            public_key=args.public_key,
            address=args.address,
            operation_id=args.operation_id,
        )
        if args.mode == "snapshot":
            print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return
    if args.mode in {"apply", "remove"}:
        update_config(
            args.config,
            name=args.name,
            public_key=args.public_key,
            address=args.address,
            operation_id=args.operation_id,
            expected_public_key=args.previous_public_key,
            expected_address=args.previous_address,
            remove=args.mode == "remove",
        )
    elif args.mode == "restore":
        restore_config(
            args.config,
            name=args.name,
            public_key=args.public_key,
            address=args.address,
            previous_public_key=args.previous_public_key,
            previous_address=args.previous_address,
            operation_id=args.operation_id,
        )


if __name__ == "__main__":
    main()
