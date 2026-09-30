# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build a sharded cache immediately before trainable CR3 vision blocks.

The script is torchrun-friendly but does not require a process group. Each
rank writes independent shards and a rank-local manifest, so all eight GPUs
stay busy without cross-rank synchronization.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import os
import time
import types
from collections import deque
from functools import partial
from pathlib import Path

import torch
import torch.nn.functional as F

# The CUDA-12.8 cache builder uses a PyTorch version older than the optional
# torchao wheel present in some images.  Transformers otherwise imports that
# incompatible optional package even though this cache is BF16 and unquantized.
if tuple(int(value) for value in torch.__version__.split("+")[0].split(".")[:2]) < (
    2,
    11,
):
    _original_find_spec = importlib.util.find_spec

    def _find_spec_without_torchao(name, *args, **kwargs):
        if name == "torchao" or name.startswith("torchao."):
            return None
        return _original_find_spec(name, *args, **kwargs)

    importlib.util.find_spec = _find_spec_without_torchao

from qwen_vl_utils.vision_process import fetch_image
from safetensors.torch import save_file
from transformers import AutoModelForImageTextToText, AutoProcessor

try:
    from cosmos_reranker.finetuning.visual_cache import visual_cache_key
except ModuleNotFoundError:
    from visual_cache import visual_cache_key


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-lists",
        type=Path,
        nargs="+",
        required=True,
        help="Text files containing one image path relative to --image-root per line.",
    )
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="/models/Cosmos3-Nano-VLM")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--min-pixels", type=int)
    parser.add_argument("--max-pixels", type=int)
    parser.add_argument("--decode-workers", type=int, default=8)
    parser.add_argument(
        "--async-write",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Overlap pinned-memory GPU copies and safetensors writes with encoding.",
    )
    parser.add_argument("--max-pending-writes", type=int, default=2)
    parser.add_argument("--log-every", type=int, default=10)
    parser.add_argument(
        "--linear-patch-embed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Evaluate the non-overlapping Conv3d patch projection as an equivalent "
            "BF16 GEMM; useful when cuDNN Conv3d is unavailable."
        ),
    )
    parser.add_argument(
        "--prefix-block",
        type=int,
        choices=range(0, 27),
        default=24,
        help=(
            "Cache the frozen visual state immediately before this block plus "
            "the earlier deep-stack outputs. Prefix block 0 caches only the "
            "patch/position embedding, so every transformer block remains "
            "trainable. Blocks at and after this boundary and the visual merger "
            "remain trainable (default: 24)."
        ),
    )
    return parser.parse_args()


def input_images(
    image_lists: list[Path],
    image_root: Path,
) -> list[Path]:
    """Read ordered image aliases and deduplicate without filesystem metadata IO."""

    unique: dict[str, Path] = {}
    for image_list in image_lists:
        with image_list.open(encoding="utf-8") as handle:
            for line in handle:
                image = line.strip()
                if image:
                    unique.setdefault(image, image_root / image)
    return list(unique.values())


def processor_batch(
    processor,
    paths: list[Path],
    *,
    min_pixels: int | None,
    max_pixels: int | None,
    decode_pool: concurrent.futures.Executor,
):
    vision_kwargs = {
        key: value
        for key, value in {
            "min_pixels": min_pixels,
            "max_pixels": max_pixels,
        }.items()
        if value is not None
    }
    elements = [{"type": "image", "image": str(path), **vision_kwargs} for path in paths]
    images = list(decode_pool.map(partial(fetch_image, image_patch_size=16), elements))
    return processor(
        text=[processor.image_token] * len(paths),
        images=images,
        padding=True,
        do_resize=False,
        return_tensors="pt",
    )


def save_after_cuda_copy(
    event: torch.cuda.Event, tensors: dict[str, torch.Tensor], output_path: Path
) -> None:
    event.synchronize()
    save_file(tensors, output_path)


