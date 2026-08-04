#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Correlate ephemeral agent trajectories with allowlisted hardware samples."""

from __future__ import annotations

import csv
import contextlib
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.:/ -]+")
SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]+")
METADATA_FIELDS = frozenset({
    "run_id", "skill", "spec_stem", "platform", "step", "chain", "agent",
    "model", "instance",
})
SAFE_EXECUTABLES = frozenset({
    "apt", "apt-get", "awk", "bash", "brev", "cargo", "cmake", "curl",
    "docker", "find", "git", "go", "grep", "helm", "jq", "kubectl", "make",
    "ninja", "node", "npm", "nvidia-smi", "pip", "pip3", "pytest", "python",
    "python3", "rg", "sed", "sh", "tar", "terraform", "uv", "uvx", "wget",
})
SHELL_WRAPPERS = frozenset({"command", "env", "nohup", "sudo", "time", "timeout"})
METRIC_UNITS = {
    "gpu.utilization": "%",
    "gpu.memory.utilization": "%",
    "gpu.memory.used": "MiB",
    "gpu.memory.total": "MiB",
    "gpu.power": "W",
    "cpu.user": "%",
    "cpu.system": "%",
    "cpu.iowait": "%",
    "cpu.idle": "%",
    "cpu.steal": "%",
    "system.load.1m": "1",
    "system.load.5m": "1",
    "system.load.15m": "1",
    "memory.used": "MiB",
    "memory.available": "MiB",
    "memory.total": "MiB",
    "swap.used": "MiB",
    "swap.total": "MiB",
}


def _safe_name(value: Any, default: str = "unknown", limit: int = 96) -> str:
    cleaned = SAFE_NAME_RE.sub("", str(value or "")).strip()
    return cleaned[:limit] or default


