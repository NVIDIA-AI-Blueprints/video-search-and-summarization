#!/usr/bin/env python3
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
######################################################################################################
"""Validate, execute, and report matched RT-CV/RT-VLM IPC A/B benchmarks."""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import statistics
import subprocess
import sys
import zipfile
from html import escape
from pathlib import Path
from typing import Any, Iterable, Optional


ARMS = {"direct", "ipc"}
DECODE_PATHS = {"direct": "direct_rtsp", "ipc": "rt_cv_nvunixfd"}
REQUIRED_COMMON = {
    "code_commit",
    "cv_image_digest",
    "vlm_image_digest",
    "model_revision",
    "hardware",
    "gpu_uuid",
    "media_sha256",
    "prompt_sha256",
    "cache_policy",
    "chunk_duration_seconds",
    "request_duration_seconds",
    "request_count",
    "runtime_policy",
}
SERIES_METRICS = {
    "throughput_chunks_per_second": "Throughput (chunks/s)",
    "chunk_latency_ms": "Chunk latency (ms)",
    "decode_latency_ms": "Decode latency (ms)",
    "vlm_latency_ms": "VLM latency (ms)",
    "gpu_util_pct": "GPU utilization (%)",
    "gpu_memory_mib": "GPU memory (MiB)",
    "decoder_util_pct": "Decoder utilization (%)",
    "cpu_pct": "Container CPU (%)",
    "container_memory_mib": "Container memory (MiB)",
    "power_w": "GPU power (W)",
}
SENSITIVE_NAME_PARTS = ("API_KEY", "CREDENTIAL", "PASSWORD", "SECRET", "TOKEN")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def _require(mapping: dict[str, Any], fields: Iterable[str], label: str) -> None:
    missing = sorted(field for field in fields if mapping.get(field) in (None, ""))
    if missing:
        raise ValueError(f"{label} missing required fields: {', '.join(missing)}")


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if not str(plan.get("benchmark_id", "")).strip():
        raise ValueError("benchmark_id is required")
    if plan.get("changed_variable") != "decode_path":
        raise ValueError("changed_variable must be decode_path")
    common = plan.get("common")
    execution = plan.get("execution")
    arms = plan.get("arms")
    if not isinstance(common, dict) or not isinstance(execution, dict) or not isinstance(arms, dict):
        raise ValueError("common, execution, and arms must be objects")
    _require(common, REQUIRED_COMMON, "common")
    for digest in ("cv_image_digest", "vlm_image_digest", "media_sha256", "prompt_sha256"):
        value = str(common[digest])
        if digest in ("cv_image_digest", "vlm_image_digest") and not value.startswith("sha256:"):
            raise ValueError(f"common.{digest} must be an immutable sha256 digest")
        hex_value = value.removeprefix("sha256:")
        if len(hex_value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in hex_value):
            raise ValueError(f"common.{digest} must contain a 64-character SHA256")
    code_commit = str(common["code_commit"])
    if not 7 <= len(code_commit) <= 64 or any(
        character not in "0123456789abcdefABCDEF" for character in code_commit
    ):
        raise ValueError("common.code_commit must be a 7-64 character git SHA")
    if common.get("runtime_policy") != "fresh_per_arm_repetition":
        raise ValueError("common.runtime_policy must be fresh_per_arm_repetition")
    if not isinstance(common["request_count"], int) or common["request_count"] < 1:
        raise ValueError("common.request_count must be a positive integer")
    if set(arms) != ARMS:
        raise ValueError("arms must contain exactly direct and ipc")
    output_root = execution.get("output_root")
    repetitions = execution.get("repetitions")
    arm_order = execution.get("arm_order")
    if not isinstance(output_root, str) or not output_root:
        raise ValueError("execution.output_root is required")
    if not isinstance(repetitions, int) or repetitions < 1:
        raise ValueError("execution.repetitions must be a positive integer")
    if arm_order not in (["direct", "ipc"], ["ipc", "direct"]):
        raise ValueError("execution.arm_order must be [direct, ipc] or [ipc, direct]")
    for name in sorted(ARMS):
        arm = arms[name]
        if not isinstance(arm, dict):
            raise ValueError(f"arms.{name} must be an object")
        if arm.get("decode_path") != DECODE_PATHS[name]:
            raise ValueError(f"arms.{name}.decode_path must be {DECODE_PATHS[name]}")
        command = arm.get("command")
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ValueError(f"arms.{name}.command must be a non-empty argv list")
        env = arm.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise ValueError(f"arms.{name}.env must be a string-to-string object")
        sensitive = sorted(
            key for key in env if any(part in key.upper() for part in SENSITIVE_NAME_PARTS)
        )
        if sensitive:
            raise ValueError(
                f"arms.{name}.env contains secret-like fields; inherit them at runtime instead: "
                + ", ".join(sensitive)
            )
        if name == "ipc":
            if arm.get("docker_ipc_mode") != "private":
                raise ValueError("arms.ipc.docker_ipc_mode must be private")
            if arm.get("socket_transport") != "bind_mount":
                raise ValueError("arms.ipc.socket_transport must be bind_mount")
    return plan


