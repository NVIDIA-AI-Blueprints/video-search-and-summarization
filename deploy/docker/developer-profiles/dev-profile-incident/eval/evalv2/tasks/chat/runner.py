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

"""The chat task (J3): one work item per chat reference, corpus, chat subject and repeat; its budget worst case.

The config's ``tasks.chat.options``:

- ``references``: ``{"path", "sha256"}``, the RA2 records file under the data root, pinned by hash (the records
  are private and not yet a registered dataset, since human review may still edit them);
- ``corpora``: ``["oracle"]`` or ``["oracle", "system"]``; ``system``: the sealed Track B run the system corpus is
  read from (``store`` under the data root, ``run``, ``subject``, ``stage``, ``repeat``);
- ``tokens_bound``: the pessimistic tokens of one call, per call kind (``incident``, ``library_plan``,
  ``library_answer``): the prompt bound from the offline estimate plus the client's 1,024 output tokens.

Only the half's questions are planned: an incident question about one of the half's clips, a library question
whose ``params.corpus`` is the half (each half is its own library). An incident question costs one call; a library
question two (the plan, then an answer, which a count or breakdown question does not need: code answers it).
Executing is the harness's (:mod:`evalv2.tasks.chat.harness`): it runs the agent's own chat router in-process, so
it runs with the agent's Python, never through the generic executor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evalv2.core.hashing import sha256_file
from evalv2.datasets.records import effective_payload, load_records
from evalv2.tasks.base import WorkItem
from evalv2.tasks.chat.corpus import CORPORA

TASK = "chat"
LEDGER = "inference"
CALLS = {"incident": ("incident",), "library": ("library_plan", "library_answer")}
CALL_KINDS = ("incident", "library_plan", "library_answer")


class ChatTaskError(ValueError):
    """A chat config the task cannot plan: missing options, an edited references file, an unknown corpus."""


def options(config: Any) -> dict[str, Any]:
    opts = dict(config.tasks[TASK].options)
    for key in ("references", "corpora", "tokens_bound"):
        if key not in opts:
            raise ChatTaskError(f"tasks.{TASK}.options needs {key!r}")
    corpora = list(opts["corpora"])
    if not corpora or set(corpora) - set(CORPORA) or len(set(corpora)) != len(corpora):
        raise ChatTaskError(f"options.corpora must name distinct corpora of {CORPORA}, got {corpora}")
    if "system" in corpora and not isinstance(opts.get("system"), Mapping):
        raise ChatTaskError("the system corpus needs options.system (store, run, subject, stage)")
    bounds = opts["tokens_bound"]
    if set(bounds) != set(CALL_KINDS) or not all(isinstance(v, int) and v > 0 for v in bounds.values()):
        raise ChatTaskError(f"options.tokens_bound needs a positive bound for each of {CALL_KINDS}")
    return opts


def references_path(opts: Mapping[str, Any], data_root: Path | str | None = None) -> Path:
    from evalv2.datasets.registry import data_root as root

    return root(data_root) / str(opts["references"]["path"])


def load_references(opts: Mapping[str, Any], data_root: Path | str | None = None) -> list[dict[str, Any]]:
    """The pinned RA2 records; refused when the file's SHA-256 is not the config's (an edited file is a new run)."""
    path = references_path(opts, data_root)
    if not path.is_file():
        from evalv2.datasets.registry import DatasetMissingError

        raise DatasetMissingError(f"chat references not found at {path} (set EVALV2_DATA_ROOT)")
    digest = sha256_file(path)
    if digest != opts["references"]["sha256"]:
        raise ChatTaskError(f"{path}: SHA-256 {digest[:12]}... is not the pinned {opts['references']['sha256'][:12]}...")
    return load_records(path)


def half_records(records: Sequence[Mapping[str, Any]], *, half: str, clips: Sequence[str]) -> list[Mapping[str, Any]]:
    """The records of one half, in file order: incident questions about its clips, library questions on its library."""
    chosen = set(clips)
    out = []
    for record in records:
        payload = record["payload"]
        if payload["scope"] == "incident" and str(payload["params"].get("incident_id")) in chosen:
            out.append(record)
        elif payload["scope"] == "library" and payload["params"].get("corpus") == half:
            out.append(record)
    return out


def work_items(records: Sequence[Mapping[str, Any]], *, corpora: Sequence[str], subjects: Sequence[str],
               repeats: int) -> list[WorkItem]:
    """record x corpus x subject x repeat; a rejected reference (a reviewer's ``reject``) is not asked."""
    items = []
    for subject in subjects:
        for corpus in corpora:
            for record in records:
                if effective_payload(record) is None:
                    continue
                payload = record["payload"]
                spec = {"corpus": corpus, "record_id": record["record_id"], "scope": payload["scope"],
                        "class": payload["class"]}
                items += [WorkItem(TASK, subject, f"{corpus}:{record['record_id']}", repeat, spec)
                          for repeat in range(1, repeats + 1)]
    return items


class ChatTask:
    task = TASK
    requires = frozenset({"json_schema"})
    # The harness never re-sends a question within a run: a failed answer is recorded, and a later
    # ``--retry-errors`` pass draws on the same ledger, so the worst case needs no retry multiplier.
    retries_included = True

    def __init__(self, *, data_root: Path | str | None = None) -> None:
        self.data_root = data_root

    def plan(self, config: Any, datasets: Mapping[str, Any]) -> list[WorkItem]:
        clips = datasets.get("clips")
        if clips is None:
            raise ValueError(f"{TASK} needs the split's clips (datasets['clips'] from the dataset loader)")
        opts = options(config)
        records = datasets.get("chat_references") or load_references(opts, self.data_root)
        if isinstance(records, Mapping):
            records = records["records"]
        chosen = half_records(records, half=config.split.half, clips=clips)
        subjects = [s.id for s in config.task_subjects(TASK)]
        return work_items(chosen, corpora=opts["corpora"], subjects=subjects, repeats=config.repeats)

    def budget_estimate(self, config: Any, items: list[WorkItem]) -> dict[str, dict[str, int]]:
        bounds = options(config)["tokens_bound"]
        attempts = tokens = 0
        for item in items:
            for kind in CALLS[item.spec["scope"]]:
                attempts += 1
                tokens += bounds[kind]
        return {LEDGER: {"attempts": attempts, "tokens": tokens}}

    def execute(self, item: WorkItem, adapter: Any, ctx: Any) -> Any:
        raise RuntimeError(f"{TASK} runs through `python -m evalv2.tasks.chat run` with the agent's Python "
                           "(the real chat router in-process), never through the generic executor")
