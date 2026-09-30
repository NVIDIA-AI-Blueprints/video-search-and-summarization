# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
from dataclasses import dataclass


from typing import TYPE_CHECKING, Any, Sequence


import torch

import torch.nn.functional as F

if TYPE_CHECKING:
    from .structured_attribute_aux import StructuredAttributeChoiceLoss
    from .training import StructuredAttributeConfig


@dataclass(frozen=True)
class BinaryResponseSpec:
    positive_ids: tuple[int, ...]
    negative_ids: tuple[int, ...]
    decision_offset: int
    positive_token_id: int
    negative_token_id: int


def _token_ids(tokenizer: Any, text: str) -> tuple[int, ...]:
    ids = tokenizer.encode(text, add_special_tokens=False)
    if not ids:
        raise ValueError(f"Response tokenized to an empty sequence: {text!r}")
    return tuple(int(token_id) for token_id in ids)


def build_binary_response_spec(
    tokenizer: Any,
    positive_response: str = "<answer>yes</answer>",
    negative_response: str = "<answer>no</answer>",
) -> BinaryResponseSpec:
    positive_ids = _token_ids(tokenizer, positive_response)
    negative_ids = _token_ids(tokenizer, negative_response)
    shared = min(len(positive_ids), len(negative_ids))
    decision_offset = next(
        (index for index in range(shared) if positive_ids[index] != negative_ids[index]),
        shared,
    )
    if decision_offset >= len(positive_ids) or decision_offset >= len(negative_ids):
        raise ValueError(
            "Positive and negative responses do not have distinct decision tokens: "
            f"positive_ids={positive_ids}, negative_ids={negative_ids}"
        )
    return BinaryResponseSpec(
        positive_ids=positive_ids,
        negative_ids=negative_ids,
        decision_offset=decision_offset,
        positive_token_id=positive_ids[decision_offset],
        negative_token_id=negative_ids[decision_offset],
    )


def _starts_with(values: Sequence[int], prefix: Sequence[int]) -> bool:
    return len(values) >= len(prefix) and tuple(values[: len(prefix)]) == tuple(prefix)


def extract_binary_scores(
    output, labels, response_spec, *, ignore_index=-100, projection_weight=None
):
    """Read the causal state before yes/no and project its two vocabulary rows."""
    if output.ndim != 3 or labels.ndim != 2 or output.shape[:2] != labels.shape:
        raise ValueError("Expected matching output [B,L,D] and labels [B,L]")
    projection = projection_weight
    if projection is not None and hasattr(projection, "to_local"):
        projection = projection.to_local()
    positive = response_spec.positive_ids[: response_spec.decision_offset + 1]
    negative = response_spec.negative_ids[: response_spec.decision_offset + 1]
    ids = [response_spec.positive_token_id, response_spec.negative_token_id]
    scores, binary_labels = [], []
    for index in range(labels.shape[0]):
        positions = torch.nonzero(labels[index] != ignore_index, as_tuple=False).flatten()
        supervised_ids = labels[index, positions].tolist()
        if _starts_with(supervised_ids, positive):
            label = 1.0
        elif _starts_with(supervised_ids, negative):
            label = 0.0
        else:
            raise ValueError(f"Example {index} does not start with a configured yes/no response")
        prediction_position = int(positions[response_spec.decision_offset].item()) - 1
        if prediction_position < 0:
            raise ValueError(f"Example {index} has no decision input token")
        decision = output[index, prediction_position].float()
        if projection is None or decision.numel() == projection.shape[0]:
            logits = decision[ids]
        elif decision.numel() == projection.shape[1]:
            logits = F.linear(decision, projection[ids].float())
        else:
            raise ValueError("Binary projection shape mismatch")
        scores.append(logits[0] - logits[1])
        binary_labels.append(label)
    return torch.stack(scores), output.new_tensor(binary_labels, dtype=torch.float32)