def _format_argv(argv: list[str], arm: str, repetition: int, output_dir: Path) -> list[str]:
    values = {"arm": arm, "repetition": repetition, "output_dir": str(output_dir)}
    try:
        return [part.format(**values) for part in argv]
    except KeyError as exc:
        raise ValueError(f"unsupported command placeholder: {exc.args[0]}") from exc


def render_runs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    validate_plan(plan)
    root = Path(plan["execution"]["output_root"])
    runs = []
    for repetition in range(1, plan["execution"]["repetitions"] + 1):
        order = list(plan["execution"]["arm_order"])
        if plan["execution"].get("alternate_order", True) and repetition % 2 == 0:
            order.reverse()
        for arm in order:
            output_dir = root / arm / f"rep-{repetition:02d}"
            runs.append(
                {
                    "arm": arm,
                    "repetition": repetition,
                    "output_dir": output_dir,
                    "argv": _format_argv(plan["arms"][arm]["command"], arm, repetition, output_dir),
                    "env": dict(plan["arms"][arm].get("env", {})),
                }
            )
    return runs


def _check_result(plan: dict[str, Any], result: dict[str, Any], arm: str) -> None:
    if result.get("schema_version") != 1 or result.get("arm") != arm:
        raise ValueError(f"{arm} result has wrong schema_version or arm")
    if result.get("runtime_fresh") is not True:
        raise ValueError(f"{arm} result does not prove a fresh runtime")
    identity = result.get("identity")
    if not isinstance(identity, dict):
        raise ValueError(f"{arm} result.identity must be an object")
    for key in REQUIRED_COMMON:
        if identity.get(key) != plan["common"].get(key):
            raise ValueError(f"{arm} identity mismatch for {key}")
    topology = result.get("topology")
    outcomes = result.get("outcomes")
    cleanup = result.get("cleanup")
    if not all(isinstance(value, dict) for value in (topology, outcomes, cleanup)):
        raise ValueError(f"{arm} topology, outcomes, and cleanup must be objects")
    if topology.get("decode_path") != DECODE_PATHS[arm]:
        raise ValueError(f"{arm} result decode_path mismatch")
    if arm == "ipc":
        if topology.get("ipc_socket_count") != 1:
            raise ValueError("ipc arm must prove exactly one IPC socket")
        if topology.get("vlm_rtsp_connections") != 0:
            raise ValueError("ipc arm must prove zero VLM RTSP connections")
        if topology.get("cv_rtsp_connections") != 1:
            raise ValueError("ipc arm must prove exactly one CV RTSP connection")
        if topology.get("ipc_cli_flags_present") is not True:
            raise ValueError("ipc arm must prove the IPC CLI flags were launched")
        if topology.get("ipc_source_log_marker_count", 0) < 1:
            raise ValueError("ipc arm must prove an IPC decoded-frame source log marker")
    else:
        if topology.get("vlm_rtsp_connections", 0) < 1:
            raise ValueError("direct arm must prove at least one VLM RTSP connection")
    if outcomes.get("offered_requests") != plan["common"]["request_count"]:
        raise ValueError(f"{arm} offered request count mismatch")
    if outcomes.get("failed_requests") != 0 or outcomes.get("empty_chunks") != 0:
        raise ValueError(f"{arm} result contains failed requests or empty chunks")
    if result.get("fatal_markers"):
        raise ValueError(f"{arm} result contains fatal markers")
    for key in ("containers_remaining", "sockets_remaining", "host_shm_objects_remaining"):
        if cleanup.get(key) != 0:
            raise ValueError(f"{arm} cleanup.{key} must be zero")


