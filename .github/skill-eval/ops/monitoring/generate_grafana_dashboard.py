#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate the Grafana dashboard JSON for distributed CPU coordinators."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


DATASOURCE = {"type": "influxdb", "uid": "${DS_INFLUXDB}"}
HOST_FILTER = 'r.coordinator_id =~ /^${coordinator:regex}$/'


def flux(measurement: str, field: str, extra: str = "", transform: str = "") -> str:
    clauses = [
        'from(bucket: v.defaultBucket)',
        '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)',
        '  |> filter(fn: (r) => r.fleet == "vss-skill-eval-distributed")',
        f"  |> filter(fn: (r) => {HOST_FILTER})",
        f'  |> filter(fn: (r) => r._measurement == "{measurement}")',
        f'  |> filter(fn: (r) => r._field == "{field}")',
    ]
    if extra:
        clauses.append(f"  |> filter(fn: (r) => {extra})")
    clauses.extend(
        [
            "  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)",
            '  |> group(columns: ["coordinator_id"])',
        ]
    )
    if transform:
        clauses.append(transform)
    return "\n".join(clauses)


def target(query: str, ref_id: str = "A") -> dict:
    return {
        "datasource": DATASOURCE,
        "query": query,
        "refId": ref_id,
    }


def thresholds(warn: float, critical: float) -> dict:
    return {
        "mode": "absolute",
        "steps": [
            {"color": "green", "value": None},
            {"color": "orange", "value": warn},
            {"color": "red", "value": critical},
        ],
    }


def neutral_thresholds() -> dict:
    return {"mode": "absolute", "steps": [{"color": "green", "value": None}]}


def panel(
    panel_id: int,
    title: str,
    panel_type: str,
    x: int,
    y: int,
    w: int,
    h: int,
    targets: list[dict],
    unit: str,
    *,
    minimum: float | None = 0,
    maximum: float | None = None,
    panel_thresholds: dict | None = None,
    description: str = "",
) -> dict:
    defaults = {
        "color": {"mode": "palette-classic"},
        "custom": {
            "axisCenteredZero": False,
            "axisColorMode": "text",
            "axisLabel": "",
            "axisPlacement": "auto",
            "drawStyle": "line",
            "fillOpacity": 12,
            "gradientMode": "none",
            "hideFrom": {"legend": False, "tooltip": False, "viz": False},
            "lineInterpolation": "smooth",
            "lineWidth": 2,
            "pointSize": 4,
            "scaleDistribution": {"type": "linear"},
            "showPoints": "never",
            "spanNulls": 30,
            "stacking": {"group": "A", "mode": "none"},
        },
        "mappings": [],
        "min": minimum,
        "thresholds": panel_thresholds or thresholds(80, 90),
        "unit": unit,
    }
    if maximum is not None:
        defaults["max"] = maximum
    return {
        "id": panel_id,
        "title": title,
        "description": description,
        "type": panel_type,
        "datasource": DATASOURCE,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {
                "calcs": ["lastNotNull", "max"],
                "displayMode": "table",
                "placement": "bottom",
                "showLegend": True,
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
            "reduceOptions": {
                "calcs": ["lastNotNull"],
                "fields": "",
                "values": False,
            },
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
        },
        "targets": targets,
    }


