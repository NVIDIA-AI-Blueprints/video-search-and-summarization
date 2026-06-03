# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Convert KPI YAML (e.g. vss_perf_lvs_kpis.yaml) to dashboard-consumable JSON.

The dashboard expects a flattened JSON where platform_configs and per-KPI baselines
are merged into each KPI's ``configurations`` list. Metric paths are shortened
(``test_cases[*].metrics.`` prefix stripped).

Usage:
    python vss_perf_kpi_export.py vss_perf_lvs_kpis.yaml           # writes <service>_kpis.json
    python vss_perf_kpi_export.py vss_perf_lvs_kpis.yaml -o out.json
    python vss_perf_kpi_export.py vss_perf_lvs_kpis.yaml --upload   # also upload to MinIO
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

PATH_PREFIX = "test_cases[*].metrics."


def _build_config_lookup(
    platform_configs: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Build {config_id: {gpu_model, gpu_count, ...config fields}} from platform_configs."""
    lookup: Dict[str, Dict[str, Any]] = {}
    for entry in platform_configs:
        gpu = entry.get("platform", {}).get("gpu", {})
        gpu_model = gpu.get("model", "")
        gpu_count = gpu.get("count", 0)
        for cfg in entry.get("config", []):
            cfg_id = cfg["id"]
            merged = {"gpu_model": gpu_model, "gpu_count": gpu_count}
            for k, v in cfg.items():
                if k != "id":
                    merged[k] = v
            lookup[cfg_id] = merged
    return lookup


def _strip_path(path: str) -> str:
    """Strip the test_cases[*].metrics. prefix for dashboard paths."""
    if path.startswith(PATH_PREFIX):
        return path[len(PATH_PREFIX):]
    return path


def _build_kpi_configurations(
    kpi: Dict[str, Any],
    config_lookup: Dict[str, Dict[str, Any]],
) -> Any:
    """Build the ``configurations`` field for a single KPI entry."""
    baselines = kpi.get("baselines")

    if baselines is None:
        return {"default": {"baseline": None, "sol": None}}

    if isinstance(baselines, dict):
        if baselines.get("default") is None and len(baselines) == 1:
            return []

        configs: List[Dict[str, Any]] = []
        for cfg_id, baseline_val in baselines.items():
            if cfg_id == "default":
                continue
            entry: Dict[str, Any] = {"id": cfg_id}
            platform = config_lookup.get(cfg_id, {})
            if platform:
                entry["gpu_model"] = platform.get("gpu_model", "")
                entry["gpu_count"] = platform.get("gpu_count", 0)
                for field in ("gpu_topology", "vlm", "llm", "max_tokens"):
                    if field in platform:
                        entry[field] = platform[field]
            entry["baseline"] = baseline_val
            configs.append(entry)
        return configs

    return {"default": {"baseline": None, "sol": None}}


def convert_yaml_to_json(yaml_data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert KPI YAML structure to dashboard JSON."""
    service = yaml_data.get("service", "")
    schema_version = yaml_data.get("schema_version", "0.0.1")
    platform_configs = yaml_data.get("platform_configs", [])
    kpis_yaml = yaml_data.get("kpis", [])

    config_lookup = _build_config_lookup(platform_configs)

    kpis_json: List[Dict[str, Any]] = []
    for kpi in kpis_yaml:
        thresholds = kpi.get("thresholds", {})

        entry: Dict[str, Any] = {
            "name": kpi["name"],
            "description": kpi.get("description", ""),
            "path": _strip_path(kpi.get("path", "")),
            "unit": kpi.get("unit", ""),
            "direction": kpi.get("direction", None),
            "warning_pct": thresholds.get("warning_percent", 10),
            "critical_pct": thresholds.get("critical_percent", 20),
            "dashboard": kpi.get("dashboard", {}),
        }

        if kpi.get("optional"):
            entry["optional"] = True

        entry["configurations"] = _build_kpi_configurations(kpi, config_lookup)

        kpis_json.append(entry)

    return {
        "service": service,
        "schema_version": schema_version,
        "topology_matching": None,
        "kpis": kpis_json,
    }


def export_kpi_json(
    yaml_path: str,
    output_path: Optional[str] = None,
) -> Path:
    """Read YAML, convert, and write JSON. Returns output path."""
    yaml_file = Path(yaml_path)
    with open(yaml_file, "r") as f:
        yaml_data = yaml.safe_load(f)

    result = convert_yaml_to_json(yaml_data)

    if output_path:
        out = Path(output_path)
    else:
        service = yaml_data.get("service", "unknown").lower()
        out = yaml_file.parent / f"{service}_kpis.json"

    with open(out, "w") as f:
        json.dump(result, f, indent=2)
    logger.info("Wrote KPI JSON: %s", out)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Convert KPI YAML to dashboard JSON and optionally upload to MinIO",
    )
    parser.add_argument("yaml_file", help="Path to KPI YAML (e.g. vss_perf_lvs_kpis.yaml)")
    parser.add_argument("-o", "--output", default=None, help="Output JSON path (default: <service>_kpis.json)")
    parser.add_argument("--upload", action="store_true", help="Upload the JSON to MinIO after generation")
    parser.add_argument("--bucket", default=None, help="MinIO bucket (default: MINIO_BUCKET env or perf-results)")
    args = parser.parse_args()

    out = export_kpi_json(args.yaml_file, args.output)
    print(f"Generated: {out}")

    if args.upload:
        try:
            from .vss_perf_common import upload_result_file
        except ImportError:
            from vss_perf_common import upload_result_file

        yaml_file = Path(args.yaml_file)
        with open(yaml_file, "r") as f:
            yaml_data = yaml.safe_load(f)
        service = yaml_data.get("service", "unknown")
        if upload_result_file(str(out), service, bucket=args.bucket):
            print(f"Uploaded to MinIO: {service}/{out.name}")
        else:
            print("Upload failed. Check MINIO_ENDPOINT and credentials.", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