def execute(plan: dict[str, Any]) -> list[Path]:
    results = []
    for run in render_runs(plan):
        output_dir = run["output_dir"]
        if output_dir.exists():
            raise ValueError(f"refusing existing output directory: {output_dir}")
        output_dir.mkdir(parents=True)
        command_log = output_dir / "command.txt"
        command_log.write_text(shlex.join(run["argv"]) + "\n", encoding="utf-8")
        env = os.environ.copy()
        env.update(run["env"])
        env.update(
            {
                "RTVI_IPC_AB_ARM": run["arm"],
                "RTVI_IPC_AB_REPETITION": str(run["repetition"]),
                "RTVI_IPC_AB_OUTPUT_DIR": str(output_dir),
            }
        )
        with (output_dir / "terminal.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                run["argv"],
                cwd=plan["execution"].get("cwd") or None,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(
                f"{run['arm']} repetition {run['repetition']} failed with exit {completed.returncode}"
            )
        result_path = output_dir / "arm-result.json"
        if not result_path.is_file():
            raise ValueError(f"missing result contract: {result_path}")
        _check_result(plan, _load(result_path), run["arm"])
        results.append(result_path)
    return results


def _percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[rank]


def _series(result: dict[str, Any], metric: str) -> list[float]:
    if metric == "throughput_chunks_per_second":
        value = result.get("throughput_chunks_per_second")
        return [float(value)] if isinstance(value, (int, float)) else []
    source = result.get("latency_samples_ms", {}) if metric.endswith("latency_ms") else result.get("resource_samples", {})
    values = source.get(metric, []) if isinstance(source, dict) else []
    return [float(value) for value in values if isinstance(value, (int, float))]


def aggregate(plan: dict[str, Any], result_paths: list[Path]) -> dict[str, Any]:
    validate_plan(plan)
    grouped: dict[str, list[dict[str, Any]]] = {"direct": [], "ipc": []}
    for path in result_paths:
        result = _load(path)
        arm = result.get("arm")
        if arm not in ARMS:
            raise ValueError(f"{path}: unknown arm {arm}")
        _check_result(plan, result, arm)
        grouped[arm].append(result)
    expected = plan["execution"]["repetitions"]
    for arm in sorted(ARMS):
        if len(grouped[arm]) != expected:
            raise ValueError(f"expected {expected} {arm} results, found {len(grouped[arm])}")

    arm_summaries: dict[str, Any] = {}
    for arm, results in grouped.items():
        metrics = {}
        for metric, label in SERIES_METRICS.items():
            values = [value for result in results for value in _series(result, metric)]
            metrics[metric] = {
                "label": label,
                "count": len(values),
                "mean": statistics.mean(values) if values else None,
                "p50": _percentile(values, 0.50),
                "p95": _percentile(values, 0.95),
                "p99": _percentile(values, 0.99),
                "min": min(values) if values else None,
                "max": max(values) if values else None,
            }
        outcomes = {
            key: sum(int(result["outcomes"].get(key, 0)) for result in results)
            for key in ("offered_requests", "successful_requests", "failed_requests", "nonempty_chunks", "empty_chunks")
        }
        arm_summaries[arm] = {"repetitions": len(results), "outcomes": outcomes, "metrics": metrics}

    comparison = []
    for metric, label in SERIES_METRICS.items():
        direct = arm_summaries["direct"]["metrics"][metric]["mean"]
        ipc = arm_summaries["ipc"]["metrics"][metric]["mean"]
        delta = None if direct in (None, 0) or ipc is None else (ipc - direct) / direct * 100.0
        comparison.append({"metric": metric, "label": label, "direct": direct, "ipc": ipc, "ipc_vs_direct_pct": delta})
    return {
        "schema_version": 1,
        "benchmark_id": plan["benchmark_id"],
        "verdict": "valid comparison",
        "changed_variable": "decode_path",
        "common": plan["common"],
        "arms": arm_summaries,
        "comparison": comparison,
    }


def _number(value: Any) -> str:
    return "N/A" if value is None else f"{value:.2f}"


def _markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# RT-CV / RT-VLM IPC A/B: {summary['benchmark_id']}",
        "",
        f"Verdict: **{summary['verdict']}**",
        "",
        "| Metric | Direct | IPC | IPC vs direct |",
        "|---|---:|---:|---:|",
    ]
    for row in summary["comparison"]:
        delta = "N/A" if row["ipc_vs_direct_pct"] is None else f"{row['ipc_vs_direct_pct']:+.2f}%"
        lines.append(f"| {row['label']} | {_number(row['direct'])} | {_number(row['ipc'])} | {delta} |")
    lines.extend(["", "## Outcomes", "", "| Arm | Offered | Successful | Failed | Non-empty chunks | Empty chunks |", "|---|---:|---:|---:|---:|---:|"])
    for arm in ("direct", "ipc"):
        value = summary["arms"][arm]["outcomes"]
        lines.append(f"| {arm} | {value['offered_requests']} | {value['successful_requests']} | {value['failed_requests']} | {value['nonempty_chunks']} | {value['empty_chunks']} |")
    lines.append("")
    return "\n".join(lines)


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def _sheet_xml(rows: list[list[Any]]) -> str:
    body = []
    for rindex, row in enumerate(rows, start=1):
        cells = []
        for cindex, value in enumerate(row, start=1):
            ref = f"{_column_name(cindex)}{rindex}"
            if value is None:
                cells.append(f'<c r="{ref}"/>')
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        body.append(f'<row r="{rindex}">{"".join(cells)}</row>')
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(body) + "</sheetData></worksheet>"


