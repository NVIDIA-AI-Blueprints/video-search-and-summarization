# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cosmos RL worker entrypoint for the selected Cosmos Nano step-8000 recipe."""

from __future__ import annotations
import argparse
import copy
import json
import math
import os
import random
import re
import types
from pathlib import Path
from typing import Any, Iterator, Literal
import pydantic
import toml
import torch
import torch.distributed as dist
from PIL import Image
from torch.utils.data import Sampler
import cosmos_rl.launcher.worker_entry
from cosmos_rl.policy.trainer.base import TrainerRegistry
from cosmos_rl.policy.trainer.llm_trainer.sft_trainer import SFTTrainer
from cosmos_rl.dispatcher.data.packer.qwen3_vl_data_packer import Qwen3_VL_DataPacker
from cosmos_rl.utils.logging import logger
from cosmos_reranker.finetuning.loss import (
    RankPointLoss,
    RankPointWithStructuredAttributeLoss,
    build_binary_response_spec,
)
from cosmos_reranker.finetuning.structured_attribute_aux import (
    StructuredAttributeChoiceLoss,
    append_structured_attribute_response,
    load_structured_attribute_assets,
)
from cosmos_reranker.finetuning.visual_cache import VisualCacheReader, visual_cache_key

try:
    from cosmos_reason1_utils.text import create_conversation
except ImportError:
    create_conversation = None
_METRICS_PATH: Path | None = None
_OFFLINE_WANDB_RUN: Any | None = None


