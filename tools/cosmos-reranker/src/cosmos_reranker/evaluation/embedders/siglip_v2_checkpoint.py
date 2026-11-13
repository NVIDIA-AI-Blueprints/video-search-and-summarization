# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""PyTorch SigLIP2 encoder for TAO/Lightning checkpoints.

This module is deliberately independent of TAO. It reconstructs the
``siglip2-so400m-patch16-256`` architecture with Transformers, maps the
``model.backbone.inner.*`` tensors saved by TAO, and downloads the matching public tokenizer on first use.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image


_CHECKPOINT_PREFIX = "model.backbone.inner."
_PATCH_WEIGHT_KEY = "vision_model.embeddings.patch_embedding.weight"
_IMAGE_SIZE = 256
_PATCH_SIZE = 16
_NUM_PATCHES = (_IMAGE_SIZE // _PATCH_SIZE) ** 2
_MAX_TEXT_TOKENS = 64
_EMBED_DIM = 1152

_TOKENIZER_ID = "google/siglip2-so400m-patch16-256"


def resolve_tokenizer_dir(directory: str | Path | None = None) -> Path:
    """Reuse local tokenizer assets, downloading missing files through the HF cache."""
    from huggingface_hub import snapshot_download

    path = Path(directory).expanduser().resolve() if directory else None
    required = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")
    if path and all((path / name).is_file() for name in required):
        return path
    snapshot = Path(snapshot_download(_TOKENIZER_ID, allow_patterns=list(required)))
    if path is None:
        return snapshot
    import shutil

    path.mkdir(parents=True, exist_ok=True)
    for name in required:
        if not (path / name).exists():
            shutil.copyfile(snapshot / name, path / name)
    return path


@dataclass(frozen=True)
class StateDictMappingReport:
    """Summary of a TAO-to-Transformers state-dict conversion."""

    mapped_keys: int
    reshaped_keys: tuple[str, ...]
    ignored_keys: tuple[str, ...]
    unexpected_keys: tuple[str, ...]
    shape_mismatches: tuple[str, ...]
    missing_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckpointMetadata:
    """Small, JSON-friendly identity and training metadata for a checkpoint."""

    path: str
    size_bytes: int
    mtime_ns: int
    fingerprint: str
    epoch: int | None
    global_step: int | None
    lightning_version: str | None
    state_dict_keys: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def checkpoint_fingerprint(path: str | Path) -> str:
    """Return a cheap identity for local embedding-cache metadata."""

    checkpoint_path = Path(path).expanduser().resolve()
    stat = checkpoint_path.stat()
    identity = f"siglip2-stat-v1\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
    return hashlib.sha256(identity).hexdigest()[:20]


def map_tao_siglip2_state_dict(
    state_dict: Mapping[str, torch.Tensor],
    target_state_dict: Mapping[str, torch.Tensor] | None = None,
    *,
    prefix: str = _CHECKPOINT_PREFIX,
) -> tuple[dict[str, torch.Tensor], StateDictMappingReport]:
    """Map TAO Lightning keys to an installed Transformers ``SiglipModel``."""

    mapped: dict[str, torch.Tensor] = {}
    reshaped: list[str] = []
    ignored: list[str] = []
    unexpected: list[str] = []
    mismatches: list[str] = []

    for checkpoint_key, tensor in state_dict.items():
        if not checkpoint_key.startswith(prefix):
            ignored.append(checkpoint_key)
            continue

        target_key = checkpoint_key[len(prefix) :]
        converted = tensor
        target_expects_linear_patches = (
            target_state_dict is not None
            and target_key in target_state_dict
            and target_state_dict[target_key].ndim == 2
        )
        if target_key == _PATCH_WEIGHT_KEY and tensor.ndim == 4 and target_expects_linear_patches:
            converted = tensor.reshape(tensor.shape[0], -1)
            reshaped.append(target_key)

        if (
            target_state_dict is not None
            and target_key in target_state_dict
            and converted.numel() == 1
            and target_state_dict[target_key].numel() == 1
            and converted.shape != target_state_dict[target_key].shape
        ):
            converted = converted.reshape(target_state_dict[target_key].shape)
            reshaped.append(target_key)

        if target_state_dict is not None:
            if target_key not in target_state_dict:
                unexpected.append(target_key)
                continue
            expected_shape = tuple(target_state_dict[target_key].shape)
            actual_shape = tuple(converted.shape)
            if actual_shape != expected_shape:
                mismatches.append(
                    f"{target_key}: checkpoint={actual_shape}, target={expected_shape}"
                )
                continue

        mapped[target_key] = converted

    missing = ()
    if target_state_dict is not None:
        missing = tuple(sorted(set(target_state_dict) - set(mapped)))

    report = StateDictMappingReport(
        mapped_keys=len(mapped),
        reshaped_keys=tuple(sorted(reshaped)),
        ignored_keys=tuple(sorted(ignored)),
        unexpected_keys=tuple(sorted(unexpected)),
        shape_mismatches=tuple(sorted(mismatches)),
        missing_keys=missing,
    )
    return mapped, report


def merge_tao_lora_state_dict(
    state_dict: Mapping[str, torch.Tensor],
    *,
    scale: float = 2.0,
) -> dict[str, torch.Tensor]:
    """Fold TAO ``LoRALinear`` wrappers into ordinary Linear weights.

    TAO checkpoints save adapted projections as ``original.weight``,
    ``lora_A``, and ``lora_B``.  The PAS SigLIP2 training specs use an
    alpha/rank ratio of 2, matching the established local inference path in
    fusion-search-utils.
    """

    merged = dict(state_dict)
    suffix = ".original.weight"
    bases = [key[: -len(suffix)] for key in state_dict if key.endswith(suffix)]
    for base in bases:
        original_key = f"{base}.original.weight"
        lora_a_key = f"{base}.lora_A"
        lora_b_key = f"{base}.lora_B"
        original = state_dict[original_key]
        lora_a = state_dict.get(lora_a_key)
        lora_b = state_dict.get(lora_b_key)
        if lora_a is None or lora_b is None:
            raise KeyError(f"Incomplete TAO LoRA weights for {base}")

        merged[f"{base}.weight"] = (
            original.float() + (lora_b.float() @ lora_a.float()) * scale
        ).to(dtype=original.dtype)
        bias_key = f"{base}.original.bias"
        if bias_key in state_dict:
            merged[f"{base}.bias"] = state_dict[bias_key]

        for key in (original_key, bias_key, lora_a_key, lora_b_key):
            merged.pop(key, None)
    return merged


def build_siglip2_so400m_config():
    """Construct the checkpoint's architecture without a network lookup."""

    from transformers import SiglipConfig, SiglipTextConfig, SiglipVisionConfig

    text_config = SiglipTextConfig(
        vocab_size=256_000,
        hidden_size=1_152,
        intermediate_size=4_304,
        num_hidden_layers=27,
        num_attention_heads=16,
        max_position_embeddings=_MAX_TEXT_TOKENS,
        hidden_act="gelu_pytorch_tanh",
        layer_norm_eps=1e-6,
        attention_dropout=0.0,
        projection_size=_EMBED_DIM,
    )
    vision_config = SiglipVisionConfig(
        hidden_size=1_152,
        intermediate_size=4_304,
        num_hidden_layers=27,
        num_attention_heads=16,
        num_channels=3,
        image_size=_IMAGE_SIZE,
        patch_size=_PATCH_SIZE,
        hidden_act="gelu_pytorch_tanh",
        layer_norm_eps=1e-6,
        attention_dropout=0.0,
    )
    return SiglipConfig(text_config=text_config.to_dict(), vision_config=vision_config.to_dict())


def _numpy_safe_globals() -> list[Any]:
    """Globals needed by this Lightning file under ``weights_only=True``."""

    import numpy.core.multiarray as multiarray  # noqa: PLC0415

    return [
        (multiarray.scalar, "numpy.core.multiarray.scalar"),
        (np.dtype, "numpy.dtype"),
        type(np.dtype(np.float64)),
        type(np.dtype(np.uint32)),
    ]


def _safe_load_checkpoint(path: Path) -> dict[str, Any]:
    """Load a Lightning checkpoint without enabling arbitrary pickle code."""

    with torch.serialization.safe_globals(_numpy_safe_globals()):
        try:
            checkpoint = torch.load(
                path,
                map_location="cpu",
                weights_only=True,
                mmap=True,
            )
        except RuntimeError as error:
            if "mmap" not in str(error).lower():
                raise
            checkpoint = torch.load(
                path,
                map_location="cpu",
                weights_only=True,
                mmap=False,
            )
    if not isinstance(checkpoint, dict):
        raise TypeError(f"Expected a checkpoint dict, got {type(checkpoint).__name__}")
    return checkpoint


def _resolve_device(device: str) -> torch.device:
    if device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    resolved = torch.device(device)
    if resolved.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(f"CUDA device requested but CUDA is unavailable: {device}")
    return resolved


def _resolve_dtype(dtype: str | torch.dtype, device: torch.device) -> torch.dtype:
    if isinstance(dtype, torch.dtype):
        resolved = dtype
    elif dtype == "auto":
        resolved = torch.float16 if device.type == "cuda" else torch.float32
    else:
        choices = {
            "float32": torch.float32,
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
        }
        try:
            resolved = choices[dtype]
        except KeyError as error:
            raise ValueError(
                f"Unsupported dtype {dtype!r}; choose auto, float32, float16, or bfloat16"
            ) from error
    if device.type == "cpu" and resolved == torch.float16:
        raise ValueError("float16 inference on CPU is unsupported; use float32 or bfloat16")
    return resolved


def _pooler_output(output: Any) -> torch.Tensor:
    """Extract pooled features across supported Transformers return styles."""

    if isinstance(output, torch.Tensor):
        return output
    pooled = getattr(output, "pooler_output", None)
    if pooled is not None:
        return pooled
    if isinstance(output, (tuple, list)) and len(output) > 1:
        return output[1]
    raise TypeError(f"SigLIP2 output has no pooled embedding: {type(output).__name__}")


class SigLIP2CheckpointEmbedder:
    """Normalized image/text encoder backed by a fine-tuned Lightning file."""

    name = "siglip2_checkpoint"

    def __init__(
        self,
        checkpoint_path: str | Path,
        *,
        tokenizer_dir: str | Path | None = None,
        device: str = "auto",
        image_batch_size: int = 16,
        text_batch_size: int = 64,
        dtype: str | torch.dtype = "auto",
    ) -> None:
        from transformers import AutoTokenizer, SiglipModel

        checkpoint_path = Path(checkpoint_path).expanduser().resolve()
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"SigLIP2 checkpoint not found: {checkpoint_path}")
        if image_batch_size <= 0 or text_batch_size <= 0:
            raise ValueError("image_batch_size and text_batch_size must be positive")

        tokenizer_path = resolve_tokenizer_dir(tokenizer_dir)

        self.checkpoint_path = checkpoint_path
        self.tokenizer_dir = tokenizer_path
        self.device = _resolve_device(device)
        self.dtype = _resolve_dtype(dtype, self.device)
        self.image_batch_size = image_batch_size
        self.text_batch_size = text_batch_size

        checkpoint = _safe_load_checkpoint(checkpoint_path)
        raw_state_dict = checkpoint.get("state_dict")
        if not isinstance(raw_state_dict, Mapping):
            raise KeyError(f"Checkpoint has no mapping-valued state_dict: {checkpoint_path}")

        stat = checkpoint_path.stat()
        self.checkpoint_metadata = CheckpointMetadata(
            path=str(checkpoint_path),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            fingerprint=checkpoint_fingerprint(checkpoint_path),
            epoch=_optional_int(checkpoint.get("epoch")),
            global_step=_optional_int(checkpoint.get("global_step")),
            lightning_version=_optional_str(checkpoint.get("pytorch-lightning_version")),
            state_dict_keys=len(raw_state_dict),
        )

        outer_logit_scale = raw_state_dict.get("model.logit_scale")
        outer_logit_bias = raw_state_dict.get("model.logit_bias")
        if outer_logit_scale is None:
            outer_logit_scale = raw_state_dict.get(f"{_CHECKPOINT_PREFIX}logit_scale")
        if outer_logit_bias is None:
            outer_logit_bias = raw_state_dict.get(f"{_CHECKPOINT_PREFIX}logit_bias")
        if not isinstance(outer_logit_scale, torch.Tensor) or outer_logit_scale.numel() != 1:
            raise KeyError("Checkpoint has no scalar model.logit_scale")
        if not isinstance(outer_logit_bias, torch.Tensor) or outer_logit_bias.numel() != 1:
            raise KeyError("Checkpoint has no scalar model.logit_bias")
        self.logit_scale_log = float(outer_logit_scale.detach().float().cpu().item())
        self.logit_scale = float(np.exp(self.logit_scale_log))
        self.logit_bias = float(outer_logit_bias.detach().float().cpu().item())

        raw_state_dict = merge_tao_lora_state_dict(raw_state_dict)

        config = build_siglip2_so400m_config()
        with torch.device("meta"):
            model = SiglipModel(config)
        target_state_dict = model.state_dict()
        mapped, report = map_tao_siglip2_state_dict(raw_state_dict, target_state_dict)
        self.mapping_report = report
        if report.missing_keys or report.unexpected_keys or report.shape_mismatches:
            raise RuntimeError(_format_mapping_failure(report))

        model.load_state_dict(mapped, strict=True, assign=True)
        if model.text_model.embeddings.position_ids.is_meta:
            model.text_model.embeddings.position_ids = torch.arange(
                _MAX_TEXT_TOKENS, dtype=torch.long
            ).unsqueeze(0)
        if model.vision_model.embeddings.position_ids.is_meta:
            model.vision_model.embeddings.position_ids = torch.arange(
                _NUM_PATCHES, dtype=torch.long
            ).unsqueeze(0)
        del mapped, raw_state_dict, checkpoint, target_state_dict

        model.requires_grad_(False)
        self.model = model.to(device=self.device, dtype=self.dtype).eval()
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(tokenizer_path),
            local_files_only=True,
            trust_remote_code=False,
        )

    @property
    def embedding_dim(self) -> int:
        return _EMBED_DIM

    @staticmethod
    def _preprocess_images(
        images: Sequence[Image.Image | str | Path],
    ) -> dict[str, torch.Tensor]:
        """Resize and normalize images for the fixed-resolution SigLIP tower."""

        pixel_values = np.empty((len(images), 3, _IMAGE_SIZE, _IMAGE_SIZE), dtype=np.float32)
        for index, source in enumerate(images):
            if isinstance(source, Image.Image):
                image = source
                close_image = False
            else:
                image = Image.open(source)
                close_image = True
            try:
                resized = image.convert("RGB").resize(
                    (_IMAGE_SIZE, _IMAGE_SIZE), Image.Resampling.BILINEAR
                )
                array = np.asarray(resized, dtype=np.float32) * (1.0 / 255.0)
                array = (array - 0.5) / 0.5
                pixel_values[index] = np.transpose(array, (2, 0, 1))
            finally:
                if close_image:
                    image.close()

        return {"pixel_values": torch.from_numpy(pixel_values)}

    def _tokenize(self, texts: Sequence[str]) -> dict[str, torch.Tensor]:
        encoded = self.tokenizer(
            [text.lower() for text in texts],
            padding="max_length",
            truncation=True,
            max_length=_MAX_TEXT_TOKENS,
            return_attention_mask=True,
            return_tensors="pt",
        )
        # TAO deployable SigLIP2 attends to the fixed-length padded sequence.
        return {
            "input_ids": encoded["input_ids"],
            "attention_mask": torch.ones_like(encoded["attention_mask"]),
        }

    def embed_images(self, images: Sequence[Image.Image | str | Path]) -> np.ndarray:
        if not images:
            return np.zeros((0, _EMBED_DIM), dtype=np.float32)
        batches: list[np.ndarray] = []
        with torch.inference_mode():
            for start in range(0, len(images), self.image_batch_size):
                inputs = self._preprocess_images(images[start : start + self.image_batch_size])
                inputs = {
                    key: value.to(
                        self.device,
                        dtype=self.dtype if key == "pixel_values" else None,
                        non_blocking=True,
                    )
                    for key, value in inputs.items()
                }
                pooled = _pooler_output(self.model.get_image_features(**inputs))
                normalized = F.normalize(pooled.float(), dim=-1)
                batches.append(normalized.cpu().numpy())
        return np.concatenate(batches, axis=0).astype(np.float32, copy=False)

    def embed_text(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, _EMBED_DIM), dtype=np.float32)
        batches: list[np.ndarray] = []
        with torch.inference_mode():
            for start in range(0, len(texts), self.text_batch_size):
                inputs = self._tokenize(texts[start : start + self.text_batch_size])
                inputs = {
                    key: value.to(self.device, non_blocking=True) for key, value in inputs.items()
                }
                pooled = _pooler_output(self.model.get_text_features(**inputs))
                normalized = F.normalize(pooled.float(), dim=-1)
                batches.append(normalized.cpu().numpy())
        return np.concatenate(batches, axis=0).astype(np.float32, copy=False)

    def score(self, image_embeddings: np.ndarray, text_embeddings: np.ndarray) -> np.ndarray:
        """Return fine-tuned SigLIP logits for all image/text pairs."""

        return self.logit_scale * (image_embeddings @ text_embeddings.T) + self.logit_bias


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, np.integer)) else None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _format_mapping_failure(report: StateDictMappingReport) -> str:
    sections = ["TAO checkpoint does not exactly match SigLIP2-SO400M patch16/256."]
    if report.missing_keys:
        sections.append(f"missing={len(report.missing_keys)}: {report.missing_keys[:5]}")
    if report.unexpected_keys:
        sections.append(f"unexpected={len(report.unexpected_keys)}: {report.unexpected_keys[:5]}")
    if report.shape_mismatches:
        sections.append(
            f"shape_mismatches={len(report.shape_mismatches)}: {report.shape_mismatches[:5]}"
        )
    return " ".join(sections)


__all__ = [
    "CheckpointMetadata",
    "SigLIP2CheckpointEmbedder",
    "StateDictMappingReport",
    "build_siglip2_so400m_config",
    "checkpoint_fingerprint",
    "map_tao_siglip2_state_dict",
]
