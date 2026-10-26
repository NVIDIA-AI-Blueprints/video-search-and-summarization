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

"""Bounded scalar trace files and verified relocatable candidate bundles."""

from __future__ import annotations

import gzip
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from pydantic import Field

from vss.observability import METRIC_CATALOG, Observation

from .media import file_hash
from .results import Evidence

MAX_TRACE_BYTES = 64 * 1024**2
MAX_RAW_BYTES = 128 * 1024**2
MAX_RECORDS = 100000
MAX_BUNDLE_BYTES = 256 * 1024**2


class TraceReference(Evidence):
    name: str = Field(pattern=r"^traces/[a-f0-9]{32}\.jsonl\.gz$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0, le=MAX_TRACE_BYTES)
    records: int = Field(ge=0, le=MAX_RECORDS)


class MetricAggregate(Evidence):
    count: int = Field(gt=0, le=MAX_RECORDS)
    sum: float | None = Field(default=None, ge=0)
    unsuccessful: int = Field(ge=0, le=MAX_RECORDS)
    unavailable: int = Field(ge=0, le=MAX_RECORDS)


def aggregate(records: tuple[Observation, ...]) -> dict[str, MetricAggregate]:
    groups: dict[str, list[Observation]] = {}
    for event in records:
        groups.setdefault(event.name, []).append(event)
    return {
        name: MetricAggregate(
            count=len(events),
            sum=sum(e.value or 0 for e in events)
            if all(e.value is not None for e in events)
            else None,
            unsuccessful=sum(e.status != "ok" for e in events),
            unavailable=sum(e.availability != "measured" for e in events),
        )
        for name, events in groups.items()
    }


def write_trace(root: Path, records: tuple[Observation, ...]) -> TraceReference:
    if len(records) > MAX_RECORDS:
        raise ValueError("trace record limit")
    name = "traces/" + uuid.uuid4().hex + ".jsonl.gz"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    try:
        raw_bytes = 0
        with temporary.open("xb") as stream:
            with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as compressed:
                for record in records:
                    raw = record.model_dump_json().encode() + b"\n"
                    raw_bytes += len(raw)
                    if len(raw) > 8192 or raw_bytes > MAX_RAW_BYTES:
                        raise ValueError("trace decompressed limit")
                    compressed.write(raw)
                    if stream.tell() > MAX_TRACE_BYTES:
                        raise ValueError("trace compressed limit")
        size = temporary.stat().st_size
        if size > MAX_TRACE_BYTES:
            raise ValueError("trace compressed limit")
        temporary.replace(path)
        return TraceReference(
            name=name, sha256=file_hash(path), bytes=size, records=len(records)
        )
    finally:
        temporary.unlink(missing_ok=True)


def read_trace(root: Path, ref: TraceReference) -> tuple[Observation, ...]:
    path = root / ref.name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("trace escapes bundle")
    if path.stat().st_size != ref.bytes or file_hash(path) != ref.sha256:
        raise ValueError("trace hash/size mismatch")
    events = []
    raw_bytes = 0
    with gzip.open(path, "rb") as stream:
        while line := stream.readline(8193):
            raw_bytes += len(line)
            if (
                len(line) > 8192
                or raw_bytes > MAX_RAW_BYTES
                or len(events) >= MAX_RECORDS
            ):
                raise ValueError("trace decompression/record limit")
            events.append(Observation.model_validate_json(line))
    if len(events) != ref.records:
        raise ValueError("trace record count mismatch")
    return tuple(events)


def verify_trace(
    root: Path, ref: TraceReference, expected: dict[str, MetricAggregate]
) -> tuple[Observation, ...]:
    events = read_trace(root, ref)
    if aggregate(events) != expected or not set(expected).issubset(METRIC_CATALOG):
        raise ValueError("trace differs from reported aggregates")
    return events


def bundle_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("bundle symlink")
        if path.is_file():
            total += path.stat().st_size
            if total > MAX_BUNDLE_BYTES:
                raise ValueError("bundle byte limit")
    return total


def verify_inventory(root: Path, refs: list[TraceReference], *, complete: bool) -> None:
    if root.is_symlink():
        raise ValueError("bundle root symlink")
    names = [ref.name for ref in refs]
    if len(set(names)) != len(names):
        raise ValueError("duplicate trace reference")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if complete and actual != set(names):
        raise ValueError("bundle inventory differs from referenced traces")
    bundle_bytes(root)


@contextmanager
def reserve(output: Path):
    lock = output.with_suffix(output.suffix + ".lock")
    with lock.open("x") as stream:
        stream.write(uuid.uuid4().hex + "\n")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def publish_bundle(output: Path, staging: Path, result: Evidence) -> None:
    """Reserve publication and roll back an owned directory if JSON cannot seal."""
    destination = output.with_suffix(".artifacts")
    with reserve(output):
        if output.exists() or destination.exists():
            raise FileExistsError("candidate outputs cannot overwrite evidence")
        bundle_bytes(staging)
        data = result.model_dump_json(indent=2).encode() + b"\n"
        if len(data) > 8 * 1024**2:
            raise ValueError("result byte limit")
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        published = False
        try:
            staging.rename(destination)
            published = True
            os.link(temporary, output)
        except BaseException:
            if published:
                shutil.rmtree(destination)
            raise
        finally:
            temporary.unlink(missing_ok=True)


def publish_result(output: Path, result: Evidence, *, reserved: bool = False) -> None:
    """Atomic immutable scalar evidence, using the same reservation policy."""
    with nullcontext() if reserved else reserve(output):
        if output.exists():
            raise FileExistsError("result cannot overwrite evidence")
        data = result.model_dump_json(indent=2).encode() + b"\n"
        if len(data) > 8 * 1024**2:
            raise ValueError("result byte limit")
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        try:
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