def dashboard() -> dict:
    host_count = """
from(bucket: v.defaultBucket)
  |> range(start: -2m)
  |> filter(fn: (r) => r.fleet == "vss-skill-eval-distributed")
  |> filter(fn: (r) => r._measurement == "system" and r._field == "uptime")
  |> last()
  |> keep(columns: ["coordinator_id"])
  |> group()
  |> distinct(column: "coordinator_id")
  |> count(column: "coordinator_id")
""".strip()
    cpu = flux("cpu", "usage_active", 'r.cpu == "cpu-total"')
    memory = flux("mem", "used_percent")
    disk_used = flux("disk", "used_percent", 'r.path == "/"')
    disk_free = flux("disk", "free", 'r.path == "/"')
    load = flux("system", "load1")
    swap = flux("swap", "used_percent")
    disk_read = flux(
        "diskio",
        "read_bytes",
        transform="  |> derivative(unit: 1s, nonNegative: true)",
    )
    disk_write = flux(
        "diskio",
        "write_bytes",
        transform="  |> derivative(unit: 1s, nonNegative: true)",
    )

    panels = [
        panel(
            1, "Hosts reporting (last 2m)", "stat", 0, 0, 6, 4,
            [target(host_count)], "short", minimum=0, maximum=8,
            panel_thresholds={
                "mode": "absolute",
                "steps": [
                    {"color": "red", "value": None},
                    {"color": "orange", "value": 7},
                    {"color": "green", "value": 8},
                ],
            },
            description="Must remain at 8. A lower value means at least one Telegraf agent stopped reporting.",
        ),
        panel(
            2, "CPU active", "stat", 6, 0, 6, 4,
            [target(cpu)], "percent", maximum=100,
            description="Latest active CPU percentage by coordinator.",
        ),
        panel(
            3, "RAM used", "stat", 12, 0, 6, 4,
            [target(memory)], "percent", maximum=100,
            description="Latest host memory usage by coordinator.",
        ),
        panel(
            4, "Root disk free", "stat", 18, 0, 6, 4,
            [target(disk_free)], "bytes", minimum=0,
            panel_thresholds=neutral_thresholds(),
            description="Free bytes on /. Use the disk-used panel for warning thresholds.",
        ),
        panel(
            5, "CPU active by coordinator", "timeseries", 0, 4, 12, 8,
            [target(cpu)], "percent", maximum=100,
            description="Warn above 80%; investigate sustained usage above 90%.",
        ),
        panel(
            6, "RAM used by coordinator", "timeseries", 12, 4, 12, 8,
            [target(memory)], "percent", maximum=100,
            description="Warn above 80%; critical above 90%.",
        ),
        panel(
            7, "Root disk used", "timeseries", 0, 12, 12, 8,
            [target(disk_used)], "percent", maximum=100,
            description="Warn above 80%; critical above 90%.",
        ),
        panel(
            8, "Disk throughput", "timeseries", 12, 12, 12, 8,
            [target(disk_read, "A"), target(disk_write, "B")], "Bps",
            panel_thresholds=neutral_thresholds(),
            description="Per-second read and write throughput. Series are grouped by coordinator.",
        ),
        panel(
            9, "1-minute load average", "timeseries", 0, 20, 12, 8,
            [target(load)], "short",
            panel_thresholds=neutral_thresholds(),
            description="Compare load with each machine's vCPU count.",
        ),
        panel(
            10, "Swap used", "timeseries", 12, 20, 12, 8,
            [target(swap)], "percent", maximum=100,
            description="Sustained swap growth is an early memory-pressure signal.",
        ),
    ]
    return {
        "__inputs": [
            {
                "name": "DS_INFLUXDB",
                "label": "InfluxDB",
                "description": "InfluxDB 2.x datasource using Flux",
                "type": "datasource",
                "pluginId": "influxdb",
                "pluginName": "InfluxDB",
            }
        ],
        "annotations": {"list": []},
        "description": "CPU, RAM, root disk, disk I/O, load, and availability for eight VSS skill-eval CPU coordinators.",
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "id": None,
        "links": [],
        "liveNow": False,
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 41,
        "tags": ["vss", "skill-eval", "coordinator", "infrastructure"],
        "templating": {
            "list": [
                {
                    "name": "coordinator",
                    "label": "Coordinator",
                    "type": "query",
                    "datasource": DATASOURCE,
                    "query": (
                        'import "influxdata/influxdb/schema"\n'
                        "schema.tagValues(\n"
                        "  bucket: v.defaultBucket,\n"
                        '  tag: "coordinator_id",\n'
                        '  predicate: (r) => r.fleet == "vss-skill-eval-distributed",\n'
                        "  start: -30d,\n"
                        ")"
                    ),
                    "definition": "",
                    "includeAll": True,
                    "allValue": ".*",
                    "multi": True,
                    "refresh": 2,
                    "sort": 1,
                    "current": {"selected": True, "text": "All", "value": "$__all"},
                    "options": [],
                }
            ]
        },
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": "VSS Skill Eval · Distributed Coordinators",
        "uid": "vss-skill-eval-coordinators",
        "version": 1,
        "weekStart": "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("vss-skill-eval-coordinators.json"),
    )
    args = parser.parse_args()
    args.output.write_text(json.dumps(dashboard(), indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
