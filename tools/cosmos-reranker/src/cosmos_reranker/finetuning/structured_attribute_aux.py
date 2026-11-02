# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
from dataclasses import dataclass, field as dataclass_field

from collections import Counter


import hashlib

import json

from pathlib import Path

import random

from typing import Any, Sequence

import torch

import torch.nn.functional as F

PAS_COMPATIBILITY_PROBE_FIELDS = (
    "top_outer_color",
    "top_outer_type",
    "bottom_color",
    "bottom_type",
    "shoe_color",
    "shoe_type",
    "viewpoint",
    "accessory",
)

_COMPATIBILITY_PROBE_PLACEHOLDER = "?"

_ACTIVE_COMPATIBILITY_SATISFACTION_MARKERS = tuple(
    f"[{field} satisfied: yes or no]\n" for field in PAS_COMPATIBILITY_PROBE_FIELDS
)

_TRISTATE_COMPATIBILITY_MARKERS = (
    "top_color:",
    "top_type:",
    "bottom_color:",
    "bottom_type:",
    "shoe_color:",
    "shoe_type:",
    "viewpoint:",
    "accessory:",
)

PAS_PREDECISION_TRISTATE_PROBE_RESPONSE = "\n".join(
    [
        "[latent requirement checklist]",
        "[state classes: inactive | satisfied | violated]",
        *(
            f"{marker} {_COMPATIBILITY_PROBE_PLACEHOLDER}"
            for marker in _TRISTATE_COMPATIBILITY_MARKERS
        ),
        "<answer>?</answer>",
    ]
)


