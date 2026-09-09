#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Pick the Sparse4D model/anchor for a DATASET_TYPE (synthetic|real) from
Compose's blueprint_config.yml and write a values-override for helm
upgrade/install -f. Labels are handled separately by the chart itself
(labels-<datasetType>.txt), not by this script.

    compute_model_selection.py --dataset-type synthetic
    compute_model_selection.py --dataset-type real -o values-model.yaml

If your install already layers its own values file(s) that customize
rtvi.vss-rtvi-cv.ngcModelsToDownload, pass the same file(s) here with
-f/--values so the generated list is patched on top of your customizations
instead of just the chart defaults — otherwise this script's output, layered
last, silently discards them (Helm replaces lists wholesale, it doesn't merge
them entry-by-entry):

    compute_model_selection.py --dataset-type real -f my-values.yaml
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[5]
BLUEPRINT_CONFIG = (
    REPO_ROOT
    / "deploy/docker/industry-profiles/warehouse-operations/blueprint-configurator/blueprint_config.yml"
)
HELM_WAREHOUSE_DIR = REPO_ROOT / "deploy/helm/industry-profiles/warehouse-operations"

# Variables blueprint_config.yml resolves per DATASET_TYPE (commons.variables.3d)
# that this script needs. sparse4d_labels_file is skipped: the chart picks
# labels-<datasetType>.txt directly instead of templating a path.
TERNARY_VARS = [
    "sparse4d_onnx_file",
    "sparse4d_anchor_file",
    "sparse4d_model",
    "sparse4d_model_source",
    "sparse4d_model_destination",
    "sparse4d_anchor_source",
    "sparse4d_anchor_destination",
]

TERNARY_RE = re.compile(
    r'^"(?P<synthetic>[^"]*)"\s+if\s+"\$\{DATASET_TYPE\}"\s*==\s*"synthetic"\s+else\s+"(?P<real>[^"]*)"$'
)


def load_dataset_variables() -> dict:
    with open(BLUEPRINT_CONFIG) as f:
        config = yaml.safe_load(f)

    variables_3d = config["commons"]["variables"]["3d"]
    exprs = {}
    for entry in variables_3d:
        for name, expr in entry.items():
            exprs[name] = expr

    resolved = {"synthetic": {}, "real": {}}
    for name in TERNARY_VARS:
        expr = exprs.get(name)
        if expr is None:
            sys.exit(f"error: {name} not found in {BLUEPRINT_CONFIG.name} commons.variables.3d")
        match = TERNARY_RE.match(expr)
        if not match:
            sys.exit(f"error: {name} expression didn't match the expected synthetic/real ternary shape: {expr!r}")
        resolved["synthetic"][name] = match.group("synthetic")
        resolved["real"][name] = match.group("real")

    return resolved


def deep_merge(base: dict, overlay: dict) -> dict:
    """Merge overlay onto base the way Helm merges values files: dicts merge
    recursively, everything else (including lists) is replaced wholesale."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def build_models_list(v: dict) -> list:
    model = v["sparse4d_model"]
    org = model.split("/", 1)[0]
    return [
        {
            "model": model,
            "org": org,
            "artifact": "model",
            "sourcePath": v["sparse4d_model_source"],
            "destPath": v["sparse4d_model_destination"],
        },
        {
            "model": model,
            "org": org,
            "artifact": "anchor",
            "sourcePath": v["sparse4d_anchor_source"],
            "destPath": v["sparse4d_anchor_destination"],
        },
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-type", required=True, choices=["synthetic", "real"])
    parser.add_argument(
        "-f",
        "--values",
        action="append",
        default=[],
        help="Values file(s) your install already passes to helm -f that customize "
        "rtvi.vss-rtvi-cv.ngcModelsToDownload; merged in before patching so those "
        "customizations aren't dropped (repeatable, applied in order)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="values-model-selection.generated.yaml",
        help="Path to write the values-override file",
    )
    args = parser.parse_args()

    resolved = load_dataset_variables()
    v = resolved[args.dataset_type]

    chart_dir = HELM_WAREHOUSE_DIR / "warehouse-3d-app"
    chart_values_path = chart_dir / "values.yaml"
    if not chart_values_path.exists():
        sys.exit(f"error: chart values not found: {chart_values_path}")

    with open(chart_values_path) as f:
        chart_values = yaml.safe_load(f) or {}

    for values_file in args.values:
        path = Path(values_file)
        if not path.exists():
            sys.exit(f"error: values file not found: {path}")
        with open(path) as f:
            overlay = yaml.safe_load(f) or {}
        chart_values = deep_merge(chart_values, overlay)

    output = {
        "warehouse": {"datasetType": args.dataset_type},
        "rtvi": {
            "vss-rtvi-cv": {
                "datasetType": args.dataset_type,
                "sparse4d": {
                    "onnxFile": v["sparse4d_onnx_file"],
                    "anchorFile": v["sparse4d_anchor_file"],
                },
                "ngcModelsToDownload": build_models_list(v),
            }
        },
    }
    with open(args.output, "w") as f:
        yaml.safe_dump(output, f, sort_keys=False, default_flow_style=False)

    print(f"[compute_model_selection] {args.dataset_type}: {v['sparse4d_model']}", file=sys.stderr)
    print(f"[compute_model_selection] wrote {args.output}", file=sys.stderr)
    values_flags = "".join(f"-f {v} " for v in args.values)
    print(
        f"\nhelm upgrade --install <release> {chart_dir} -n <namespace> "
        f"{values_flags}-f {args.output} ...",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
