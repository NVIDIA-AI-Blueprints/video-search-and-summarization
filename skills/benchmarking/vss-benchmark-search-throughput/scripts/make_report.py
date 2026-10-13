#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib", "pyyaml"]
# ///

"""Write a per-path throughput report for a results directory.

    uv run scripts/make_report.py results/search_throughput_<stamp>

Everything lands in ``<dir>/report/``: ``REPORT.md`` plus the charts from
``plot_results.py``. Safe to re-run while the sweep is still going; the report
says how many runs it covers.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plot_results import latency_breakdown  # noqa: E402
from plot_results import main as plot_main  # noqa: E402

PATH_ORDER = ["attribute", "event", "fusion", "tag", "object"]
CPU_METRIC = "cpu_core_seconds_total:mean_rate"
SATURATED = 0.9
RESOURCE_PICTURES = (
    ("resources_cpu.png", "CPU"),
    ("resources_memory.png", "Memory"),
    ("resources_gpu.png", "GPU utilisation"),
    ("resources_gpu_memory.png", "GPU memory"),
)


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _num(v: float | None, digits: int = 2) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def _planned(results: Path) -> tuple[list, list[int], list[int], list[str]]:
    sweep = (yaml.safe_load((results / "config.yaml").read_text()) or {}).get("sweep", {})
    critics = ["off" if c in (False, "off") else "on" for c in sweep.get("critic", [True])]
    return (critics, [int(k) for k in sweep.get("top_k", [])],
            [int(c) for c in sweep.get("concurrency", [])], list(sweep.get("paths") or []))


def _top_usage(resources: list[dict[str, str]], run_id: str) -> str:
    cpu_by: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    gpu: dict[str, float] = defaultdict(float)
    for r in resources:
        if r["run_id"] != run_id or _f(r["avg"]) is None:
            continue
        if r["metric"] in (CPU_METRIC, "cpu_cores"):
            cpu_by[r["metric"]][r["service"]] += _f(r["avg"])
        elif r["metric"] == "gpu_utilization":
            gpu[r["service"]] += _f(r["avg"])
    # A run shorter than the counter's window has no mean_rate; the rate()
    # gauge is the only CPU figure left, and it carries earlier work.
    cpu = cpu_by.get(CPU_METRIC) or cpu_by.get("cpu_cores") or {}
    residue = CPU_METRIC not in cpu_by and bool(cpu)
    parts = []
    if cpu:
        svc, val = max(cpu.items(), key=lambda x: x[1])
        parts.append(f"CPU nhiều nhất: {svc} {val:.1f} core"
                     + (" (run quá ngắn, số này gồm tải của run trước)" if residue else ""))
    busy = {s: v for s, v in gpu.items() if v >= 5}
    if busy:
        svc, val = max(busy.items(), key=lambda x: x[1])
        parts.append(f"GPU: {svc} {val:.0f}%")
    return "; ".join(parts) or "không có số liệu tài nguyên"


def _breakdown_table(rows: list[dict], top_k: int | None) -> list[str]:
    """One row per (path, critic, concurrency) at ``top_k``; stages as columns."""
    rows = [r for r in rows if top_k is None or r["top_k"] == top_k]
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        for label, value in r["stages"].items():
            totals[label] += value
    labels = [lbl for lbl, v in sorted(totals.items(), key=lambda kv: -kv[1]) if v > 0.0005]
    rows.sort(key=lambda r: (PATH_ORDER.index(r["path"]) if r["path"] in PATH_ORDER else 99,
                             r["critic"], r["concurrency"]))
    out = [
        f"Bảng ở top_k {top_k} (giây):" if top_k is not None else "Bảng (giây):",
        "",
        "| Path | Critic | c | Tổng | " + " | ".join(labels) + " | other |",
        "|---|---|---|---|" + "---|" * (len(labels) + 1),
    ]
    for r in rows:
        cells = [_num(r["stages"].get(lbl), 3) if lbl in r["stages"] else "-" for lbl in labels]
        out.append(f"| {r['path']} | {r['critic']} | {r['concurrency']} | "
                   f"{r['wall_sec']:.3f} | " + " | ".join(cells) + f" | {r['other_sec']:.3f} |")
    return out + [""]


def build(results: Path) -> str:
    summary = _read(results / "summary.csv")
    resources = _read(results / "resources_summary.csv")
    critics, top_ks, concs, paths = _planned(results)
    paths = paths or sorted({r["path"] for r in summary},
                            key=lambda p: PATH_ORDER.index(p) if p in PATH_ORDER else 99)
    planned = len(critics) * len(top_ks) * len(concs) * max(1, len(paths))

    cell = {(r["critic"], r["path"], int(r["top_k"]), int(r["concurrency"])): r for r in summary}
    failures = sum(int(r["failure_count"] or 0) for r in summary)
    measured = sum(int(r["measured_requests"] or 0) for r in summary)
    dirty = [r["run_id"] for r in summary if r.get("measurement_window_clean") != "True"]
    starts = sorted(r["started_at"] for r in summary if r.get("started_at"))
    ends = sorted(r["completed_at"] for r in summary if r.get("completed_at"))

    out: list[str] = [f"# Báo cáo throughput theo path — `{results.name}`", ""]
    if results.name.startswith("smoke_"):
        out += [
            "> **Đây là smoke run, không phải số đo.** Mỗi run chỉ có vài request, không "
            "có thời gian đo tối thiểu, và chỉ một mức concurrency. Nó chứng minh mọi "
            "bước của pipeline chạy được; đừng trích các con số dưới đây làm kết quả.",
            "",
        ]
    status = ("đã xong" if len(summary) >= planned
              else "**sweep đang chạy**, chạy lại script khi xong để cập nhật")
    out += [
        f"{len(summary)}/{planned} run ({status}). "
        f"Mỗi run đo **một path** với đúng câu hỏi routing vào path đó. "
        + (f"Thời gian: {starts[0][:16].replace('T', ' ')} → {ends[-1][:16].replace('T', ' ')} UTC. "
           if starts and ends else "")
        + f"{measured:,} request đo, {failures} lỗi"
        + (f", {len(dirty)} run có cửa sổ đo không sạch." if dirty else ", mọi cửa sổ đo sạch."),
        "",
        "## Kết luận",
        "",
    ]
    k0 = min(top_ks) if top_ks else None
    single = len(concs) == 1
    if single:
        out += [
            f"Throughput của từng path ở top_k {k0}, concurrency {concs[0]} (chỉ đo một "
            "mức concurrency, nên không xác định được trần):",
            "",
            "| Path | Critic | Throughput (q/s) | p50 | p95 | Tài nguyên |",
            "|---|---|---|---|---|---|",
        ]
    else:
        out += [
            "Throughput tối đa của từng path (top_k nhỏ nhất), và concurrency bắt đầu "
            f"bão hòa (đạt {SATURATED:.0%} mức tối đa):",
            "",
            "| Path | Critic | Tối đa (q/s) | Tại c | Bão hòa từ c | p50 tại mức tối đa | Tài nguyên tại mức tối đa |",
            "|---|---|---|---|---|---|---|",
        ]
    for path in paths:
        for critic in critics:
            rows = [cell[(critic, path, k0, c)] for c in concs if (critic, path, k0, c) in cell]
            if not rows:
                out.append(f"| {path} | {critic} | chưa chạy |" + " |" * (3 if single else 4))
                continue
            if single:
                r = rows[0]
                out.append(
                    f"| {path} | {critic} | **{_num(_f(r['queries_per_sec']))}** | "
                    f"{_num(_f(r['p50_sec']))} s | {_num(_f(r['p95_sec']))} s | "
                    f"{_top_usage(resources, r['run_id'])} |"
                )
                continue
            peak = max(rows, key=lambda r: _f(r["queries_per_sec"]) or 0)
            top = _f(peak["queries_per_sec"]) or 0
            sat = next(int(r["concurrency"]) for r in rows
                       if (_f(r["queries_per_sec"]) or 0) >= SATURATED * top)
            not_sat = sat == max(concs) and int(peak["concurrency"]) == max(concs)
            out.append(
                f"| {path} | {critic} | **{top:.2f}** | {peak['concurrency']} | "
                f"{'chưa bão hòa' if not_sat else sat} | {_num(_f(peak['p50_sec']))} s | "
                f"{_top_usage(resources, peak['run_id'])} |"
            )
    if not single:
        out += [
            "",
            "\"Chưa bão hòa\" nghĩa là throughput vẫn tăng tới concurrency lớn nhất đã "
            "đo, nên trần thật còn cao hơn.",
        ]
    out += [
        "",
        "## Throughput (queries/sec)",
        "",
        "![throughput](throughput.png)",
        "",
        *[line for path in paths
          if (results / "report" / f"throughput_{path}.png").exists()
          for line in (f"### Path {path}", "", f"![throughput {path}](throughput_{path}.png)", "")],
    ]
    header = "| Path | top_k | " + " | ".join(f"c={c}" for c in concs) + " |"
    sep = "|---|---|" + "---|" * len(concs)
    for critic in critics:
        out += [f"### Critic {critic}", "", header, sep]
        for path in paths:
            for k in top_ks:
                vals = [_num(_f(cell[(critic, path, k, c)]["queries_per_sec"]))
                        if (critic, path, k, c) in cell else "-" for c in concs]
                out.append(f"| {path} | {k} | " + " | ".join(vals) + " |")
        out.append("")

    out += ["## Latency p50 / p95 (giây)", "", "![latency](latency.png)", ""]
    for critic in critics:
        out += [f"### Critic {critic}", "", header, sep]
        for path in paths:
            for k in top_ks:
                vals = []
                for c in concs:
                    r = cell.get((critic, path, k, c))
                    vals.append(f"{_num(_f(r['p50_sec']))} / {_num(_f(r['p95_sec']))}"
                                if r else "-")
                out.append(f"| {path} | {k} | " + " | ".join(vals) + " |")
        out.append("")

    report = results / "report"
    pictures = [(n, title) for n, title in RESOURCE_PICTURES if (report / n).exists()]
    if pictures:
        out += [
            "## Tài nguyên theo path, critic, top_k và concurrency",
            "",
            "Mỗi hàng là một path, mỗi cột là critic off/on, mỗi màu là một service "
            "(3 service dùng nhiều nhất trong từng ô), kiểu nét là top_k. Giá trị là "
            "trung bình trong cửa sổ đo của run.",
            "",
        ]
        for n, title in pictures:
            out += [f"### {title}", "", f"![{title}]({n})", ""]
    if (report / "resources_timeline.png").exists():
        out += [
            "## Tài nguyên theo thời gian",
            "",
            "Toàn bộ sweep theo trục thời gian (chỉ cửa sổ đo; khoảng trống là warmup, "
            "cooldown). Nền mỗi run tô theo màu path, đậm hơn khi critic on.",
            "",
            "![resources timeline](resources_timeline.png)",
            "",
        ]

    out += ["## Latency theo từng bước", ""]
    breakdown = latency_breakdown(results, summary)
    if not breakdown:
        out += [
            "Sweep này **không có số liệu theo bước**: nó chạy trước khi harness lưu "
            "`timings` mà CLI trả về, nên mỗi request chỉ có tổng latency. Các sweep "
            "chạy từ bây giờ sẽ có biểu đồ và bảng ở đây.",
            "",
        ]
    else:
        out += [
            "Thời gian trung bình mỗi request cho từng bước (self time, giây). "
            "\"other\" = tổng latency trừ thời gian CLI tự đo: khởi động process, đọc "
            "config, tạo client. Các bước chạy song song (hai nhánh của fusion, VLM "
            "verify từng hit) có thể cộng lại lớn hơn tổng latency (vạch đen).",
            "",
        ]
        out += [line for path in paths
                if (report / f"latency_breakdown_{path}.png").exists()
                for line in (f"![latency breakdown {path}](latency_breakdown_{path}.png)", "")]
        out += _breakdown_table(breakdown, k0)

    out += [
        "## Lưu ý",
        "",
        "- Mỗi path dùng một câu hỏi cố định, nên vector query được cache sau lần đầu: "
        "CPU/GPU của vss-rtvi-embed gần 0 và không đại diện cho tải thật.",
        "- Mỗi latency gồm thời gian khởi động một process `vss` "
        f"({_num(_f(summary[0].get('cli_spawn_cost_sec')) if summary else None)} s).",
        "",
        "---",
        f"Tạo bởi `scripts/make_report.py` từ `summary.csv` và `resources_summary.csv` "
        f"của `{results.name}`.",
    ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results_dir", type=Path)
    args = parser.parse_args(argv)
    if not (args.results_dir / "summary.csv").exists():
        print(f"ERROR: no summary.csv in {args.results_dir}", file=sys.stderr)
        return 2
    report = args.results_dir / "report"
    report.mkdir(exist_ok=True)
    plot_main([str(args.results_dir), "--out", str(report)])
    (report / "REPORT.md").write_text(build(args.results_dir), encoding="utf-8")
    print(f"  wrote {report / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
