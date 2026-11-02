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

"""Judge results as data: a cache keyed by everything that decides a judgement, so scoring can be repeated offline.

A run (approval-gated) asks the judge through a :class:`RecordingJudge`, which appends each new result to a
:class:`JudgeCache`; an offline score reads the same cache through a :class:`CachedJudge`, which never calls
anything and reports a missing judgement as unscored, never as a fail. The key holds the judge model, its prompt
hash and settings, and the two texts, so a changed judge or prompt can never reuse an old result.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evalv2.core.hashing import canonical_json, sha256_json


@dataclass(frozen=True)
class JudgeVerdict:
    """One judgement: a score in [0, 1], or the reason there is none."""

    score: float | None
    error: str = ""
    raw: str | None = None  # the judge's reply text, kept in the cache for audit (it can quote the judged texts)

    @property
    def ok(self) -> bool:
        return self.score is not None and not self.error


@dataclass(frozen=True)
class JudgeIdentity:
    """What makes two judgements comparable: the model, the prompt's SHA-256 and the request settings."""

    model: str
    prompt_sha256: str
    settings: Mapping[str, Any]

    def key(self, expected: str | None, predicted: str | None) -> str:
        return sha256_json({"model": self.model, "prompt_sha256": self.prompt_sha256,
                            "settings": dict(self.settings), "expected": expected, "predicted": predicted})


Judge = Callable[[str | None, str | None], JudgeVerdict]


class JudgeCache:
    """An append-only JSONL file of judgements, one per key; a second, different result for a key is refused."""

    def __init__(self, path: Path, identity: JudgeIdentity) -> None:
        self.path = Path(path)
        self.identity = identity
        self._entries: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._entries[entry["key"]] = entry

    def get(self, expected: str | None, predicted: str | None) -> dict[str, Any] | None:
        return self._entries.get(self.identity.key(expected, predicted))

    def put(self, expected: str | None, predicted: str | None, score: float, raw: str | None = None) -> None:
        key = self.identity.key(expected, predicted)
        existing = self._entries.get(key)
        if existing is not None:
            if existing["score"] != score:
                raise ValueError(f"judge cache {key[:12]} already holds score {existing['score']}, not {score}")
            return
        entry = {"key": key, "score": score, "raw": raw, "model": self.identity.model}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical_json(entry) + b"\n")
        self._entries[key] = entry

    def __len__(self) -> int:
        return len(self._entries)


class CachedJudge:
    """Offline: the cached score, or ``not judged`` (unscored) when the cache has none; never calls a model."""

    def __init__(self, cache: JudgeCache) -> None:
        self.cache = cache
        self.misses: list[str] = []

    def __call__(self, expected: str | None, predicted: str | None) -> JudgeVerdict:
        entry = self.cache.get(expected, predicted)
        if entry is None:
            self.misses.append(self.cache.identity.key(expected, predicted))
            return JudgeVerdict(None, "not judged: no cached judgement for this pair")
        return JudgeVerdict(float(entry["score"]))


class RecordingJudge:
    """A run's judge: the cache first, else ``ask`` (a live judge client) with the new result appended."""

    def __init__(self, cache: JudgeCache, ask: Callable[[str | None, str | None], JudgeVerdict]) -> None:
        self.cache = cache
        self.ask = ask
        self.calls = 0

    def __call__(self, expected: str | None, predicted: str | None) -> JudgeVerdict:
        entry = self.cache.get(expected, predicted)
        if entry is not None:
            return JudgeVerdict(float(entry["score"]))
        self.calls += 1
        verdict = self.ask(expected, predicted)
        if verdict.ok and verdict.score is not None:
            self.cache.put(expected, predicted, verdict.score, raw=verdict.raw)
        return verdict


class SequenceJudge:
    """Replays stored judgements in call order, per text pair (verify-v1: a V1 record's own scores, no calls)."""

    def __init__(self, judgements: Iterable[tuple[str | None, str | None, float | None]]) -> None:
        self._queue: dict[tuple[str | None, str | None], deque[float | None]] = defaultdict(deque)
        for expected, predicted, score in judgements:
            self._queue[(expected, predicted)].append(score)

    def __call__(self, expected: str | None, predicted: str | None) -> JudgeVerdict:
        queue = self._queue.get((expected, predicted))
        if not queue:
            return JudgeVerdict(None, "no stored judgement for this pair")
        score = queue.popleft()
        return JudgeVerdict(score) if score is not None else JudgeVerdict(None, "stored judgement was unscored")

    def unused(self) -> int:
        return sum(len(q) for q in self._queue.values())
