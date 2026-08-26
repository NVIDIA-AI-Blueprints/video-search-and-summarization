#!/usr/bin/env python3
"""
Convert a CLIP-ReID Market-1501 ViT-B-16 SIE+OLP checkpoint to ONNX.

Kept as close as possible to convert_to_onnx.py. Path overrides (--repo-dir,
--checkpoint, --output) exist only so download-embedding-models.sh can run this
inside the perception container with bind mounts.
"""

import argparse
import os
import sys


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo-dir", required=True, help="CLIP-ReID repository root")
    p.add_argument("--checkpoint", required=True, help="Path to the .pth checkpoint")
    p.add_argument("--output", required=True, help="Destination path for reid_model.onnx")
    return p.parse_args()


def convert_to_onnx(repo_dir, checkpoint, output_path):
    repo = os.path.abspath(repo_dir)
    sys.path.insert(0, repo)
    os.chdir(repo)

    import torch
    from yacs.config import CfgNode
    from model.make_model_clipreid import make_model
    from config import cfg

    # Torch ≥2.6 defaults weights_only=True; the CLIP-ReID checkpoint is a full state dict.
    _orig_load = torch.load

    def _load(*a, **k):
        k.setdefault("map_location", "cpu")
        k.setdefault("weights_only", False)
        return _orig_load(*a, **k)

    torch.load = _load

    # Load configuration. Upstream ships this file with every key under DATASETS
    # commented out, so it parses as None and merge_from_file rejects it against
    # the CfgNode default. The defaults (market1501) are what we want anyway.
    with open("configs/person/vit_clipreid.yml") as f:
        file_cfg = CfgNode.load_cfg(f)
    if file_cfg.get("DATASETS") is None:
        file_cfg.pop("DATASETS", None)
    cfg.merge_from_other_cfg(file_cfg)

    # Enable SIE and OLP (must match training settings)
    cfg.MODEL.SIE_CAMERA = True
    cfg.MODEL.SIE_COE = 1.0
    cfg.MODEL.STRIDE_SIZE = [12, 12]

    cfg.freeze()

    # Create model
    model = make_model(cfg, num_class=751, camera_num=6, view_num=1)
    model.eval()
    model.cuda()  # Move model to GPU

    # Load checkpoint
    model.load_param(checkpoint)

    # Create dummy input for tracing
    # The input shape should match your preprocessing (256, 128)
    dummy_input = torch.randn(1, 3, 256, 128).cuda()

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Export the model
    torch.onnx.export(
        model,                  # model being run
        dummy_input,           # model input
        output_path,           # where to save the model
        export_params=True,    # store the trained parameter weights inside the model file
        opset_version=17,      # the ONNX version to export the model to
        do_constant_folding=True,  # whether to execute constant folding for optimization
        input_names=['input'],     # the model's input names
        output_names=['output'],   # the model's output names
        dynamic_axes={
            'input': {0: 'batch_size'},    # variable length axes
            'output': {0: 'batch_size'}
        },
        # Torch ≥2.9 defaults to the torch.export exporter; stay on the
        # TorchScript one that produced the deployed reid_model.onnx.
        dynamo=False,
    )

    print(f"Model has been converted to ONNX and saved to {output_path}")


if __name__ == "__main__":
    args = parse_args()
    convert_to_onnx(args.repo_dir, args.checkpoint, args.output)