def visual_prefix_features(visual, pixel_values, grid_thw, prefix_block: int):
    """Run only the frozen prefix of Qwen3-VL's vision transformer."""

    hidden_states = visual.patch_embed(pixel_values)
    hidden_states = hidden_states + visual.fast_pos_embed_interpolate(grid_thw)
    rotary_pos_emb = visual.rot_pos_emb(grid_thw)
    seq_len = hidden_states.shape[0]
    rotary_pos_emb = rotary_pos_emb.reshape(seq_len, -1)
    emb = torch.cat((rotary_pos_emb, rotary_pos_emb), dim=-1)
    position_embeddings = (emb.cos(), emb.sin())
    cu_seqlens = torch.repeat_interleave(grid_thw[:, 1] * grid_thw[:, 2], grid_thw[:, 0]).cumsum(
        dim=0,
        dtype=grid_thw.dtype if torch.jit.is_tracing() else torch.int32,
    )
    cu_seqlens = F.pad(cu_seqlens, (1, 0), value=0)

    deepstack = []
    for layer_num in range(prefix_block):
        hidden_states = visual.blocks[layer_num](
            hidden_states,
            cu_seqlens=cu_seqlens,
            position_embeddings=position_embeddings,
        )
        if layer_num in visual.deepstack_visual_indexes:
            merger_index = visual.deepstack_visual_indexes.index(layer_num)
            deepstack.append(visual.deepstack_merger_list[merger_index](hidden_states))
    expected = sum(index < prefix_block for index in visual.deepstack_visual_indexes)
    if len(deepstack) != expected:
        raise ValueError(f"Expected {expected} prefix deep-stack outputs, got {len(deepstack)}")
    if deepstack:
        deepstack_features = torch.stack(deepstack, dim=1)
    else:
        merged_tokens = sum(
            int(t * h * w) // (visual.config.spatial_merge_size**2) for t, h, w in grid_thw.tolist()
        )
        deepstack_features = hidden_states.new_empty(
            (merged_tokens, 0, visual.config.out_hidden_size)
        )
    return hidden_states.contiguous(), deepstack_features.contiguous()


def enable_linear_patch_embed(model) -> None:
    """Replace non-overlapping Conv3d with its mathematically identical GEMM."""

    def linear_forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        target_dtype = self.proj.weight.dtype
        return F.linear(
            hidden_states.to(dtype=target_dtype),
            self.proj.weight.flatten(1),
            self.proj.bias,
        )

    patch_embed = model.model.visual.patch_embed
    patch_embed.forward = types.MethodType(linear_forward, patch_embed)