class RecipeConfig(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")


class VisionConfig(RecipeConfig):
    nframes: Literal[1] = 1
    min_pixels: int | None = None
    max_pixels: Literal[81920] = 81920


class DatasetConfig(RecipeConfig):
    annotation_path: str | list[str]
    media_path: str
    response_mode: Literal["binary"] = "binary"


class LossConfig(RecipeConfig):
    rank_mode: Literal["probability_mass"] = "probability_mass"
    rank_weight: float = 1.0
    point_weight: float = 1.0
    point_mode: Literal["class_balanced"] = "class_balanced"
    negative_point_weight: float = 1.0
    teacher_weight: Literal[0.0] = 0.0
    rank_temperature: float = 1.0
    point_temperature: float = 1.0
    candidate_group_size: Literal[20] = 20
    positive_response: str = "<answer>yes</answer>"
    negative_response: str = "<answer>no</answer>"


class VisualCacheConfig(RecipeConfig):
    manifests: list[str] = pydantic.Field(min_length=1)
    resident_shards: int = 8
    slice_reads: bool = False


class StructuredAttributeConfig(RecipeConfig):
    train_records_path: str
    attribute_vocab_path: str
    # Preserved for provenance; training validation is disabled in this recipe.
    validation_records_path: str | None = None
    loss_weight: float = 0.25
    seed: int = 160901


class CustomConfig(RecipeConfig):
    train_dataset: DatasetConfig
    system_prompt: str = ""
    vision: VisionConfig = pydantic.Field(default_factory=VisionConfig)
    loss: LossConfig = pydantic.Field(default_factory=LossConfig)
    visual_cache: VisualCacheConfig
    structured_attribute: StructuredAttributeConfig
    fp32_trainable_master_only: Literal[True] = True


class PasConversationDataset(torch.utils.data.Dataset):
    """Preserve source query groups and append deterministic attribute targets."""

    def __init__(self, custom_config, dataset_config, *, structured_attribute_assets):
        paths = dataset_config.annotation_path
        paths = [paths] if isinstance(paths, str) else paths
        if not paths:
            raise ValueError("At least one annotation shard is required")
        self.annotations = []
        for path in paths:
            with open(path, encoding="utf-8") as stream:
                shard = json.load(stream)
            if not isinstance(shard, list):
                raise TypeError(f"Annotations must be a JSON list: {path}")
            self.annotations.extend(shard)
        self.media_path = dataset_config.media_path
        self.system_prompt = custom_config.system_prompt
        self.vision_kwargs = custom_config.vision.model_dump(exclude_none=True)
        self.group_size = custom_config.loss.candidate_group_size
        self.positive_response = custom_config.loss.positive_response
        self.negative_response = custom_config.loss.negative_response
        self.structured_attribute_assets = structured_attribute_assets
        self.structured_attribute_seed = custom_config.structured_attribute.seed
        if len(self.annotations) % self.group_size:
            raise ValueError("Annotations must contain complete K20 groups")
        self.group_ranges = [
            (start, start + self.group_size)
            for start in range(0, len(self.annotations), self.group_size)
        ]
        previous_id = None
        for start, end in self.group_ranges:
            rows = self.annotations[start:end]
            group_ids = {row.get("group_id") for row in rows}
            if None in group_ids or len(group_ids) != 1 or previous_id in group_ids:
                raise ValueError(f"Invalid query group at rows {start}:{end}")
            previous_id = next(iter(group_ids))
            labels = []
            ranks = []
            for row in rows:
                response = row["conversations"][1]["value"]
                if response not in (self.positive_response, self.negative_response):
                    raise ValueError(f"Unsupported binary response: {response!r}")
                label = int(response.startswith(self.positive_response))
                if int(row["label"]) != label:
                    raise ValueError("Annotation label disagrees with response")
                labels.append(label)
                if "retriever_rank" in row:
                    ranks.append(int(row["retriever_rank"]))
            if not 0 < sum(labels) < self.group_size:
                raise ValueError("A query group needs both positive and negative examples")
            if ranks and (
                len(ranks) != self.group_size
                or ranks[0] != 1
                or any(b <= a for a, b in zip(ranks, ranks[1:]))
            ):
                raise ValueError("Candidates must preserve the original retriever order")

    def setup(self, config, tokenizer=None):
        return None

    def __len__(self):
        return len(self.annotations)

    def __getitem__(self, index):
        sample = self.annotations[index]
        conversations = sample["conversations"]
        user_prompt = conversations[0]["value"]
        response = conversations[1]["value"]
        images = sample.get("image") or sample.get("images")
        if isinstance(images, str):
            images = [images]
        if not images or len(images) != 1:
            raise ValueError("This checkpoint recipe requires one candidate image")
        structured = None
        labels = self.structured_attribute_assets.labels_by_image.get(str(images[0]))
        if labels is not None:
            response, structured = append_structured_attribute_response(
                response,
                sample_id=str(sample.get("id", index)),
                labels=labels,
                assets=self.structured_attribute_assets,
                seed=self.structured_attribute_seed,
            )
        images = [os.path.join(self.media_path, image) for image in images]
        user_prompt = re.sub(r"(\n)?</?image>(\n)?", "", user_prompt)
        if create_conversation is not None:
            messages = create_conversation(
                system_prompt=self.system_prompt,
                user_prompt=user_prompt,
                response=response,
                images=images,
                videos=None,
                vision_kwargs=self.vision_kwargs,
            )
        else:
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        *(
                            {"type": "image", "image": image, **self.vision_kwargs}
                            for image in images
                        ),
                        {"type": "text", "text": user_prompt},
                    ],
                }
            )
            messages.append({"role": "assistant", "content": response})
        result = {
            "messages": messages,
            "_pas_visual_cache_keys": [visual_cache_key(image) for image in images],
            "_pas_group_id": str(sample["group_id"]),
            "_pas_binary_label": int(sample["label"]),
        }
        if structured is not None:
            result["_pas_structured_attributes"] = structured
        return result


_PAS_PACKER_SIDECAR_FIELDS = ("_pas_group_id", "_pas_binary_label", "_pas_structured_attributes")


def configure_cuda_runtime() -> None:
    """Work around unsupported half-precision cuDNN Conv3d on this pod.

    CUDA kernels remain enabled. Only cuDNN convolution dispatch is disabled;
    PyTorch's native CUDA Conv3d handles the CR3 vision patch projection.
    """

    if os.environ.get("PAS_DISABLE_CUDNN_CONV", "0") == "1":
        torch.backends.cudnn.enabled = False
        logger.warning(
            "Disabled cuDNN convolution dispatch because the CUDA 13.2 "
            "forward-compatibility runtime cannot select a BF16 Conv3d engine"
        )


