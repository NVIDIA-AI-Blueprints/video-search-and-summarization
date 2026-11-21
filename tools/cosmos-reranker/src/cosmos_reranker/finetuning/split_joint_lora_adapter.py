#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Split a joint CR3 LoRA export into language and visual adapters.

The visual adapter can be merged into the base checkpoint for vLLM, while the
language adapter remains dynamically loadable.  Applying both pieces is
mathematically identical to applying the original joint adapter because they
target disjoint modules.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from safetensors.torch import load_file, save_file


LANGUAGE_TARGETS = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "up_proj",
    "down_proj",
    "gate_proj",
]


def _copy_metadata(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    for path in source.iterdir():
        if path.is_file() and path.name not in {
            "adapter_model.safetensors",
            "adapter_config.json",
        }:
            shutil.copy2(path, output / path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument(
        "--visual-adapter",
        type=Path,
        default=None,
        help=(
            "Optional parent adapter supplying frozen visual tensors when the "
            "training export contains trainable language tensors only."
        ),
    )
    parser.add_argument("--language-output", type=Path, required=True)
    parser.add_argument("--visual-output", type=Path, required=True)
    args = parser.parse_args()

    state = load_file(str(args.adapter / "adapter_model.safetensors"))
    visual = {key: value for key, value in state.items() if ".visual." in key}
    language = {key: value for key, value in state.items() if ".visual." not in key}
    visual_source = args.adapter
    if not visual and args.visual_adapter is not None:
        parent_state = load_file(str(args.visual_adapter / "adapter_model.safetensors"))
        visual = {key: value for key, value in parent_state.items() if ".visual." in key}
        visual_source = args.visual_adapter
    if visual_source == args.adapter:
        split_is_complete = len(language) + len(visual) == len(state)
    else:
        # The training export is language-only; visual tensors came from the
        # explicitly supplied frozen parent adapter.
        split_is_complete = len(language) == len(state)
    if not visual or not language or not split_is_complete:
        raise ValueError(
            f"Invalid split: total={len(state)}, language={len(language)}, "
            f"visual={len(visual)}, visual_source={visual_source}"
        )

    config = json.loads((args.adapter / "adapter_config.json").read_text(encoding="utf-8"))
    visual_targets = [target for target in config["target_modules"] if "visual" in target]
    if not visual_targets:
        raise ValueError("adapter_config.json contains no visual target modules")

    for output, tensors, targets in (
        (args.language_output, language, LANGUAGE_TARGETS),
        (args.visual_output, visual, visual_targets),
    ):
        _copy_metadata(args.adapter, output)
        save_file(tensors, str(output / "adapter_model.safetensors"))
        split_config = dict(config)
        split_config["target_modules"] = targets
        (output / "adapter_config.json").write_text(
            json.dumps(split_config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "source": str(args.adapter),
                "language_output": str(args.language_output),
                "language_tensors": len(language),
                "visual_output": str(args.visual_output),
                "visual_tensors": len(visual),
                "visual_source": str(visual_source),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