def normalized_field(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


@dataclass(frozen=True)
class StructuredAttributeAssets:
    fields: tuple[str, ...]
    choices_by_field: dict[str, tuple[str, ...]]
    labels_by_image: dict[str, tuple[int, ...]]
    query_labels_by_index: tuple[tuple[int, ...], ...] = dataclass_field(default_factory=tuple)


def load_structured_attribute_assets(
    records_path: str | Path,
    vocab_path: str | Path,
    query_records_path: str | Path | None = None,
) -> StructuredAttributeAssets:
    records = json.loads(Path(records_path).read_text(encoding="utf-8"))
    vocab = json.loads(Path(vocab_path).read_text(encoding="utf-8"))
    fields = tuple(str(value) for value in vocab["attributes"])
    choices = {
        field: tuple(str(value) for value in vocab["id_to_value"][field][1:]) for field in fields
    }
    if any(not values or len(values) > 14 for values in choices.values()):
        raise ValueError("Structured PAS fields must contain between 1 and 14 choices")

    labels_by_image: dict[str, tuple[int, ...]] = {}
    for row in records:
        image = str(row["unique_name"])
        labels = tuple(int(value) for value in row["image_attr_values"])
        if len(labels) != len(fields):
            raise ValueError(f"Attribute width mismatch for {image}: {labels}")
        for field, label in zip(fields, labels, strict=True):
            if not 0 <= label <= len(choices[field]):
                raise ValueError(f"Invalid {field!r} label {label} for {image}")
        previous = labels_by_image.setdefault(image, labels)
        if previous != labels:
            raise ValueError(f"Conflicting structured labels for {image}")
    query_labels_by_index: tuple[tuple[int, ...], ...] = ()
    if query_records_path is not None:
        query_rows = json.loads(Path(query_records_path).read_text(encoding="utf-8"))
        labels_by_index = []
        for row_index, raw_labels in enumerate(query_rows):
            labels = tuple(int(value) for value in raw_labels)
            if len(labels) != len(fields):
                raise ValueError(f"Query attribute width mismatch at row {row_index}")
            labels_by_index.append(labels)
        query_labels_by_index = tuple(labels_by_index)
    return StructuredAttributeAssets(fields, choices, labels_by_image, query_labels_by_index)


def _stable_rng(seed: int, sample_id: str, field: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}\0{sample_id}\0{field}".encode()).digest()
    return random.Random(int.from_bytes(digest[:16], "big"))


def append_structured_attribute_response(
    binary_response: str,
    *,
    sample_id: str,
    labels: Sequence[int],
    assets: StructuredAttributeAssets,
    seed: int,
    query_labels: Sequence[int] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Append full-vocabulary choices and isolated answers to yes/no output."""

    if len(labels) != len(assets.fields):
        raise ValueError("Structured attribute labels have the wrong width")
    option_lines: list[str] = []
    answer_lines: list[str] = []
    targets: list[dict[str, Any]] = []
    if query_labels is not None and len(query_labels) != len(assets.fields):
        raise ValueError("Structured query labels have the wrong width")
    scalar_match = True
    scalar_mismatch = False
    scalar_comparable = False
    for field_index, (field, raw_label) in enumerate(zip(assets.fields, labels, strict=True)):
        field_key = normalized_field(field)
        canonical = list(assets.choices_by_field[field])
        shuffled = list(canonical)
        _stable_rng(seed, sample_id, field).shuffle(shuffled)
        rendered = "|".join(
            f"{chr(ord('A') + index)}={value}" for index, value in enumerate(shuffled)
        )
        option_lines.append(f"[{field_key} choices] {rendered}")
        marker = f"[{field_key} answer]\n"
        if int(raw_label) == 0:
            answer = "?"
        else:
            expected_value = canonical[int(raw_label) - 1]
            target_class = shuffled.index(expected_value)
            answer = chr(ord("A") + target_class)
            targets.append(
                {
                    "field": field_key,
                    "marker": marker,
                    "target_class": target_class,
                    "option_count": len(shuffled),
                    "target_value": expected_value,
                    "choices": shuffled,
                    **(
                        {
                            "query_target_class": shuffled.index(
                                canonical[int(query_labels[field_index]) - 1]
                            ),
                            "query_target_value": canonical[int(query_labels[field_index]) - 1],
                        }
                        if query_labels is not None and int(query_labels[field_index]) > 0
                        else {}
                    ),
                }
            )
        if query_labels is not None and int(query_labels[field_index]) > 0 and int(raw_label) > 0:
            scalar_comparable = True
            differs = int(raw_label) != int(query_labels[field_index])
            scalar_mismatch = scalar_mismatch or differs
            scalar_match = scalar_match and not differs
        answer_lines.extend([marker.rstrip("\n"), answer])

    # Choice and field text are intentionally in the assistant prefix.  The
    # overall yes/no decision therefore has exactly the same user context and
    # prediction position as deployed binary reranking.
    response = "\n".join(
        [
            binary_response,
            "[masked attribute choice context]",
            *option_lines,
            "[masked attribute answers]",
            *answer_lines,
        ]
    )
    metadata = {
        "sample_id": str(sample_id),
        "response": response,
        "targets": targets,
        "scalar_match": scalar_match if query_labels is not None else None,
        "scalar_mismatch": scalar_mismatch if query_labels is not None else None,
        "scalar_comparable": scalar_comparable if query_labels is not None else None,
    }
    return response, metadata


def _find_unique_subsequence(values: list[int], needle: list[int]) -> int:
    matches = [
        index
        for index in range(len(values) - len(needle) + 1)
        if values[index : index + len(needle)] == needle
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one structured marker occurrence, found {len(matches)}")
    return matches[0]


class StructuredAttributeChoiceLoss:
    """Constrained A-N CE at only the structured field answer positions."""

    def __init__(
        self,
        tokenizer,
        projection_weight,
        fields: Sequence[str],
        *,
        hard_example_weight: float = 1.0,
        hard_example_fields: Sequence[str] = (),
    ):
        self.tokenizer = tokenizer
        self.projection_weight = projection_weight
        self.fields = tuple(normalized_field(field) for field in fields)
        if hard_example_weight < 1.0:
            raise ValueError("hard_example_weight must be at least 1.0")
        self.hard_example_weight = float(hard_example_weight)
        self.hard_example_fields = frozenset(
            normalized_field(field) for field in hard_example_fields
        )
        unknown_hard_fields = self.hard_example_fields.difference(self.fields)
        if unknown_hard_fields:
            raise ValueError(f"Unknown hard-example fields: {sorted(unknown_hard_fields)}")
        letter_ids = []
        for index in range(14):
            letter = chr(ord("A") + index)
            ids = tokenizer.encode(letter, add_special_tokens=False)
            if len(ids) != 1:
                raise ValueError(f"Structured label {letter!r} is not one token: {ids}")
            letter_ids.append(int(ids[0]))
        if len(set(letter_ids)) != len(letter_ids):
            raise ValueError("Structured option letters do not have unique token IDs")
        self.letter_ids = tuple(letter_ids)
        self._audit_enabled = False
        self._audit_records: list[dict[str, Any]] = []

    def set_audit_enabled(self, enabled: bool) -> None:
        self._audit_enabled = bool(enabled)
        if not self._audit_enabled:
            self._audit_records = []

    def pop_audit_records(self) -> list[dict[str, Any]]:
        records = self._audit_records
        self._audit_records = []
        return records

    def __call__(
        self,
        output: torch.Tensor,
        target: torch.LongTensor,
        records: Sequence[dict[str, Any] | None],
        *,
        ignore_index: int = -100,
        projection_weight: torch.Tensor | None = None,
    ) -> tuple[
        torch.Tensor,
        dict[str, tuple[torch.Tensor, int]],
        torch.Tensor,
        torch.BoolTensor,
    ]:
        if len(records) != int(target.shape[0]):
            raise ValueError("Structured metadata and batch sizes disagree")
        projection = projection_weight if projection_weight is not None else self.projection_weight
        if hasattr(projection, "to_local"):
            projection = projection.to_local()
        letter_ids = torch.tensor(self.letter_ids, device=output.device)
        letter_weight = projection.index_select(0, letter_ids)

        losses: list[torch.Tensor] = []
        correct_by_field: dict[str, torch.Tensor] = {}
        count_by_field: Counter[str] = Counter()
        hard_by_field: Counter[str] = Counter()
        covered_samples = 0
        compatibility_scores: list[torch.Tensor] = []
        compatibility_valid: list[bool] = []
        for row_index, record in enumerate(records):
            if record is None or not record.get("targets"):
                compatibility_scores.append(output[row_index].sum() * 0.0)
                compatibility_valid.append(False)
                continue
            covered_samples += 1
            query_log_probabilities: list[torch.Tensor] = []
            supervised_positions = torch.nonzero(
                target[row_index].ne(ignore_index), as_tuple=False
            ).flatten()
            supervised_ids = target[row_index, supervised_positions].tolist()
            expected_ids = self.tokenizer.encode(str(record["response"]), add_special_tokens=False)
            if supervised_ids[: len(expected_ids)] != expected_ids:
                raise ValueError(
                    f"Packed structured response differs for sample {record['sample_id']}"
                )
            for item in record["targets"]:
                field = normalized_field(str(item["field"]))
                if field not in self.fields:
                    raise ValueError(f"Unknown structured field {field!r}")
                marker_ids = self.tokenizer.encode(str(item["marker"]), add_special_tokens=False)
                marker_offset = _find_unique_subsequence(expected_ids, marker_ids)
                answer_offset = marker_offset + len(marker_ids)
                target_class = int(item["target_class"])
                option_count = int(item["option_count"])
                expected_letter_id = self.letter_ids[target_class]
                if expected_ids[answer_offset] != expected_letter_id:
                    raise ValueError(
                        f"Structured answer is not isolated for {field}: "
                        f"expected={expected_letter_id}, got={expected_ids[answer_offset]}"
                    )
                target_position = int(supervised_positions[answer_offset].item())
                if target_position <= 0:
                    raise ValueError("Structured answer has no prediction position")
                hidden = output[row_index, target_position - 1]
                logits = F.linear(hidden.to(letter_weight.dtype), letter_weight).float()
                logits = logits[:option_count]
                class_target = torch.tensor([target_class], device=logits.device, dtype=torch.long)
                predicted_class = int(logits.argmax().item())
                is_hard_example = (
                    predicted_class != target_class
                    and field in self.hard_example_fields
                    and self.hard_example_weight > 1.0
                )
                field_loss = F.cross_entropy(logits.unsqueeze(0), class_target)
                if is_hard_example:
                    field_loss = field_loss * self.hard_example_weight
                    hard_by_field[field] += 1
                losses.append(field_loss)
                if "query_target_class" in item:
                    query_log_probabilities.append(
                        logits.log_softmax(dim=0)[int(item["query_target_class"])]
                    )
                is_correct = logits.argmax().eq(target_class).float().detach()
                if self._audit_enabled:
                    probabilities = logits.softmax(dim=0).detach().cpu().tolist()
                    choices = [str(value) for value in item["choices"]]
                    self._audit_records.append(
                        {
                            **{str(key): value for key, value in record.get("audit", {}).items()},
                            "sample_id": str(record["sample_id"]),
                            "field": field,
                            "choices": choices,
                            "label_value": str(item["target_value"]),
                            "predicted_value": choices[predicted_class],
                            "correct": bool(predicted_class == target_class),
                            "target_probability": float(probabilities[target_class]),
                            "probabilities_by_value": {
                                value: float(probabilities[index])
                                for index, value in enumerate(choices)
                            },
                        }
                    )
                correct_by_field[field] = (
                    is_correct
                    if field not in correct_by_field
                    else correct_by_field[field] + is_correct
                )
                count_by_field[field] += 1
            compatibility_scores.append(
                torch.stack(query_log_probabilities).mean()
                if query_log_probabilities
                else output[row_index].sum() * 0.0
            )
            compatibility_valid.append(bool(query_log_probabilities))

        zero = output.sum() * 0.0
        loss = torch.stack(losses).mean() if losses else zero
        loss_sum = (
            torch.stack([value.detach() for value in losses]).sum() if losses else zero.detach()
        )
        correct_sum = (
            torch.stack(list(correct_by_field.values())).sum()
            if correct_by_field
            else zero.detach()
        )
        metrics: dict[str, tuple[torch.Tensor, int]] = {
            "attribute_loss": (loss_sum, len(losses)),
            "attribute_accuracy": (correct_sum, len(losses)),
            "attribute_sample_coverage": (
                output.new_tensor(float(covered_samples)).detach(),
                int(target.shape[0]),
            ),
        }
        focused_count = sum(count_by_field[field] for field in self.hard_example_fields)
        hard_count = sum(hard_by_field.values())
        if focused_count:
            metrics["attribute_online_hard_example_rate"] = (
                output.new_tensor(float(hard_count)).detach(),
                focused_count,
            )
        for field in self.fields:
            count = count_by_field[field]
            if count:
                metrics[f"attribute_{field}_accuracy"] = (
                    correct_by_field[field],
                    count,
                )
                if field in self.hard_example_fields:
                    metrics[f"attribute_{field}_hard_example_rate"] = (
                        output.new_tensor(float(hard_by_field[field])).detach(),
                        count,
                    )
        return (
            loss,
            metrics,
            torch.stack(compatibility_scores),
            torch.tensor(compatibility_valid, device=output.device, dtype=torch.bool),
        )