def _is_policy_master() -> bool:
    return (
        os.environ.get("COSMOS_ROLE") == "Policy"
        and int(os.environ.get("NODE_RANK", "0")) == 0
        and int(os.environ.get("LOCAL_RANK", os.environ.get("RANK", "0"))) == 0
    )


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def write_loss_metrics(report_data: dict[str, Any], step: int) -> None:
    """Write rank-zero training and validation reports as newline JSON."""

    if not _is_policy_master() or _METRICS_PATH is None:
        return
    record = {"step": int(step)}
    record.update({key: _json_value(value) for key, value in report_data.items()})
    with _METRICS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    if _OFFLINE_WANDB_RUN is not None:
        _OFFLINE_WANDB_RUN.log(
            {key: value for key, value in record.items() if key != "step"},
            step=int(step),
        )


def init_offline_wandb(config) -> None:
    """Keep W&B logging usable without making credentials block a smoke run."""

    global _OFFLINE_WANDB_RUN
    if not _is_policy_master() or "wandb" not in config.logging.logger:
        return
    import wandb

    if wandb.api.api_key:
        return
    output_dir = Path(config.train.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _OFFLINE_WANDB_RUN = wandb.init(
        project=config.logging.project_name,
        group=config.logging.group_name,
        name=config.logging.experiment_name,
        dir=str(output_dir),
        mode="offline",
        config={
            "policy.model_name_or_path": config.policy.model_name_or_path,
            "train.epoch": config.train.epoch,
            "train.optm_lr": config.train.optm_lr,
            "custom.loss": config.custom.get("loss", {}),
        },
    )
    logger.warning(
        "WANDB_API_KEY is not configured; recording an offline W&B run under %s",
        output_dir,
    )


def _copy_pas_packer_sidecars(processed, sample):
    """Preserve supervision that the upstream Qwen packer does not know about."""
    for name in _PAS_PACKER_SIDECAR_FIELDS:
        if sample.get(name) is not None:
            processed[name] = sample[name]
    return processed


class PasCachedQwen3VLDataPacker(Qwen3_VL_DataPacker):
    """Substitute cached frozen visual tensors after ordinary token packing."""

    class _CopyableString(str):
        """Work around the upstream packer's unconditional content.copy()."""

        def copy(self):
            return self

    def __init__(self, cache_config: VisualCacheConfig, vision_config: VisionConfig):
        super().__init__()
        self.cache_config = cache_config
        self.vision_config = vision_config
        self.cache: VisualCacheReader | None = None

    def setup(self, config, *args, **kwargs):
        super().setup(config, *args, **kwargs)
        self.cache = VisualCacheReader(
            self.cache_config.manifests,
            resident_shards=self.cache_config.resident_shards,
            slice_reads=self.cache_config.slice_reads,
        )
        expected_preprocessing = {
            "image_patch_size": 16,
            "spatial_merge_size": 2,
            # Older cosmos_reason1_utils VisionConfig releases do not expose
            # min_pixels.  The cache builder records an unset minimum as None,
            # so preserve that meaning instead of failing during worker setup.
            "min_pixels": getattr(self.vision_config, "min_pixels", None),
            "max_pixels": self.vision_config.max_pixels,
        }
        if self.cache.preprocessing != expected_preprocessing:
            raise ValueError(
                "Visual cache preprocessing disagrees with [custom.vision]: "
                f"cache={self.cache.preprocessing}, expected={expected_preprocessing}"
            )
        # Cached grids already describe the final resize. The processor only
        # needs to create matching image placeholder tokens and mRoPE positions.
        self.hf_processor.image_processor.do_resize = False
        logger.info("Loaded PAS visual-cache index with %s images", len(self.cache.entries))

    def sft_process_sample(self, sample):
        cache_keys = sample.get("_pas_visual_cache_keys")
        if cache_keys is None:
            # Compatibility with samples produced before multi-image cache
            # support was added.
            cache_keys = [sample["_pas_visual_cache_key"]]
        assert self.cache is not None
        grids = [self.cache.grid(cache_key) for cache_key in cache_keys]
        if any(grid_t != 1 for grid_t, _, _ in grids):
            raise ValueError(f"PAS image cache requires grid_t=1, got {grids}")

        # The upstream packer needs an image solely to determine how many image
        # placeholder tokens and mRoPE positions to emit. A black image with
        # the cached pre-merge grid dimensions produces the exact same layout;
        # its cheap CPU pixels are discarded in _collate_fn below.
        dummy_images = [Image.new("RGB", (grid_w * 16, grid_h * 16)) for _, grid_h, grid_w in grids]
        messages = copy.deepcopy(sample["messages"])
        for message in messages:
            if message.get("role") == "assistant" and isinstance(message.get("content"), str):
                message["content"] = self._CopyableString(message["content"])
        processed = super().sft_process_sample({"messages": messages, "images": dummy_images})
        processed["_pas_visual_cache_keys"] = list(cache_keys)
        return _copy_pas_packer_sidecars(processed, sample)

    def _collate_fn(self, processed_samples, computed_max_len):
        batch = super()._collate_fn(processed_samples, computed_max_len)
        assert self.cache is not None
        cache_keys = [
            cache_key
            for sample in processed_samples
            for cache_key in sample.get(
                "_pas_visual_cache_keys", [sample.get("_pas_visual_cache_key")]
            )
        ]
        if any(cache_key is None for cache_key in cache_keys):
            raise ValueError("Processed PAS sample is missing its visual-cache key")
        records = [self.cache.get(cache_key) for cache_key in cache_keys]
        cached_grids = torch.tensor([grid for _, grid in records], dtype=torch.long)
        if not torch.equal(cached_grids, batch["image_grid_thw"]):
            raise ValueError("Cached image grids disagree with Qwen preprocessing")
        if self.cache.prefix_block is None:
            raise ValueError("Visual prefix cache is missing prefix_block metadata")
        # Reuse the standard image/video tensor fields so existing dataloader,
        # transfer, and FSDP signatures stay unchanged. The Qwen3-VL patch
        # recognizes this shape pair as a cached visual prefix.
        batch["pixel_values"] = torch.cat([value[0] for value, _ in records], dim=0)
        batch["pixel_values_videos"] = torch.cat([value[1] for value, _ in records], dim=0)
        batch["video_grid_thw"] = torch.tensor([self.cache.prefix_block], dtype=torch.long)
        return batch


class DistributedGroupBatchSampler(Sampler[list[int]]):
    """Keep every positive/negative candidate group on one DP rank.

    PyTorch's ordinary DistributedSampler strides through individual examples.
    Since PAS stores one positive followed by three negatives, that would send
    positives and negatives to different ranks and make a local ranking loss
    undefined. This sampler distributes whole, contiguous groups instead.
    """

    def __init__(
        self,
        dataset,
        *,
        group_size: int,
        batch_size: int,
        num_replicas: int,
        rank: int,
        shuffle: bool = False,
        pad: bool = True,
        seed: int = 0,
    ) -> None:
        if group_size <= 1:
            raise ValueError(f"group_size must exceed one, got {group_size}")
        if len(dataset) % group_size:
            raise ValueError(
                f"Dataset length {len(dataset)} must be divisible by group_size={group_size}"
            )
        self.group_size = int(group_size)
        if batch_size < group_size or batch_size % group_size:
            raise ValueError(
                f"batch_size={batch_size} must be a multiple of group_size={group_size}"
            )
        self.batch_size = int(batch_size)
        self.num_replicas = int(num_replicas)
        self.rank = int(rank)
        self.shuffle = bool(shuffle)
        self.pad = bool(pad)
        self.seed = int(seed)
        self.epoch = 0
        self.groups_per_batch = self.batch_size // self.group_size
        self.num_groups = len(dataset) // self.group_size
        self.batches_per_rank = math.ceil(
            self.num_groups / (self.num_replicas * self.groups_per_batch)
        )
        self.padded_num_groups = self.batches_per_rank * self.num_replicas * self.groups_per_batch
        if not self.pad and self.padded_num_groups != self.num_groups:
            multiple = self.num_replicas * self.groups_per_batch
            raise ValueError(
                f"Validation has {self.num_groups} groups, but exact distributed "
                f"validation requires a multiple of {multiple}. Choose a validation "
                "subset without sampler padding."
            )

    def __iter__(self) -> Iterator[list[int]]:
        groups = list(range(self.num_groups))
        if self.shuffle:
            random.Random(self.seed + self.epoch).shuffle(groups)
        padded = self.padded_num_groups
        if padded > len(groups):
            groups.extend(groups[index % len(groups)] for index in range(padded - len(groups)))
        rank_groups = groups[self.rank : padded : self.num_replicas]
        for offset in range(0, len(rank_groups), self.groups_per_batch):
            indices = []
            for group in rank_groups[offset : offset + self.groups_per_batch]:
                begin = group * self.group_size
                indices.extend(range(begin, begin + self.group_size))
            yield indices

    def __len__(self) -> int:
        return self.batches_per_rank

    def set_epoch(self, epoch: int) -> None:
        self.epoch = int(epoch)


def _pas_hidden_state_lm_head_forward(
    _module: torch.nn.Module, hidden_states: torch.Tensor
) -> torch.Tensor:
    """Skip full-vocabulary logits; the loss projects only yes/no head rows."""

    return hidden_states


def _pas_group_batch_sampler(
    dataset,
    *,
    batch_size: int,
    sampler=None,
    num_replicas: int | None = None,
    rank: int | None = None,
    config=None,
    pad: bool,
    **_,
) -> DistributedGroupBatchSampler:
    """Cosmos-RL batch-sampler factory for train and validation."""

    if num_replicas is None:
        num_replicas = sampler.num_replicas
    if rank is None:
        rank = sampler.rank
    shuffle = bool(getattr(sampler, "shuffle", False))
    loss_config = LossConfig.model_validate(
        config.custom.get("loss", {}) if config is not None else {}
    )
    return DistributedGroupBatchSampler(
        dataset,
        group_size=loss_config.candidate_group_size,
        batch_size=batch_size,
        num_replicas=num_replicas,
        rank=rank,
        shuffle=shuffle,
        pad=pad,
        seed=(config.train.train_policy.dataloader_seed if config is not None else 0),
    )


def pas_train_group_batch_sampler(
    dataset,
    *,
    batch_size: int,
    sampler=None,
    num_replicas: int | None = None,
    rank: int | None = None,
    config=None,
    **kwargs,
) -> DistributedGroupBatchSampler:
    return _pas_group_batch_sampler(
        dataset,
        batch_size=batch_size,
        sampler=sampler,
        num_replicas=num_replicas,
        rank=rank,
        config=config,
        pad=True,
        **kwargs,
    )


@TrainerRegistry.register(trainer_type="pas_rank_point_sft")
class PasRankPointTrainer(SFTTrainer):
    def __init__(self, *args, **kwargs):
        global _METRICS_PATH
        super().__init__(*args, **kwargs)
        custom = CustomConfig.model_validate(self.config.custom)
        if (
            self.parallel_dims.pp_enabled
            or self.parallel_dims.cp_enabled
            or self.config.train.sequence_packing
        ):
            raise ValueError("The selected recipe requires PP=CP=1 and sequence_packing=false")
        loss = custom.loss
        response_spec = build_binary_response_spec(
            self.data_packer.tokenizer, loss.positive_response, loss.negative_response
        )
        self.loss_fn = RankPointLoss(
            response_spec,
            group_size=loss.candidate_group_size,
            rank_weight=loss.rank_weight,
            point_weight=loss.point_weight,
            rank_temperature=loss.rank_temperature,
            point_temperature=loss.point_temperature,
            negative_point_weight=loss.negative_point_weight,
        )
        if not self.config.policy.enable_liger_fused_cross_entropy:
            raise ValueError("The selected recipe requires hidden-state fused-CE mode")
        hf_model = getattr(self.model, "model", None)
        if hf_model is None or not hasattr(hf_model, "lm_head"):
            raise ValueError("Could not locate the HF LM head")
        head = hf_model.lm_head
        if not getattr(head, "_pas_hidden_state_forward", False):
            head.forward = types.MethodType(_pas_hidden_state_lm_head_forward, head)
            head._pas_hidden_state_forward = True
        self.loss_fn.projection_weight = self.model.lm_head.weight
        if hasattr(self.model.lm_head, "lora_A"):
            raise ValueError("The supplied checkpoint does not train LM-head LoRA")
        structured = custom.structured_attribute
        assets = load_structured_attribute_assets(
            structured.train_records_path, structured.attribute_vocab_path
        )
        choice_loss = StructuredAttributeChoiceLoss(
            self.data_packer.tokenizer, self.model.lm_head.weight, assets.fields
        )
        self.loss_fn = RankPointWithStructuredAttributeLoss(self.loss_fn, choice_loss, structured)
        self._candidate_group_size = loss.candidate_group_size
        self._runtime_group_audited = False
        _METRICS_PATH = Path(self.config.train.output_dir) / "loss_metrics.jsonl"
        if _is_policy_master() and not self.config.train.resume:
            _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _METRICS_PATH.unlink(missing_ok=True)
        init_offline_wandb(self.config)

    def _assert_runtime_candidate_groups(self, batch):
        if len(batch) % self._candidate_group_size:
            raise ValueError("Runtime batch contains incomplete query groups")
        for start in range(0, len(batch), self._candidate_group_size):
            rows = batch[start : start + self._candidate_group_size]
            ids = {row.get("_pas_group_id") for row in rows}
            labels = [row.get("_pas_binary_label") for row in rows]
            if None in ids or len(ids) != 1:
                raise ValueError("Packing or dispatch reordered candidates across queries")
            if any(label not in (0, 1) for label in labels) or not 0 < sum(labels) < len(labels):
                raise ValueError("Runtime query group has invalid binary labels")

    def step_training(self, *args, **kwargs):
        batch = args[0] if args else kwargs.get("global_batch")
        if batch is None:
            raise ValueError("Training batch is unavailable")
        self._assert_runtime_candidate_groups(batch)
        self.loss_fn.reset_metrics()
        self.loss_fn.set_structured_records(
            [sample.get("_pas_structured_attributes") for sample in batch]
        )
        report = super().step_training(*args, **kwargs)
        self.loss_fn.assert_structured_records_consumed()
        for name, (metric_sum, metric_count) in self.loss_fn.metric_totals().items():
            value = self._distributed_metric_mean(metric_sum, metric_count)
            if value is not None:
                suffix = "_loss" if name in ("rank", "point") else ""
                report[f"train/{name}{suffix}"] = value
        return report

    def _distributed_metric_mean(self, metric_sum: torch.Tensor, metric_count: int) -> float | None:
        """Compute a count-weighted metric mean with identical collectives/rank."""

        totals = torch.stack(
            [metric_sum.float(), metric_sum.new_tensor(float(metric_count)).float()]
        )
        if self.parallel_dims.dp_replicate_enabled or self.parallel_dims.dp_shard_enabled:
            dist.all_reduce(
                totals,
                op=dist.ReduceOp.SUM,
                group=self.parallel_dims.mesh["dp"].get_group(),
            )
        if totals[1].item() == 0:
            return None
        return (totals[0] / totals[1]).item()


def main():
    configure_cuda_runtime()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_known_args()[0]
    with open(args.config, encoding="utf-8") as stream:
        config = toml.load(stream)
    if config.get("validation", {}).get("enable", False):
        raise ValueError("Evaluate checkpoints separately; source training validation is disabled")
    custom = CustomConfig.model_validate(config.get("custom", {}))
    assets = load_structured_attribute_assets(
        custom.structured_attribute.train_records_path,
        custom.structured_attribute.attribute_vocab_path,
    )

    def build_dataset(_config):
        return PasConversationDataset(
            custom, custom.train_dataset, structured_attribute_assets=assets
        )

    cosmos_rl.launcher.worker_entry.main(
        dataset=build_dataset,
        val_dataset=None,
        data_packer=PasCachedQwen3VLDataPacker(custom.visual_cache, custom.vision),
        batch_sampler=pas_train_group_batch_sampler,
        custom_logger_fns=[write_loss_metrics],
    )


if __name__ == "__main__":
    main()