class RankPointLoss:
    """Probability-mass ranking plus per-query class-balanced binary BCE."""

    def __init__(
        self,
        response_spec,
        *,
        group_size=20,
        rank_weight=1.0,
        point_weight=1.0,
        rank_temperature=1.0,
        point_temperature=1.0,
        negative_point_weight=1.0,
    ):
        self.response_spec = response_spec
        self.group_size = group_size
        self.rank_weight = rank_weight
        self.point_weight = point_weight
        self.rank_temperature = rank_temperature
        self.point_temperature = point_temperature
        self.negative_point_weight = negative_point_weight
        self.projection_weight = None
        self.last_scores = self.last_labels = None
        self.reset_metrics()

    def reset_metrics(self):
        self._metric_sums = {}
        self._metric_counts = {}

    def metric_totals(self):
        return {
            name: (value, self._metric_counts[name]) for name, value in self._metric_sums.items()
        }

    def mean_metrics(self):
        return {
            name: value / self._metric_counts[name] for name, value in self._metric_sums.items()
        }

    def __call__(
        self,
        output,
        target,
        *,
        ignore_index=-100,
        loss_scaling_factor=1.0,
        output_packing_mask=None,
        target_packing_mask=None,
        lin_weight=None,
        **_,
    ):
        if output_packing_mask is not None or target_packing_mask is not None:
            raise ValueError("The checkpoint recipe does not support sequence packing")
        if lin_weight is not None:
            self.projection_weight = lin_weight
        scores, labels = extract_binary_scores(
            output,
            target,
            self.response_spec,
            ignore_index=ignore_index,
            projection_weight=self.projection_weight,
        )
        return self.from_scores(scores, labels, loss_scaling_factor=loss_scaling_factor)

    def from_scores(self, scores, labels, *, loss_scaling_factor=1.0):
        scores = scores.float().reshape(-1)
        labels = labels.float().reshape(-1).to(device=scores.device)
        if scores.shape != labels.shape or not scores.numel() or scores.numel() % self.group_size:
            raise ValueError("Scores and labels must contain complete K20 query groups")
        self.last_scores, self.last_labels = scores, labels
        grouped_scores = list(scores.split(self.group_size))
        grouped_labels = list(labels.split(self.group_size))
        positives = [group > 0.5 for group in grouped_labels]
        negatives = [~positive for positive in positives]
        if any(not bool(p.any()) or not bool(n.any()) for p, n in zip(positives, negatives)):
            raise ValueError("Every query group needs both positive and negative candidates")
        loss_rank = torch.stack(
            [
                torch.logsumexp(group_scores / self.rank_temperature, dim=0)
                - torch.logsumexp((group_scores / self.rank_temperature)[positive], dim=0)
                for group_scores, positive in zip(grouped_scores, positives)
            ]
        ).mean()
        point_terms = []
        # Preserve the source graph's separate rank and point splits. Sharing
        # one split changes accumulation order before the BF16 backward cast.
        grouped_point_scores = list(scores.split(self.group_size))
        grouped_point_labels = list(labels.split(self.group_size))
        for group_scores, group_labels, positive, negative in zip(
            grouped_point_scores, grouped_point_labels, positives, negatives, strict=True
        ):
            losses = F.binary_cross_entropy_with_logits(
                group_scores / self.point_temperature, group_labels, reduction="none"
            )
            point_terms.append(
                (losses[positive].mean() + self.negative_point_weight * losses[negative].mean())
                / (1.0 + self.negative_point_weight)
            )
        loss_point = torch.stack(point_terms).mean()
        zero = scores.new_zeros(())
        total = (
            self.rank_weight * (loss_rank + 0.0 * zero)
            + self.point_weight * loss_point
            + 0.0 * zero
        )
        with torch.no_grad():
            metrics = {
                "rank": loss_rank.detach(),
                "point": loss_point.detach(),
                "binary_accuracy": ((scores > 0) == (labels > 0.5)).float().mean(),
            }
            counts = {
                "rank": len(grouped_scores),
                "point": len(grouped_scores),
                "binary_accuracy": scores.numel(),
            }
            for name, value in metrics.items():
                weighted = value * counts[name]
                self._metric_sums[name] = self._metric_sums.get(name, 0) + weighted
                self._metric_counts[name] = self._metric_counts.get(name, 0) + counts[name]
        return total * float(loss_scaling_factor)