def _write_xlsx(path: Path, summary: dict[str, Any]) -> None:
    comparison = [["Metric", "Direct", "IPC", "IPC vs direct (%)"]]
    for row in summary["comparison"]:
        comparison.append(
            [row["label"], row["direct"], row["ipc"], row["ipc_vs_direct_pct"]]
        )
    details = [["Arm", "Metric", "Count", "Mean", "p50", "p95", "p99", "Min", "Max"]]
    for arm in ("direct", "ipc"):
        for metric in SERIES_METRICS:
            value = summary["arms"][arm]["metrics"][metric]
            details.append(
                [
                    arm,
                    value["label"],
                    value["count"],
                    value["mean"],
                    value["p50"],
                    value["p95"],
                    value["p99"],
                    value["min"],
                    value["max"],
                ]
            )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        book.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Comparison" sheetId="1" r:id="rId1"/><sheet name="Arm details" sheetId="2" r:id="rId2"/></sheets></workbook>')
        book.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        book.writestr("xl/worksheets/sheet1.xml", _sheet_xml(comparison))
        book.writestr("xl/worksheets/sheet2.xml", _sheet_xml(details))


def write_report(summary: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ipc-ab-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output_dir / "ipc-ab-comparison.md").write_text(_markdown(summary), encoding="utf-8")
    with (output_dir / "ipc-ab-comparison.tsv").open("w", encoding="utf-8") as stream:
        stream.write("metric\tdirect\tipc\tipc_vs_direct_pct\n")
        for row in summary["comparison"]:
            stream.write(f"{row['metric']}\t{row['direct']}\t{row['ipc']}\t{row['ipc_vs_direct_pct']}\n")
    _write_xlsx(output_dir / "ipc-ab-comparison.xlsx", summary)


def _parse_result(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("result must be arm=/path/to/arm-result.json")
    arm, path = value.split("=", 1)
    if arm not in ARMS:
        raise argparse.ArgumentTypeError("result arm must be direct or ipc")
    return arm, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("plan", type=Path)
    render = sub.add_parser("render")
    render.add_argument("plan", type=Path)
    run = sub.add_parser("run")
    run.add_argument("plan", type=Path)
    run.add_argument("--execute", action="store_true")
    report = sub.add_parser("report")
    report.add_argument("plan", type=Path)
    report.add_argument("--result", action="append", required=True, type=_parse_result)
    report.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        plan = validate_plan(_load(args.plan))
        if args.command == "validate":
            print(json.dumps({"valid": True, "benchmark_id": plan["benchmark_id"]}, indent=2))
        elif args.command == "render" or (args.command == "run" and not args.execute):
            rendered = [{**run, "output_dir": str(run["output_dir"]), "command": shlex.join(run["argv"])} for run in render_runs(plan)]
            for run in rendered:
                run.pop("argv")
            print(json.dumps(rendered, indent=2))
        elif args.command == "run":
            paths = execute(plan)
            summary = aggregate(plan, paths)
            write_report(summary, Path(plan["execution"]["output_root"]) / "report")
            print(json.dumps({"completed": True, "results": [str(path) for path in paths]}, indent=2))
        else:
            result_paths = []
            for declared_arm, path in args.result:
                actual_arm = _load(path).get("arm")
                if actual_arm != declared_arm:
                    raise ValueError(
                        f"{path}: declared as {declared_arm}, but result contains arm={actual_arm}"
                    )
                result_paths.append(path)
            summary = aggregate(plan, result_paths)
            write_report(summary, args.output_dir)
            print(json.dumps({"reported": True, "output_dir": str(args.output_dir)}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