def _timestamp_us(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return None
        # Contemporary Unix values are approximately 1e18 ns, 1e15 us,
        # 1e12 ms, and 1e9 s. Keep the thresholds between those ranges:
        # treating 1.7e15 microseconds as nanoseconds shifts events to 1970.
        if number > 1e17:
            return int(number / 1_000)
        if number > 1e14:
            return int(number)
        if number > 1e11:
            return int(number * 1_000)
        return int(number * 1_000_000)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return int(parsed.timestamp() * 1_000_000)
    except ValueError:
        return None


def _command_summary(command: Any) -> str:
    """Return only allowlisted executable names; arguments never survive."""
    words = re.findall(r"[A-Za-z0-9_./+-]+", str(command or ""))
    found: list[str] = []
    for word in words:
        executable = word.rsplit("/", 1)[-1].lower()
        if executable in SHELL_WRAPPERS:
            continue
        if executable in SAFE_EXECUTABLES and executable not in found:
            found.append(executable)
    return " + ".join(found[:4]) if found else "shell"


def _load_trajectory(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows
    value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if isinstance(value, dict) and isinstance(value.get("steps"), list):
        return [row for row in value["steps"] if isinstance(row, dict)]
    return []


def _legacy_calls(step: dict[str, Any]) -> list[dict[str, Any]]:
    message = step.get("message")
    if not isinstance(message, str):
        return []
    try:
        decoded = json.loads(message)
    except json.JSONDecodeError:
        return []
    content = ((decoded.get("message") or {}).get("content")
               if isinstance(decoded, dict) else None)
    if not isinstance(content, list):
        return []
    calls = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            calls.append({
                "function_name": item.get("name"),
                "arguments": item.get("input") or {},
            })
    return calls


def _agent_events(
    path: Path, started_us: int, finished_us: int
) -> tuple[list[dict], list[dict]]:
    rows = _load_trajectory(path)
    parsed: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        timestamp = _timestamp_us(row.get("timestamp"))
        if timestamp is not None and started_us <= timestamp <= finished_us:
            parsed.append((timestamp, row))
    parsed.sort(key=lambda item: item[0])

    spans: list[dict] = []
    instants: list[dict] = []
    for index, (start_us, row) in enumerate(parsed):
        next_us = parsed[index + 1][0] if index + 1 < len(parsed) else finished_us
        end_us = max(start_us + 1, min(next_us, finished_us))
        source_value = str(row.get("source") or "").lower()
        source = source_value if source_value in {"agent", "user"} else "event"
        span_id = f"step-{index + 1}"
        spans.append({
            "id": span_id,
            "name": f"{source} step",
            "source": source,
            "start_us": start_us,
            "end_us": end_us,
        })
        calls = row.get("tool_calls")
        if not isinstance(calls, list):
            calls = _legacy_calls(row)
        for call_index, call in enumerate(calls):
            if not isinstance(call, dict):
                continue
            tool = _safe_name(call.get("function_name"), "tool", 48)
            arguments = call.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            label = tool
            if tool.lower() in {"bash", "shell"}:
                label = f"{tool}: {_command_summary(arguments.get('command'))}"
            instants.append({
                "id": f"{span_id}-tool-{call_index + 1}",
                "name": label,
                "tool": tool,
                "timestamp_us": start_us + call_index,
                "step_id": span_id,
            })
    return spans, instants


def _sidecar(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _gpu_metrics(csv_path: Path, sidecar: dict[str, Any]) -> list[dict]:
    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return []
    raw_times = []
    for row in rows:
        try:
            stamp = dt.datetime.strptime(
                row["timestamp"].strip(), "%Y/%m/%d %H:%M:%S.%f"
            ).replace(tzinfo=dt.timezone.utc)
            raw_times.append(int(stamp.timestamp() * 1_000_000))
        except (KeyError, ValueError):
            raw_times.append(None)
    first = next((value for value in raw_times if value is not None), None)
    started = _timestamp_us(sidecar.get("started_at"))
    offset = (started - first) if first is not None and started is not None else 0
    mapping = {
        "util_gpu_pct": "gpu.utilization",
        "util_mem_pct": "gpu.memory.utilization",
        "mem_used_mib": "gpu.memory.used",
        "mem_total_mib": "gpu.memory.total",
        "power_w": "gpu.power",
    }
    metrics = []
    for row, timestamp in zip(rows, raw_times):
        if timestamp is None:
            continue
        try:
            gpu_index = int(float(row["gpu_index"]))
        except (KeyError, ValueError):
            continue
        for column, name in mapping.items():
            try:
                value = float(row[column])
            except (KeyError, ValueError):
                continue
            if math.isfinite(value):
                metrics.append({
                    "timestamp_us": timestamp + offset,
                    "name": name,
                    "value": value,
                    "unit": METRIC_UNITS[name],
                    "attributes": {"gpu_index": gpu_index},
                })
    return metrics


def _system_metrics(csv_path: Path, sidecar: dict[str, Any]) -> list[dict]:
    mapping = {
        "cpu_user_pct": "cpu.user",
        "cpu_system_pct": "cpu.system",
        "cpu_iowait_pct": "cpu.iowait",
        "cpu_idle_pct": "cpu.idle",
        "cpu_steal_pct": "cpu.steal",
        "load_1m": "system.load.1m",
        "load_5m": "system.load.5m",
        "load_15m": "system.load.15m",
        "mem_used_mib": "memory.used",
        "mem_available_mib": "memory.available",
        "mem_total_mib": "memory.total",
        "swap_used_mib": "swap.used",
        "swap_total_mib": "swap.total",
    }
    rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    raw_times: list[int | None] = []
    for row in rows:
        try:
            raw_times.append(int(row["timestamp_ns"]) // 1_000)
        except (KeyError, ValueError):
            raw_times.append(None)
    first = next((value for value in raw_times if value is not None), None)
    started = _timestamp_us(sidecar.get("started_at"))
    offset = (started - first) if first is not None and started is not None else 0

    metrics = []
    for row, timestamp_us in zip(rows, raw_times):
        if timestamp_us is None:
            continue
        # The sampler's first row primes /proc/stat and therefore carries
        # five CPU percentages set to zero. Treating that as a measurement
        # says the host is simultaneously 0% busy and 0% idle, and skews every
        # short trace. Keep that row's instantaneous load/memory values, but
        # omit CPU percentages until a real counter delta exists.
        try:
            cpu_total = sum(float(row[column]) for column in (
                "cpu_user_pct", "cpu_system_pct", "cpu_iowait_pct",
                "cpu_idle_pct", "cpu_steal_pct",
            ))
        except (KeyError, ValueError):
            cpu_total = None
        for column, name in mapping.items():
            if (
                name.startswith("cpu.")
                and cpu_total is not None
                and cpu_total < 50.0
            ):
                continue
            try:
                value = float(row[column])
            except (KeyError, ValueError):
                continue
            if math.isfinite(value):
                metrics.append({
                    "timestamp_us": timestamp_us + offset,
                    "name": name,
                    "value": value,
                    "unit": METRIC_UNITS[name],
                    "attributes": {},
                })
    return metrics


def _active_step(spans: list[dict], timestamp_us: int) -> str:
    for span in spans:
        if span["start_us"] <= timestamp_us < span["end_us"]:
            return span["id"]
    return ""


def _summary(metrics: list[dict]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for metric in metrics:
        grouped.setdefault(metric["name"], []).append(metric["value"])
    aggregates = {
        name: {
            "samples": len(values),
            "min": round(min(values), 3),
            "mean": round(sum(values) / len(values), 3),
            "max": round(max(values), 3),
            "unit": METRIC_UNITS.get(name, "1"),
        }
        for name, values in sorted(grouped.items())
    }
    gpu_samples: dict[tuple[int, int], dict[str, float]] = {}
    for metric in metrics:
        if metric["name"] not in {"gpu.utilization", "gpu.memory.used"}:
            continue
        key = (
            metric["timestamp_us"],
            int(metric.get("attributes", {}).get("gpu_index", 0)),
        )
        gpu_samples.setdefault(key, {})[metric["name"]] = metric["value"]
    idle_resident = [
        row for row in gpu_samples.values()
        if row.get("gpu.utilization", 100) < 5 and row.get("gpu.memory.used", 0) > 1024
    ]
    signals = {
        "gpu_idle_with_resident_memory_samples": len(idle_resident),
        "gpu_idle_with_resident_memory_pct": round(
            100 * len(idle_resident) / len(gpu_samples), 2
        ) if gpu_samples else 0.0,
    }
    return {"metrics": aggregates, "waste_signals": signals}


def _perfetto(model: dict[str, Any]) -> dict[str, Any]:
    origin = model["bounds"]["start_us"]
    events: list[dict[str, Any]] = [
        {"name": "process_name", "ph": "M", "pid": 1, "tid": 0,
         "args": {"name": "Agent activity"}},
        {"name": "process_name", "ph": "M", "pid": 2, "tid": 0,
         "args": {"name": "Hardware metrics"}},
        {"name": "trace_origin_unix_us", "ph": "i", "s": "g", "pid": 1,
         "tid": 0, "ts": 0, "args": {"unix_us": str(origin)}},
    ]
    for span in model["spans"]:
        events.append({
            "name": span["name"], "cat": "agent", "ph": "X", "pid": 1,
            "tid": 1, "ts": span["start_us"] - origin,
            "dur": max(1, span["end_us"] - span["start_us"]),
            "args": {"step_id": span["id"], "source": span["source"]},
        })
    for event in model["instants"]:
        events.append({
            "name": event["name"], "cat": "tool", "ph": "i", "s": "t",
            "pid": 1, "tid": 2, "ts": event["timestamp_us"] - origin,
            "args": {"step_id": event["step_id"], "tool": event["tool"]},
        })
    for metric in model["metrics"]:
        track = metric["name"]
        gpu = metric.get("attributes", {}).get("gpu_index")
        if gpu is not None:
            track = f"gpu.{gpu}.{track.removeprefix('gpu.')}"
        events.append({
            "name": track, "cat": "metric", "ph": "C", "pid": 2, "tid": 1,
            "ts": metric["timestamp_us"] - origin,
            "args": {"value": metric["value"], "unit": metric["unit"],
                     "active_step": metric.get("active_step", "")},
        })
    return {"traceEvents": events, "displayTimeUnit": "ms"}


def _otlp_lines(model: dict[str, Any]) -> list[str]:
    seed = json.dumps(model["metadata"], sort_keys=True).encode()
    trace_id = hashlib.sha256(seed).hexdigest()[:32]
    resource = {
        "attributes": [
            {"key": key, "value": {"stringValue": str(value)}}
            for key, value in sorted(model["metadata"].items())
            if value not in (None, "")
        ]
    }
    spans = []
    for index, span in enumerate(model["spans"] + [
        {
            "id": event["id"], "name": event["name"],
            "start_us": event["timestamp_us"], "end_us": event["timestamp_us"] + 1,
        } for event in model["instants"]
    ]):
        span_id = hashlib.sha256(f"{trace_id}:{index}".encode()).hexdigest()[:16]
        spans.append({
            "traceId": trace_id,
            "spanId": span_id,
            "name": span["name"],
            "kind": 1,
            "startTimeUnixNano": str(span["start_us"] * 1_000),
            "endTimeUnixNano": str(span["end_us"] * 1_000),
            "attributes": [{
                "key": "skill_eval.event_id",
                "value": {"stringValue": span["id"]},
            }],
        })
    trace_request = {
        "resourceSpans": [{
            "resource": resource,
            "scopeSpans": [{"scope": {"name": "vss.skill_eval.timeline"}, "spans": spans}],
        }]
    }

    grouped: dict[str, list[dict]] = {}
    for metric in model["metrics"]:
        attributes = [
            {"key": key, "value": {"intValue": str(value)}}
            for key, value in sorted(metric.get("attributes", {}).items())
        ]
        if metric.get("active_step"):
            attributes.append({
                "key": "skill_eval.active_step",
                "value": {"stringValue": metric["active_step"]},
            })
        grouped.setdefault(metric["name"], []).append({
            "timeUnixNano": str(metric["timestamp_us"] * 1_000),
            "asDouble": metric["value"],
            "attributes": attributes,
        })
    metric_request = {
        "resourceMetrics": [{
            "resource": resource,
            "scopeMetrics": [{
                "scope": {"name": "vss.skill_eval.timeline"},
                "metrics": [{
                    "name": name,
                    "unit": METRIC_UNITS.get(name, "1"),
                    "gauge": {"dataPoints": points},
                } for name, points in sorted(grouped.items())],
            }],
        }]
    }
    return [
        json.dumps(trace_request, separators=(",", ":")),
        json.dumps(metric_request, separators=(",", ":")),
    ]


def _html(model: dict[str, Any]) -> str:
    start = model["bounds"]["start_us"]
    end = max(start + 1, model["bounds"]["end_us"])
    width, height = 1400, 390
    left, right = 170, 20

    def x(timestamp: int) -> float:
        return left + (timestamp - start) * (width - left - right) / (end - start)

    colors = {"agent": "#76b900", "user": "#5b8def", "event": "#888"}
    svg = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Agent and hardware timeline">',
        '<rect width="100%" height="100%" fill="#11151a"/>',
    ]
    tracks = [("Agent steps", 45), ("Tool calls", 105), ("GPU utilization", 180),
              ("CPU utilization", 255), ("Memory used", 330)]
    for label, y in tracks:
        svg.append(f'<text x="8" y="{y}" fill="#d5dbe3">{html.escape(label)}</text>')
        svg.append(f'<line x1="{left}" x2="{width-right}" y1="{y}" y2="{y}" '
                   'stroke="#35404c"/>')
    for span in model["spans"]:
        sx, ex = x(span["start_us"]), x(span["end_us"])
        color = colors.get(span["source"], colors["event"])
        svg.append(
            f'<rect x="{sx:.2f}" y="25" width="{max(1, ex-sx):.2f}" height="28" '
            f'fill="{color}" opacity=".75"><title>{html.escape(span["id"])}: '
            f'{html.escape(span["name"])}</title></rect>'
        )
    for event in model["instants"]:
        ex = x(event["timestamp_us"])
        svg.append(
            f'<circle cx="{ex:.2f}" cy="105" r="5" fill="#f5a623"><title>'
            f'{html.escape(event["name"])} ({html.escape(event["step_id"])})'
            '</title></circle>'
        )

    def polyline(name: str, y: int, maximum: float, color: str) -> None:
        points = [
            (x(metric["timestamp_us"]),
             y - min(max(metric["value"], 0), maximum) * 55 / maximum)
            for metric in model["metrics"] if metric["name"] == name
        ]
        if points:
            joined = " ".join(f"{px:.2f},{py:.2f}" for px, py in points)
            svg.append(f'<polyline points="{joined}" fill="none" stroke="{color}" '
                       'stroke-width="2"/>')

    polyline("gpu.utilization", 225, 100, "#76b900")
    polyline("cpu.user", 300, 100, "#5b8def")
    memory = [m["value"] for m in model["metrics"] if m["name"] == "memory.used"]
    polyline("memory.used", 375, max(memory) if memory else 1, "#d66efd")
    svg.append("</svg>")

    rows = []
    for name, values in model["summary"]["metrics"].items():
        rows.append(
            "<tr>" + "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in (
                    name, values["samples"], values["min"], values["mean"],
                    values["max"], values["unit"],
                )
            ) + "</tr>"
        )
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skill-eval agent hardware timeline</title>
<style>
body{font:14px system-ui,sans-serif;background:#0b0e11;color:#e8edf2;margin:24px}
h1{font-size:22px} .meta{color:#9eabb8} svg{width:100%;border:1px solid #35404c}
table{border-collapse:collapse;width:100%;margin-top:18px}th,td{padding:6px 10px;
border-bottom:1px solid #303943;text-align:right}th:first-child,td:first-child{text-align:left}
code{color:#9dccff}
</style></head><body>
<h1>Skill-eval agent hardware timeline</h1>
<p class="meta">Hover timeline marks for sanitized labels. Raw commands, arguments,
messages, observations, environments, and file contents are intentionally absent.</p>
""" + "".join(svg) + """
<h2>Metric summary</h2>
<table><thead><tr><th>Metric</th><th>Samples</th><th>Min</th><th>Mean</th>
<th>Max</th><th>Unit</th></tr></thead><tbody>""" + "".join(rows) + """
</tbody></table>
<h2>Waste signal</h2><p><code>GPU idle &lt;5% with &gt;1 GiB resident:</code> """ + \
        html.escape(str(model["summary"]["waste_signals"][
            "gpu_idle_with_resident_memory_pct"])) + "% of paired samples</p></body></html>\n"


def generate(
    results_root: Path,
    *,
    include_task_name: str,
    trace_token: str,
    started_at: float,
    finished_at: float,
    metadata: dict[str, Any] | None = None,
) -> Path | None:
    """Generate all comparison formats from one sanitized canonical model."""
    candidates = [
        path for path in results_root.glob(
            f"*/{include_task_name}__*/agent/trajectory.json*"
        ) if path.stat().st_mtime >= started_at - 2
    ]
    trajectory = max(candidates, key=lambda path: path.stat().st_mtime) \
        if candidates else None
    started_us = int(started_at * 1_000_000)
    finished_us = int(finished_at * 1_000_000)
    try:
        spans, instants = _agent_events(
            trajectory, started_us, finished_us
        ) if trajectory else ([], [])

        metrics: list[dict] = []
        gpu_csv = results_root / "gputrace" / f"{trace_token}.csv"
        if gpu_csv.exists():
            metrics.extend(_gpu_metrics(
                gpu_csv, _sidecar(gpu_csv.with_suffix(".json"))
            ))
        system_csv = results_root / "systemtrace" / f"{trace_token}.csv"
        if system_csv.exists():
            metrics.extend(_system_metrics(
                system_csv, _sidecar(system_csv.with_suffix(".json"))
            ))
        metrics.sort(key=lambda item: (item["timestamp_us"], item["name"]))
        for metric in metrics:
            metric["active_step"] = _active_step(spans, metric["timestamp_us"])

        all_times = (
            [span["start_us"] for span in spans] +
            [span["end_us"] for span in spans] +
            [metric["timestamp_us"] for metric in metrics]
        )
        start_us = min(all_times) if all_times else started_us
        end_us = max(all_times) if all_times else finished_us
        supplied_metadata = metadata or {}
        safe_metadata = {
            key: _safe_name(supplied_metadata[key], "", 128)
            for key in sorted(METADATA_FIELDS)
            if key in supplied_metadata and supplied_metadata[key] not in (None, "")
        }
        model = {
            "schema": 1,
            "metadata": safe_metadata,
            "bounds": {"start_us": start_us, "end_us": max(start_us + 1, end_us)},
            "spans": spans,
            "instants": instants,
            "metrics": metrics,
            "summary": _summary(metrics),
            "privacy": {
                "source_trajectory": "ephemeral",
                "published_fields": "allowlist",
                "raw_commands_included": False,
            },
        }
        outputs = {
            "timeline.json": json.dumps(model, indent=2) + "\n",
            "timeline.perfetto.json": (
                json.dumps(_perfetto(model), separators=(",", ":")) + "\n"
            ),
            "timeline.html": _html(model),
            "timeline.otlp.jsonl": "\n".join(_otlp_lines(model)) + "\n",
        }
        timeline_root = results_root / "timeline"
        timeline_root.mkdir(parents=True, exist_ok=True)
        token = SAFE_TOKEN_RE.sub("-", str(trace_token))[:160].strip(".-") or "trace"
        dest = timeline_root / token
        # Stage outside results_root so even an abrupt process death cannot
        # leave a partial timeline.* set for the artifact collector to archive.
        # The parent is the same filesystem, preserving atomic directory rename.
        temporary = Path(tempfile.mkdtemp(
            prefix=f".timeline-{token}.", dir=results_root.parent
        ))
        try:
            for filename, content in outputs.items():
                (temporary / filename).write_text(content, encoding="utf-8")
            if dest.exists():
                shutil.rmtree(dest)
            os.replace(temporary, dest)
        finally:
            with contextlib.suppress(OSError):
                shutil.rmtree(temporary)
        return dest
    finally:
        # Deletion is independent of rendering success. A malformed trajectory
        # or unwritable output must not turn raw messages and tool results into
        # a persistent viewer retention path.
        if trajectory is not None:
            with contextlib.suppress(OSError):
                shutil.rmtree(trajectory.parent)