def main() -> None:
    args = parse_args()
    if args.batch_size <= 0 or args.decode_workers <= 0:
        raise ValueError("--batch-size and --decode-workers must be positive")
    if args.max_pending_writes <= 0:
        raise ValueError("--max-pending-writes must be positive")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable")
    rank = int(os.environ.get("RANK", os.environ.get("LOCAL_RANK", "0")))
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    device = torch.device("cuda", local_rank)
    torch.cuda.set_device(device)
    torch.backends.cudnn.enabled = False

    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = input_images(args.image_lists, args.image_root)[rank::world_size]

    processor = AutoProcessor.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(
        args.model,
        dtype=torch.bfloat16,
        attn_implementation="flash_attention_2",
        local_files_only=True,
    )
    model.eval()
    if args.linear_patch_embed:
        enable_linear_patch_embed(model)
    # Cache construction only executes the vision tower. Moving the entire
    # 8B language model on every rank wastes GPU transfer time and can thrash a
    # network filesystem when eight ranks fault all language tensors at once.
    model.model.visual.to(device)

    entries = {}
    total_tokens = 0
    batches = [
        paths[begin : begin + args.batch_size] for begin in range(0, len(paths), args.batch_size)
    ]
    copy_stream = torch.cuda.Stream(device=device) if args.async_write else None
    pending_writes: deque[concurrent.futures.Future] = deque()
    torch.cuda.synchronize(device)
    started = time.perf_counter()
    with (
        torch.inference_mode(),
        concurrent.futures.ThreadPoolExecutor(max_workers=args.decode_workers) as decode_pool,
        concurrent.futures.ThreadPoolExecutor(max_workers=1) as prefetch_pool,
        concurrent.futures.ThreadPoolExecutor(max_workers=1) as writer_pool,
    ):

        def submit_preprocess(shard_paths: list[Path]):
            return prefetch_pool.submit(
                processor_batch,
                processor,
                shard_paths,
                min_pixels=args.min_pixels,
                max_pixels=args.max_pixels,
                decode_pool=decode_pool,
            )

        input_future = submit_preprocess(batches[0]) if batches else None
        for shard_index, shard_paths in enumerate(batches):
            assert input_future is not None
            inputs = input_future.result()
            input_future = (
                submit_preprocess(batches[shard_index + 1])
                if shard_index + 1 < len(batches)
                else None
            )
            pixel_values = inputs["pixel_values"].to(device)
            grids = inputs["image_grid_thw"].to(device)
            prefix_features, prefix_deepstack = visual_prefix_features(
                model.model.visual,
                pixel_values,
                grids,
                args.prefix_block,
            )
            output_tensors = {
                "prefix_features": prefix_features,
                "deepstack_features": prefix_deepstack,
            }
            shard_name = f"rank{rank:02d}_shard{shard_index:06d}.safetensors"
            shard_path = args.output_dir / shard_name

            if args.async_write:
                assert copy_stream is not None
                cpu_tensors = {
                    name: torch.empty(
                        value.shape,
                        dtype=value.dtype,
                        device="cpu",
                        pin_memory=True,
                    )
                    for name, value in output_tensors.items()
                }
                copy_stream.wait_stream(torch.cuda.current_stream(device))
                with torch.cuda.stream(copy_stream):
                    for name, value in output_tensors.items():
                        cpu_tensors[name].copy_(value, non_blocking=True)
                    copy_done = torch.cuda.Event()
                    copy_done.record(copy_stream)
                for value in output_tensors.values():
                    value.record_stream(copy_stream)
                pending_writes.append(
                    writer_pool.submit(save_after_cuda_copy, copy_done, cpu_tensors, shard_path)
                )
                while len(pending_writes) >= args.max_pending_writes:
                    pending_writes.popleft().result()
            else:
                save_file(
                    {name: value.cpu().contiguous() for name, value in output_tensors.items()},
                    shard_path,
                )

            offset = 0
            deepstack_offset = 0
            for path, grid in zip(shard_paths, grids.tolist()):
                patch_length = int(grid[0] * grid[1] * grid[2])
                merged_length = patch_length // (model.model.visual.config.spatial_merge_size**2)
                entry = {
                    "shard": shard_name,
                    "offset": offset,
                    "length": patch_length,
                    "deepstack_offset": deepstack_offset,
                    "deepstack_length": merged_length,
                    "grid_thw": grid,
                }
                offset += patch_length
                deepstack_offset += merged_length
                entries[visual_cache_key(path)] = entry
            batch_visual_tokens = deepstack_offset
            total_tokens += batch_visual_tokens
            images_done = min((shard_index + 1) * args.batch_size, len(paths))
            if (
                shard_index == 0
                or shard_index + 1 == len(batches)
                or (shard_index + 1) % args.log_every == 0
            ):
                elapsed = time.perf_counter() - started
                print(
                    json.dumps(
                        {
                            "rank": rank,
                            "shard": shard_index,
                            "images_done": images_done,
                            "images_total": len(paths),
                            "images_per_second": round(images_done / elapsed, 3),
                            "visual_tokens": batch_visual_tokens,
                        }
                    ),
                    flush=True,
                )

        while pending_writes:
            pending_writes.popleft().result()

    elapsed = time.perf_counter() - started

    manifest = {
        "version": 2,
        "rank": rank,
        "world_size": world_size,
        "model": args.model,
        "feature_layout": "visual_prefix_plus_deepstack",
        "prefix_block": args.prefix_block,
        "dtype": "bfloat16",
        "preprocessing": {
            "image_patch_size": 16,
            "spatial_merge_size": 2,
            "min_pixels": args.min_pixels,
            "max_pixels": args.max_pixels,
        },
        "entries": entries,
        "images": len(entries),
        "visual_tokens": total_tokens,
        "build": {
            "batch_size": args.batch_size,
            "decode_workers": args.decode_workers,
            "async_write": args.async_write,
            "linear_patch_embed": args.linear_patch_embed,
            "prefix_block": args.prefix_block,
            "elapsed_seconds": elapsed,
            "images_per_second": len(paths) / elapsed if elapsed else 0.0,
        },
    }
    manifest_path = args.output_dir / f"manifest_rank{rank:02d}.json"
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "manifest": str(manifest_path),
                "images": len(entries),
                "elapsed_seconds": round(elapsed, 3),
                "images_per_second": round(len(paths) / elapsed, 3),
                "max_cuda_memory_gib": round(torch.cuda.max_memory_allocated(device) / 2**30, 3),
            }
        )
    )


if __name__ == "__main__":
    main()