class RankPointWithStructuredAttributeLoss:
    """Add masked multi-field choice CE after the ordinary yes/no decision."""

    def __init__(
        self,
        base: RankPointLoss,
        choice_loss: StructuredAttributeChoiceLoss,
        config: StructuredAttributeConfig,
    ) -> None:
        self.base = base
        self.choice_loss = choice_loss
        self.config = config
        self._records: list[dict[str, Any] | None] | None = None
        self._attribute_metric_sums: dict[str, torch.Tensor] = {}
        self._attribute_metric_counts: dict[str, int] = {}

    def __getattr__(self, name: str):
        """Delegate the ordinary rank-loss contract to the wrapped loss.

        The trainer's metadata queues evolve independently of this optional
        auxiliary wrapper (for example, same-label consistency was added to
        ``RankPointLoss`` after this class).  Forwarding unknown attributes
        keeps a structured-attribute run behaviorally identical to its base
        ranking control except for the explicitly added auxiliary term.
        """
        if name == "base":
            raise AttributeError(name)
        return getattr(self.base, name)

    @property
    def projection_weight(self):
        return self.base.projection_weight

    @projection_weight.setter
    def projection_weight(self, value) -> None:
        self.base.projection_weight = value

    def set_structured_records(self, records: Sequence[dict[str, Any] | None]) -> None:
        if self._records:
            raise ValueError("Previous structured metadata was not consumed")
        self._records = list(records)

    def _take_records(self, count: int) -> list[dict[str, Any] | None]:
        if self._records is None or len(self._records) < count:
            available = 0 if self._records is None else len(self._records)
            raise ValueError(f"Need {count} structured metadata rows, found {available}")
        selected = self._records[:count]
        del self._records[:count]
        return selected

    def assert_structured_records_consumed(self) -> None:
        if self._records:
            raise ValueError(f"{len(self._records)} structured metadata rows were not consumed")
        self._records = None

    def reset_metrics(self) -> None:
        self.base.reset_metrics()
        self._attribute_metric_sums = {}
        self._attribute_metric_counts = {}

    def metric_totals(self) -> dict[str, tuple[torch.Tensor, int]]:
        totals = dict(self.base.metric_totals())
        totals.update(
            {
                name: (value, self._attribute_metric_counts[name])
                for name, value in self._attribute_metric_sums.items()
            }
        )
        return totals

    def mean_metrics(self) -> dict[str, torch.Tensor]:
        result = dict(self.base.mean_metrics())
        result.update(
            {
                name: value / max(self._attribute_metric_counts[name], 1)
                for name, value in self._attribute_metric_sums.items()
            }
        )
        return result

    def __call__(self, output, target, **kwargs):
        rank_point = self.base(output, target, **kwargs)
        records = self._take_records(int(target.shape[0]))
        contribution, metrics, _compatibility_scores, _compatibility_valid = self.choice_loss(
            output,
            target,
            records,
            projection_weight=kwargs.get("lin_weight")
            if kwargs.get("lin_weight") is not None
            else self.projection_weight,
        )
        for name, (metric_sum, metric_count) in metrics.items():
            self._attribute_metric_sums[name] = (
                metric_sum
                if name not in self._attribute_metric_sums
                else self._attribute_metric_sums[name] + metric_sum
            )
            self._attribute_metric_counts[name] = (
                self._attribute_metric_counts.get(name, 0) + metric_count
            )
        mismatch_contribution = output.sum() * 0.0
        return (
            rank_point
            + contribution * self.config.loss_weight * float(kwargs.get("loss_scaling_factor", 1.0))
            + mismatch_contribution * float(kwargs.get("loss_scaling_factor", 1.0))
        )
