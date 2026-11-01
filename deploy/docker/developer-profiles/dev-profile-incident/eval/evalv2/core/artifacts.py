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

"""Append-only, content-addressed artifact store on the local filesystem.

Every request, response, prediction, judge verdict, embedding and score a V2 run produces is an artifact,
addressed by the SHA-256 of its canonical bytes. Nothing is ever overwritten: writing the same content again
returns the same reference, and a file whose bytes no longer match its address is reported as corrupt.

Layout under ``root``:

- ``objects/<kind>/<sha[:2]>/<sha>.json``: a JSON envelope ``{"kind", "payload"}``;
- ``objects/<kind>/<sha[:2]>/<sha>.bin``: a blob (for example an image), addressed by its raw bytes;
- ``runs/<run id>/index.jsonl``: the run's append-only record of which artifact answered which work item;
- ``runs/<run id>/SEALED``: present once the run is sealed; it holds the run manifest's reference.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalv2.core.hashing import canonical_json, sha256_bytes

KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(RuntimeError):
    """An artifact is missing, corrupt, or a write would break immutability."""


class SealedRunError(ArtifactError):
    """A sealed run is read-only."""


@dataclass(frozen=True, order=True)
class ArtifactRef:
    kind: str
    sha256: str

    def __post_init__(self) -> None:
        if not KIND_PATTERN.match(self.kind):
            raise ArtifactError(f"invalid artifact kind {self.kind!r}")
        if not _SHA_PATTERN.match(self.sha256):
            raise ArtifactError(f"invalid SHA-256 {self.sha256!r}")

    def __str__(self) -> str:
        return f"{self.kind}:{self.sha256}"

    @classmethod
    def parse(cls, text: str) -> ArtifactRef:
        kind, _, sha = text.partition(":")
        return cls(kind, sha)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, ref: ArtifactRef, suffix: str) -> Path:
        return self.root / "objects" / ref.kind / ref.sha256[:2] / f"{ref.sha256}{suffix}"

    def _write_once(self, ref: ArtifactRef, suffix: str, data: bytes) -> ArtifactRef:
        path = self._path(ref, suffix)
        if path.exists():
            if path.read_bytes() != data:
                raise ArtifactError(f"corrupt artifact {ref}: stored bytes do not match their address")
            return ref
        _atomic_write(path, data)
        return ref

    def put(self, kind: str, payload: Any) -> ArtifactRef:
        """Store a JSON payload; the same kind and payload always return the same reference."""
        data = canonical_json({"kind": kind, "payload": payload})
        return self._write_once(ArtifactRef(kind, sha256_bytes(data)), ".json", data)

    def put_blob(self, kind: str, data: bytes) -> ArtifactRef:
        """Store raw bytes (an image, a clip), addressed by their own SHA-256."""
        return self._write_once(ArtifactRef(kind, sha256_bytes(data)), ".bin", bytes(data))

    def get(self, ref: ArtifactRef) -> Any:
        path = self._path(ref, ".json")
        if not path.exists():
            raise ArtifactError(f"missing artifact {ref}")
        data = path.read_bytes()
        if sha256_bytes(data) != ref.sha256:
            raise ArtifactError(f"corrupt artifact {ref}: stored bytes do not match their address")
        envelope = json.loads(data)
        if envelope.get("kind") != ref.kind:
            raise ArtifactError(f"artifact {ref} holds kind {envelope.get('kind')!r}")
        return envelope["payload"]

    def get_blob(self, ref: ArtifactRef) -> bytes:
        path = self._path(ref, ".bin")
        if not path.exists():
            raise ArtifactError(f"missing blob {ref}")
        data = path.read_bytes()
        if sha256_bytes(data) != ref.sha256:
            raise ArtifactError(f"corrupt blob {ref}: stored bytes do not match their address")
        return data

    def exists(self, ref: ArtifactRef) -> bool:
        return self._path(ref, ".json").exists() or self._path(ref, ".bin").exists()

    def run(self, run_id: str) -> Run:
        return Run(self, run_id)


class Run:
    """One run's append-only index of work items and, once sealed, its manifest."""

    def __init__(self, store: ArtifactStore, run_id: str) -> None:
        if not RUN_ID_PATTERN.match(run_id):
            raise ArtifactError(f"invalid run id {run_id!r}")
        self.store = store
        self.run_id = run_id
        self.root = store.root / "runs" / run_id

    @property
    def index_path(self) -> Path:
        return self.root / "index.jsonl"

    @property
    def sealed(self) -> bool:
        return (self.root / "SEALED").exists()

    def record(self, key: str, ref: ArtifactRef, **fields: Any) -> None:
        """Append one line linking a work item to the artifact that answered it."""
        if self.sealed:
            raise SealedRunError(f"run {self.run_id} is sealed")
        line = {"key": key, "ref": str(ref), "at": datetime.now(UTC).isoformat(), **fields}
        self.root.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("ab") as handle:
            handle.write(canonical_json(line) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def records(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        return [json.loads(line) for line in self.index_path.read_text().splitlines() if line.strip()]

    def seal(self, manifest: dict[str, Any]) -> ArtifactRef:
        """Store the run manifest and make the run read-only. Sealing twice is refused."""
        if self.sealed:
            raise SealedRunError(f"run {self.run_id} is already sealed")
        manifest = {**manifest, "run_id": self.run_id, "index_sha256": self._index_sha256()}
        ref = self.store.put("run_manifest", manifest)
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.root / "manifest.json", canonical_json(manifest))
        _atomic_write(self.root / "SEALED", str(ref).encode())
        return ref

    def manifest_ref(self) -> ArtifactRef | None:
        marker = self.root / "SEALED"
        return ArtifactRef.parse(marker.read_text().strip()) if marker.exists() else None

    def verify(self) -> list[str]:
        """Problems with a sealed run: a changed index or a missing or corrupt artifact."""
        ref = self.manifest_ref()
        if ref is None:
            return [f"run {self.run_id} is not sealed"]
        problems: list[str] = []
        manifest = self.store.get(ref)
        if manifest.get("index_sha256") != self._index_sha256():
            problems.append(f"run {self.run_id}: index.jsonl changed after sealing")
        for record in self.records():
            try:
                artifact = ArtifactRef.parse(record["ref"])
                if self.store._path(artifact, ".bin").exists():
                    self.store.get_blob(artifact)
                else:
                    self.store.get(artifact)
            except ArtifactError as exc:
                problems.append(str(exc))
        return problems

    def _index_sha256(self) -> str:
        return sha256_bytes(self.index_path.read_bytes() if self.index_path.exists() else b"")
