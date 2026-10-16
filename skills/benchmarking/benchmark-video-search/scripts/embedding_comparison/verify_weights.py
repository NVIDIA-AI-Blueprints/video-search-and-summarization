#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify a deployed SigLIP2 ONNX checkpoint against a reference.

Two independent checks, usable separately:

1. Byte hash: sha256 the deployed ONNX external-data weights file and compare
   it to an expected hash (the NGC ``deployable_v2.0`` weights hash). A match
   proves the file is the unmodified NGC deployable.

2. Tensor-multiset compare: extract every ONNX initializer tensor and every
   PyTorch state-dict tensor, key each by ``(shape, dtype, sha256(bytes))``, and
   compare the multisets. This is name-agnostic and format-agnostic, so it can
   settle ``deployed ONNX == fine-tuned .pth`` across the two serializations
   (a direct file-hash compare is meaningless across formats).

torch/onnx are optional (the embedding_comparison harness keeps heavy ML deps
external). The byte-hash check runs with the standard library alone; the
tensor compare is skipped with a clear report when either is absent.

Reference hashes recorded from the PAS evaluation record and the live fetch:

  deployed ONNX weights.bin (live fetch) ... 038ff3be8d9867a9b563ee211b0d4c3f1262c11bf0d23e2940d491bbe429ef6f
  repo fine-tuned siglip_v2_v2.0.pth ......... 8eae363cfa4a486d3ed9d5850248d2986b56fa0409febea4bee6419df58fff81
  repo experiment.yaml ..................... 0756d5ae649bf1125564d59d79cdf5d570bc8d067edb8a420e328aa427127e99
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from comparison_common import sha256_file

# Recorded reference hashes (see module docstring for provenance).
DEPLOYED_ONNX_WEIGHTS_SHA256 = "038ff3be8d9867a9b563ee211b0d4c3f1262c11bf0d23e2940d491bbe429ef6f"
REPO_FINETUNED_PTH_SHA256 = "8eae363cfa4a486d3ed9d5850248d2986b56fa0409febea4bee6419df58fff81"
REPO_EXPERIMENT_YAML_SHA256 = "0756d5ae649bf1125564d59d79cdf5d570bc8d067edb8a420e328aa427127e99"

REFERENCES = {
    "deployed": DEPLOYED_ONNX_WEIGHTS_SHA256,
    "finetuned-pth": REPO_FINETUNED_PTH_SHA256,
}


def _tensor_key(array) -> tuple:
    """Identity key for a tensor: (shape, dtype, sha256 of raw bytes)."""
    return (tuple(int(s) for s in array.shape), str(array.dtype), hashlib.sha256(bytes(array)).hexdigest())


def onnx_initializer_keys(onnx_path: Path) -> list[tuple]:
    """All ONNX initializer tensors as identity keys (loads external data)."""
    import onnx
    from onnx import numpy_helper
    model = onnx.load(str(onnx_path), load_external_data=True)
    return [_tensor_key(numpy_helper.to_array(init)) for init in model.graph.initializer]


def _walk_tensors(obj):
    """Yield every leaf torch.Tensor found anywhere in a loaded checkpoint."""
    import torch
    if isinstance(obj, torch.Tensor):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _walk_tensors(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _walk_tensors(value)


def pth_tensor_keys(pth_path: Path) -> list[tuple]:
    """All leaf tensors in a PyTorch checkpoint as identity keys."""
    import torch
    state = torch.load(str(pth_path), map_location="cpu")
    return [_tensor_key(t.detach().cpu().numpy()) for t in _walk_tensors(state)]


def compare_multiset(onnx_keys: list[tuple], pth_keys: list[tuple]) -> dict:
    """Compare two tensor multisets; name-agnostic, format-agnostic."""
    from collections import Counter
    onnx_bag, pth_bag = Counter(onnx_keys), Counter(pth_keys)
    common = sum((onnx_bag & pth_bag).values())
    only_onnx = sum((onnx_bag - pth_bag).values())
    only_pth = sum((pth_bag - onnx_bag).values())
    return {
        "onnx_tensor_count": len(onnx_keys),
        "pth_tensor_count": len(pth_keys),
        "tensors_matched": common,
        "tensors_only_in_onnx": only_onnx,
        "tensors_only_in_pth": only_pth,
        "weights_identical": only_onnx == 0 and only_pth == 0 and common == len(onnx_keys),
    }


def verify(args) -> dict:
    onnx_weights_sha = sha256_file(args.onnx_weights)
    expected = args.expected or REFERENCES.get(args.reference or "")
    report = {
        "onnx_weights": str(args.onnx_weights),
        "onnx_weights_sha256": onnx_weights_sha,
        "expected_sha256": expected,
        "expected_source": "explicit" if args.expected else (args.reference or None),
        "onnx_weights_match_expected": (expected == onnx_weights_sha) if expected else None,
        "torch_available": False,
        "onnx_available": False,
        "tensor_compare": None,
    }

    if args.onnx is not None or args.pth is not None:
        try:
            onnx_keys = onnx_initializer_keys(args.onnx) if args.onnx is not None else []
            report["onnx_available"] = True
        except ImportError:
            report["onnx_available"] = False
            onnx_keys = []
        try:
            pth_keys = pth_tensor_keys(args.pth) if args.pth is not None else []
            report["torch_available"] = True
        except ImportError:
            report["torch_available"] = False
            pth_keys = []
        if report["onnx_available"] and report["torch_available"] and args.onnx and args.pth:
            report["pth_sha256"] = sha256_file(args.pth)
            report["tensor_compare"] = compare_multiset(onnx_keys, pth_keys)
        elif args.onnx is not None and args.pth is not None:
            report["tensor_compare"] = {"skipped": "torch and onnx are both required for a tensor compare"}
    return report


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--onnx-weights", type=Path, required=True,
                   help="Deployed ONNX external-data weights file (e.g. siglip_v2_v2.0_weights.bin). Hashed.")
    p.add_argument("--onnx", type=Path, help="model.onnx for initializer tensor extraction (tensor compare).")
    p.add_argument("--pth", type=Path, help="PyTorch checkpoint to tensor-compare against.")
    p.add_argument("--expected", help="Expected sha256 of --onnx-weights.")
    p.add_argument("--reference", choices=sorted(REFERENCES), help="Use a recorded reference hash.")
    p.add_argument("--report", type=Path, help="Write the JSON report here (default: stdout).")
    p.add_argument("--strict", action="store_true", help="Exit non-zero on mismatch.")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if not args.expected and not args.reference:
        parser().error("give --expected HASH or --reference {reference}")
    report = verify(args)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(text)
    else:
        sys.stdout.write(text)
    if args.strict:
        if report["onnx_weights_match_expected"] is False:
            return 1
        compare = report.get("tensor_compare")
        if isinstance(compare, dict) and not compare.get("weights_identical", True):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
