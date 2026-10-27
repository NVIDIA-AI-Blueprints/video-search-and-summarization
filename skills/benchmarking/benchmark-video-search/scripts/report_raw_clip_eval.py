# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Build a self-contained, shareable HTML report from a raw clip evaluation."""

from __future__ import annotations

import argparse
import html
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def read_json(path: Path, *, required: bool = False) -> Any:
    if not path.exists():
        if required:
            raise ValueError(f"Required run artifact is missing: {path.name}")
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {path.name}: {error}") from error


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text().splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {path.name}: {error}") from error
    return rows


def percent(value: Any, digits: int = 2) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value * 100:.{digits}f}%"


def milliseconds(value: Any) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value * 1000:.1f}"


def format_ms(value: Any) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    return f"{value:.1f}"


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(q * len(values)) - 1)]


def ap_at(ranking: list[Any], relevant: list[Any], k: int) -> float | None:
    """Compute the evaluator's clip AP@k from an already deduplicated ranking."""
    relevant_set = set(relevant)
    if not relevant_set or k < 1:
        return None
    seen: set[Any] = set()
    found = 0
    numerator = 0.0
    rank = 0
    for clip_id in ranking:
        if clip_id in seen:
            continue
        seen.add(clip_id)
        rank += 1
        if rank > k:
            break
        if clip_id in relevant_set:
            found += 1
            numerator += found / rank
    return numerator / min(len(relevant_set), k)


def capture_file(root: Path, mode: str, qid: str, k: int, multi_k: bool) -> Path:
    # The evaluator validates query IDs as single path components. Repeat the
    # check here because report inputs may come from a damaged or hand-edited run.
    if Path(qid).name != qid or qid in {".", ".."}:
        raise ValueError(f"Unsafe query ID in queries.json: {qid!r}")
    base = root / "captures" / mode
    if multi_k:
        base /= f"top-k-{k}"
    return base / f"{qid}.json"


def summary_for_k(value: dict[str, Any], k: int) -> dict[str, Any]:
    by_top_k = value.get("by_top_k")
    if isinstance(by_top_k, dict):
        return by_top_k.get(str(k), {})
    if str(k) in value and isinstance(value[str(k)], dict):
        return value[str(k)]
    return value if len(value.get("summary", {})) else {}


def route_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(record.get("query_id")): record
        for record in records
        if record.get("query_id") and record.get("stage", "measured") == "measured"
    }


def load_captures(
    root: Path,
    queries: list[dict[str, Any]],
    modes: list[str],
    ks: list[int],
    multi_k: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    route_records = route_map(read_jsonl(root / "routes.jsonl"))
    route_dir = root / "routing"
    route_failures = (
        {
            path.name.removesuffix(".failed.json"): read_json(path)
            for path in route_dir.glob("*.failed.json")
        }
        if route_dir.is_dir()
        else {}
    )
    rows: list[dict[str, Any]] = []
    missing: list[str] = []

    for mode in modes:
        for query in queries:
            qid = str(query["query_id"])
            per_k: dict[str, Any] = {}
            for k in ks:
                path = capture_file(root, mode, qid, k, multi_k)
                capture = read_json(path)
                if not isinstance(capture, dict):
                    missing.append(f"{mode}/{qid}/k={k}")
                    continue
                score = capture.get("score") or {}
                ranking = score.get("ranking") or []
                ap_key = f"ap_at_{k}"
                ap = score.get(ap_key)
                if ap is None:
                    ap = ap_at(ranking, query.get("relevant_clip_ids", []), k)
                per_k[str(k)] = {
                    "capture": capture,
                    "score": score,
                    "ranking": ranking,
                    "ap": ap,
                }

            route = route_records.get(qid, {}) if mode == "live-routed" else {}
            route_failure = route_failures.get(qid) if mode == "live-routed" else None
            available = [per_k[str(k)] for k in ks if str(k) in per_k]
            chosen = available[0]["capture"] if available else {}
            row_route = (
                route.get("chosen_route")
                or chosen.get("path")
                or (mode.removesuffix("-only") if mode != "live-routed" else "unknown")
            )
            ranked_source = max(
                available,
                key=lambda item: len(item.get("ranking") or []),
                default={},
            )
            ranking = ranked_source.get("ranking", [])
            if "10" in per_k:
                ap10 = per_k["10"].get("ap")
            elif max(ks, default=0) >= 10:
                ap10 = ap_at(ranking, query.get("relevant_clip_ids", []), 10)
            else:
                ap10 = None

            full_k = max(ks) if ks else None
            full_source = per_k.get(str(full_k), {}) if full_k is not None else {}
            full_score = full_source.get("score", {})
            full_ap = full_source.get("ap")
            per_query_latency = {}
            for k, item in per_k.items():
                capture = item["capture"]
                per_query_latency[k] = {
                    "routing_s": capture.get("routing_s"),
                    "request_preparation_s": capture.get("request_preparation_s"),
                    "retrieval_s": capture.get("response_s"),
                    "end_to_end_s": (
                        capture.get("response_s", 0) + capture.get("routing_s", 0)
                        if isinstance(capture.get("response_s"), (int, float))
                        else None
                    ),
                }
            primary_k = 10 if "10" in per_query_latency else (str(min(ks)) if ks else None)
            primary_capture = per_k.get(primary_k, {}).get("capture", {})
            primary_latency = per_query_latency.get(primary_k, {})
            rows.append(
                {
                    "query_id": qid,
                    "query": query,
                    "mode": mode,
                    "route": row_route,
                    "route_details": route or route_failure,
                    "decomposition": route.get("decomposition") if route else None,
                    "plan": route.get("plan") if route else None,
                    "payload": primary_capture.get("payload"),
                    "available_ks": [int(k) for k in per_k],
                    "ap_by_k": {k: per_k[str(k)].get("ap") for k in ks if str(k) in per_k},
                    "ap10": ap10,
                    "full_ap": full_ap,
                    "full_k": full_k,
                    "full_relevant_count": full_score.get("relevant_count"),
                    "ranking": ranking,
                    "raw_hits": full_score.get("raw_hits"),
                    "unique_clips": full_score.get("unique_clips"),
                    "per_k_latency": per_query_latency,
                    "primary_k": primary_k,
                    "primary_latency": primary_latency,
                    "per_k_data": {
                        k: per_k[str(k)]["capture"].get("data", [])
                        for k in ks
                        if str(k) in per_k
                    },
                    "per_k_payload": {
                        k: per_k[str(k)]["capture"].get("payload")
                        for k in ks
                        if str(k) in per_k
                    },
                    "score": full_score,
                    "status": (
                        "captured"
                        if len(available) == len(ks)
                        else "partial captures"
                        if available
                        else "routing failed"
                        if route_failure
                        else "missing capture"
                    ),
                }
            )
    return rows, missing


def query_group_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            (row["mode"], row["query"].get("query_domain", "unknown"), row["route"])
        ].append(row)
    result = []
    for (mode, domain, route), members in sorted(groups.items()):
        scores10 = [
            row["ap10"]
            for row in members
            if isinstance(row.get("ap10"), (int, float))
        ]
        full_scores = [
            row["full_ap"]
            for row in members
            if isinstance(row.get("full_ap"), (int, float))
        ]
        latencies = [
            row["primary_latency"]["end_to_end_s"] * 1000
            for row in members
            if isinstance(
                row.get("primary_latency", {}).get("end_to_end_s"), (int, float)
            )
        ]
        top10_misses = [
            row
            for row in members
            if not (
                set(row["ranking"][:10])
                & set(row["query"].get("relevant_clip_ids", []))
            )
        ]
        no_rankings = [row for row in members if not row.get("ranking")]
        result.append(
            {
                "mode": mode,
                "domain": domain,
                "route": route,
                "queries": len(members),
                "scored_queries": len(scores10),
                "map10": mean(scores10),
                "full_map": mean(full_scores),
                "end_to_end_mean_ms": mean(latencies),
                "no_relevant_top10": len(top10_misses),
                "no_ranking": len(no_rankings),
                "top10_miss_rate": len(top10_misses) / len(members) if members else None,
            }
        )
    return result


def clean_identity(identity: dict[str, Any]) -> dict[str, Any]:
    clean = dict(identity)
    hashes = clean.get("input_sha256")
    if isinstance(hashes, dict):
        clean["input_sha256"] = [
            {"file": Path(name).name, "sha256": digest}
            for name, digest in hashes.items()
        ]
    return clean


def summary_details(value: Any) -> Any:
    """Keep aggregate evaluation fields while avoiding a duplicate query dump."""
    if isinstance(value, dict):
        return {
            key: summary_details(item)
            for key, item in value.items()
            if key != "rows"
        }
    if isinstance(value, list):
        return [summary_details(item) for item in value]
    return value


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def pretty(value: Any) -> str:
    if value is None:
        return "Not recorded"
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def stat_card(label: str, value: Any, note: str = "") -> str:
    return (
        '<article class="card"><div class="label">'
        + esc(label)
        + '</div><div class="value">'
        + esc(value)
        + '</div><div class="note">'
        + esc(note)
        + "</div></article>"
    )


def detail_block(title: str, value: Any) -> str:
    return (
        "<details><summary>"
        + esc(title)
        + "</summary><pre>"
        + esc(pretty(value))
        + "</pre></details>"
    )


def aggregate_table(
    summary: dict[str, Any], modes: list[str], ks: list[int], rows: list[dict[str, Any]]
) -> str:
    lines = [
        "<table><thead><tr><th>Mode</th><th>Depth</th><th>Event mAP</th><th>PAS mAP</th><th>Overall mAP</th><th>Retrieval mean / p95 (ms)</th><th>Routing mean / p95 (ms)</th><th>End-to-end mean / p95 (ms)</th><th>Routes</th></tr></thead><tbody>"
    ]
    for mode in modes:
        mode_summary = summary.get(mode, {})
        for k in ks:
            current = summary_for_k(mode_summary, k)
            accuracy = current.get("summary", {})
            latency = current.get("latency", {})
            routes = current.get("routes", {})
            captured = [
                row
                for row in rows
                if row["mode"] == mode and str(k) in row.get("ap_by_k", {})
            ]
            if not accuracy and captured:
                accuracy = {
                    domain: {
                        f"mAP@{k}_percent": 100
                        * statistics.mean(
                            row["ap_by_k"][str(k)]
                            for row in captured
                            if domain == "overall"
                            or row["query"].get("query_domain") == domain
                            if isinstance(row["ap_by_k"].get(str(k)), (int, float))
                        )
                    }
                    for domain in ("event", "pas", "overall")
                    if any(
                        (domain == "overall" or row["query"].get("query_domain") == domain)
                        and isinstance(row["ap_by_k"].get(str(k)), (int, float))
                        for row in captured
                    )
                }
                stages = {
                    stage: [
                        row.get("per_k_latency", {}).get(str(k), {}).get(key)
                        for row in captured
                        if isinstance(
                            row.get("per_k_latency", {}).get(str(k), {}).get(key),
                            (int, float),
                        )
                    ]
                    for stage, key in (
                        ("retrieval", "retrieval_s"),
                        ("routing", "routing_s"),
                        ("end_to_end", "end_to_end_s"),
                    )
                }
                latency = {
                    stage: {
                        "mean_ms": statistics.mean(values) * 1000,
                        "p95_ms": percentile(values, 0.95) * 1000,
                    }
                    for stage, values in stages.items()
                    if values
                }
                routes = dict(Counter(row["route"] for row in captured))
            def accuracy_cell(domain: str) -> str:
                stats = accuracy.get(domain, {})
                key = next((name for name in stats if name.startswith("mAP@") and name.endswith("_percent")), None)
                return f"{stats[key]:.2f}%" if key and isinstance(stats.get(key), (int, float)) else "—"
            def latency_cell(stage: str) -> str:
                stats = latency.get(stage, {})
                if not stats:
                    return "—"
                return f"{stats.get('mean_ms', 0):.1f} / {stats.get('p95_ms', 0):.1f}"
            route_text = ", ".join(f"{name}: {count}" for name, count in sorted(routes.items())) or "—"
            lines.append(
                "<tr>"
                f"<td>{esc(mode)}</td><td>{k}</td>"
                f"<td>{esc(accuracy_cell('event'))}</td><td>{esc(accuracy_cell('pas'))}</td>"
                f"<td>{esc(accuracy_cell('overall'))}</td>"
                f"<td>{esc(latency_cell('retrieval'))}</td><td>{esc(latency_cell('routing'))}</td>"
                f"<td>{esc(latency_cell('end_to_end'))}</td><td>{esc(route_text)}</td></tr>"
            )
    lines.append("</tbody></table>")
    return "".join(lines)


def diagnostic_table(groups: list[dict[str, Any]]) -> str:
    lines = [
        "<table><thead><tr><th>Mode</th><th>GT type</th><th>Route</th><th>Scored / queries</th><th>mAP@10</th><th>Full / deepest mAP</th><th>No relevant clip in top 10</th><th>No ranking</th><th>Mean end-to-end (ms)</th></tr></thead><tbody>"
    ]
    for group in groups:
        lines.append(
            "<tr>"
            f"<td>{esc(group['mode'])}</td><td>{esc(group['domain'])}</td><td>{esc(group['route'])}</td>"
            f"<td>{group['scored_queries']} / {group['queries']}</td><td>{esc(percent(group['map10']))}</td>"
            f"<td>{esc(percent(group['full_map']))}</td>"
            f"<td>{group['no_relevant_top10']} ({esc(percent(group['top10_miss_rate']))})</td>"
            f"<td>{group['no_ranking']}</td>"
            f"<td>{esc(format_ms(group['end_to_end_mean_ms']))}</td></tr>"
        )
    lines.append("</tbody></table>")
    return "".join(lines)


def rerank_section(root: Path) -> str:
    """Original vs reranked mAP from rerank_raw_clip_eval.py outputs, if any."""

    def points(value: Any) -> str:
        return f"{value:.2f}%" if isinstance(value, (int, float)) else "—"

    parts = []
    for path in sorted((root / "rerank").glob("*/comparison.json")):
        comparison = read_json(path) or {}
        identity = comparison.get("identity", {})
        lines = [
            "<table><thead><tr><th>Setting</th><th>Mode</th><th>Depth</th><th>Variant</th><th>Event mAP</th><th>PAS mAP</th><th>Overall mAP</th><th>Δ overall vs stage-1 pool</th><th>Rerank mean / p95 (ms)</th><th>Frames / candidate</th><th>Score failures</th></tr></thead><tbody>"
        ]
        for row in comparison.get("rows", []):
            latency = row.get("rerank_latency") or {}
            delta = row.get("delta_overall_vs_stage1_pool")
            frames = row.get("mean_frames_per_candidate")
            lines.append(
                "<tr>"
                f"<td>{esc(row.get('setting'))}</td><td>{esc(row.get('mode'))}</td><td>{esc(row.get('k'))}</td>"
                f"<td>{esc(row.get('variant'))}</td><td>{esc(points(row.get('event')))}</td>"
                f"<td>{esc(points(row.get('pas')))}</td><td>{esc(points(row.get('overall')))}</td>"
                f"<td>{esc('—' if delta is None else f'{delta:+.2f} pts')}</td>"
                f"<td>{esc(format_ms(latency.get('mean_ms')))} / {esc(format_ms(latency.get('p95_ms')))}</td>"
                f"<td>{esc('—' if frames is None else f'{frames:.1f}')}</td>"
                f"<td>{esc(row.get('score_failures'))}</td></tr>"
            )
        lines.append("</tbody></table>")
        state = "" if (path.parent / "complete.json").is_file() else " · <strong>incomplete</strong>"
        parts.append(
            f"<h3>{esc(path.parent.name)}</h3><p class=\"subtle\">Reranker {esc(identity.get('reranker'))}"
            f" · model {esc(identity.get('model_id') or '—')} · LoRA {esc(identity.get('lora_path') or 'none')}"
            f" · depth {esc(identity.get('rerank_depth'))} of stage-1 k={esc(identity.get('stage1_k'))}"
            f" · query text {esc(identity.get('query_text'))}{state}</p>"
            + '<div class="table-wrap">'
            + "".join(lines)
            + "</div>"
            + detail_block("Settings, variant definitions and per-route mAP", comparison)
        )
    if not parts:
        return ""
    return (
        '<section><h2>Reranker comparison</h2><p class="subtle">Each variant ranks the same saved stage-1 hits. '
        "Compare “reranked” with “stage-1 pool”, which is exactly what the reranker saw; “baseline” is the run's own capture at that depth.</p>"
        + "".join(parts)
        + "</section>"
    )


def query_table(rows: list[dict[str, Any]], ks: list[int], gallery_size: int) -> str:
    full_label = (
        "Full mAP"
        if gallery_size > 0 and ks and max(ks) >= gallery_size
        else f"AP@{max(ks)}"
        if ks
        else "Deepest AP"
    )
    headings = ["Mode", "Query ID", "GT type", "Chosen route", "Query", "Relevant", "AP@10", full_label]
    primary_k = 10 if 10 in ks else (min(ks) if ks else None)
    headings += [
        f"Search response @k={primary_k} (ms)",
        "Request prep (ms)",
        "Routing (ms)",
        f"End-to-end @k={primary_k} (ms)",
        "Status",
        "Details",
    ]
    lines = ["<table id=\"queries\"><thead><tr>"]
    lines.extend(f"<th>{esc(heading)}</th>" for heading in headings)
    lines.append("</tr></thead><tbody>")
    for row in rows:
        query = row["query"]
        domain = query.get("query_domain", "unknown")
        relevant = query.get("relevant_clip_ids", [])
        latency = row.get("per_k_latency", {}).get(str(primary_k), {}) if primary_k is not None else {}
        query_details = {
            "ground_truth": query,
            "route_record": row.get("route_details"),
            "decomposition": row.get("decomposition"),
            "plan": row.get("plan"),
            "request_payload_by_k": row.get("per_k_payload"),
            "AP and scoring": row.get("score"),
            "AP by top-k": row.get("ap_by_k"),
            "retrieved unique clip ranking": row.get("ranking"),
            "per-k latencies (seconds)": row.get("per_k_latency"),
            "retrieval hits by k": row.get("per_k_data"),
        }
        details = "<details><summary>Inspect</summary>" + "".join(
            detail_block(title, value) for title, value in query_details.items() if value is not None
        ) + "</details>"
        lines.append(
            f"<tr data-mode=\"{esc(row['mode'])}\" data-domain=\"{esc(domain)}\" data-route=\"{esc(row['route'])}\">"
            f"<td>{esc(row['mode'])}</td><td>{esc(row['query_id'])}</td><td>{esc(domain)}</td>"
            f"<td>{esc(row['route'])}</td><td class=\"query-text\">{esc(query.get('query', ''))}</td>"
            f"<td>{len(relevant)}</td><td>{esc(percent(row.get('ap10')))}</td>"
            f"<td>{esc(percent(row.get('full_ap')))}</td>"
            f"<td>{esc(milliseconds(latency.get('retrieval_s')))}</td>"
            f"<td>{esc(milliseconds(latency.get('request_preparation_s')))}</td>"
            f"<td>{esc(milliseconds(latency.get('routing_s')))}</td>"
            f"<td>{esc(milliseconds(latency.get('end_to_end_s')))}</td>"
            f"<td>{esc(row['status'])}</td><td>{details}</td></tr>"
        )
    lines.append("</tbody></table>")
    return "".join(lines)


def build_html(root: Path) -> str:
    identity = read_json(root / "identity.json", required=True)
    query_data = read_json(root / "queries.json", required=True)
    queries = query_data.get("queries", []) if isinstance(query_data, dict) else []
    if not queries:
        raise ValueError("queries.json has no queries")

    modes = identity.get("modes") or []
    ks = identity.get("top_k_values") or []
    if not modes or not ks:
        raise ValueError("identity.json must contain modes and top_k_values")
    modes = [str(mode) for mode in modes]
    if any(mode not in {"embed-only", "attribute-only", "live-routed"} for mode in modes):
        raise ValueError("identity.json contains an unsupported evaluation mode")
    ks = sorted({int(k) for k in ks})
    if any(k < 1 or k > 1000 for k in ks):
        raise ValueError("identity.json contains an invalid top-k value")
    summary = read_json(root / "summary.json") or {}
    metrics = read_json(root / "metrics.json") or {}
    complete = read_json(root / "complete.json")
    failure = read_json(root / "failed.json")
    gpu = read_json(root / "gpu-summary.json")
    startup = read_json(root / "startup.json")
    tokens = read_json(root / "token-usage.json")
    prompt = read_json(root / "prompt.json")
    multi_k = bool(identity.get("multi_k", len(ks) > 1))

    rows, missing = load_captures(root, queries, modes, ks, multi_k)
    groups = query_group_stats(rows)
    gallery_size = int(identity.get("gallery") or 0)
    is_complete = bool(complete and complete.get("all_modes_complete")) and not missing
    status = (
        "Complete"
        if is_complete
        else "Failed / partial"
        if failure
        else "Partial / incomplete"
    )
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    clean = clean_identity(identity)

    domains = Counter(query.get("query_domain", "unknown") for query in queries)
    route_counts = Counter(row["route"] for row in rows if row["available_ks"])
    captured_count = sum(bool(row["available_ks"]) for row in rows)
    latency_definition = identity.get("latency_definition", "Not recorded")
    cards = "".join(
        [
            stat_card("Run status", status, f"{captured_count} captured query/mode rows; {len(missing)} missing depth captures"),
            stat_card("Queries", len(queries), ", ".join(f"{d}: {c}" for d, c in sorted(domains.items()))),
            stat_card("Gallery", gallery_size, "registered clips/sources"),
            stat_card("Modes × depths", f"{len(modes)} × {len(ks)}", f"modes: {', '.join(modes)}; k: {', '.join(map(str, ks))}"),
            stat_card("Routes", ", ".join(f"{name}: {count}" for name, count in sorted(route_counts.items())) or "—", "measured captured queries, all modes"),
            stat_card("Source commit", str(identity.get("source_commit") or "—")[:12], str(identity.get("source_commit") or "")),
        ]
    )

    missing_section = ""
    if missing:
        missing_section = (
            '<section class="warning"><h2>Missing captures</h2><p>This report is partial. '
            "Missing query/depth captures are listed below. Do not interpret the aggregate as a completed run.</p><pre>"
            + esc("\n".join(missing))
            + "</pre></section>"
        )
    if failure:
        missing_section += (
            '<section class="warning"><h2>Run failure</h2><pre>'
            + esc(pretty(failure))
            + "</pre></section>"
        )
    if (not complete or not complete.get("all_modes_complete")) and not failure:
        missing_section += (
            '<section class="warning"><h2>Run completion is unconfirmed</h2><p>The run did not record '
            "successful completion. Review missing captures and the progress/failure artifacts before using its aggregates.</p></section>"
        )

    provenance = {
        "identity": clean,
        "completion": complete,
        "failure": failure,
        "startup": startup,
        "summary.json aggregate details": summary_details(summary),
        "metrics.json": metrics,
        "latency definition": latency_definition,
    }
    report_prompt = detail_block("Prompt and routing protocol", prompt) if prompt else ""
    gpu_section = detail_block("GPU telemetry and sampling windows", gpu) if gpu else "<p>GPU telemetry was not recorded.</p>"
    token_section = detail_block("Token usage by stage", tokens) if tokens else "<p>Token usage was not recorded.</p>"

    options = lambda values: "".join(
        f'<option value="{esc(value)}">{esc(value)}</option>' for value in values
    )
    filter_controls = (
        '<label>Mode <select id="mode-filter"><option value="">All</option>'
        + options(sorted({r["mode"] for r in rows}))
        + '</select></label><label>GT type <select id="domain-filter"><option value="">All</option>'
        + options(sorted({str(r["query"].get("query_domain", "unknown")) for r in rows}))
        + '</select></label><label>Route <select id="route-filter"><option value="">All</option>'
        + options(sorted({r["route"] for r in rows}))
        + '</select></label><label>Search <input id="query-filter" type="search" placeholder="Query text or ID"></label>'
        '<span id="visible-count"></span>'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Raw clip evaluation report</title>
<style>
:root{{--ink:#17212b;--muted:#526273;--line:#d7dfe7;--panel:#fff;--bg:#f3f6f9;--accent:#176b87;--warn:#fff3cd;--good:#e5f5eb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
header{{background:#102a43;color:white;padding:28px max(20px,calc((100vw - 1500px)/2));}}header h1{{margin:0 0 5px;font-size:28px}}header p{{margin:0;color:#c9d8e5}}
main{{max-width:1500px;margin:22px auto;padding:0 20px 50px}}section,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;margin:16px 0;box-shadow:0 2px 8px #102a430a}}
h2{{font-size:20px;margin:0 0 12px}}h3{{font-size:16px;margin:14px 0 8px}}p.subtle,.note{{color:var(--muted);font-size:13px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0}}
.card{{background:white;border:1px solid var(--line);border-radius:10px;padding:14px}}.label{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}.value{{font-size:22px;font-weight:700;overflow-wrap:anywhere}}.note{{margin-top:3px;overflow-wrap:anywhere}}
.warning{{background:var(--warn);border-color:#e7c968}}.ok{{background:var(--good)}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid var(--line)}}th{{background:#eaf0f5;position:sticky;top:0;z-index:1;cursor:pointer}}tbody tr:hover{{background:#f7fbfd}}.table-wrap{{overflow:auto;max-height:75vh;border:1px solid var(--line);border-radius:7px}}
.filters{{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:12px 0}}label{{display:grid;gap:4px;font-size:13px;color:var(--muted)}}select,input{{font:inherit;padding:7px 9px;border:1px solid #9aabba;border-radius:5px;color:var(--ink);background:white}}#visible-count{{margin-left:auto;color:var(--muted)}}.query-text{{min-width:240px;max-width:500px;white-space:pre-wrap;overflow-wrap:anywhere}}
details{{margin:3px 0}}summary{{cursor:pointer;color:var(--accent);font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f9;padding:10px;border-radius:6px;max-height:550px;overflow:auto;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}}.pill{{display:inline-block;border-radius:100px;background:#e8f1f6;padding:2px 9px;font-size:12px}}.footer{{color:var(--muted);font-size:12px}}
@media(max-width:700px){{main{{padding:0 10px 30px}}section{{padding:12px}}header{{padding:20px}}}}
</style></head><body>
<header><h1>Raw clip evaluation report</h1><p>Generated {esc(generated)} · Run {esc(identity.get('run_id') or 'unknown')} · <span class="pill">{esc(status)}</span></p></header>
<main>
<section><h2>Run summary</h2><div class="cards">{cards}</div><p class="subtle">Latency definition: {esc(latency_definition)}</p></section>
{missing_section}
<section><h2>Accuracy and latency by mode and depth</h2>{aggregate_table(summary, modes, ks, rows)}</section>
{rerank_section(root)}
<section><h2>Diagnostic cuts by query type and selected route</h2><p class="subtle">These groups expose weak spots; groups contain different queries and are descriptive, not causal comparisons.</p>{diagnostic_table(groups)}</section>
<section><h2>Run configuration and provenance</h2>{detail_block('Identity, versions, settings, and input hashes', provenance)}</section>
<section><h2>GPU telemetry</h2>{gpu_section}</section>
<section><h2>Token usage</h2>{token_section}</section>
{f'<section><h2>Prompt</h2>{report_prompt}</section>' if report_prompt else ''}
<section><h2>Per-query results</h2><p class="subtle">One row per query and mode. AP@10 is shown when available or derived from a deeper saved ranking. “Full mAP” is labeled only when the deepest k covers the recorded gallery size. Latencies are client-observed; routing is one call shared across depths.</p>
<div class="filters">{filter_controls}</div><div class="table-wrap">{query_table(rows, ks, gallery_size)}</div></section>
<p class="footer">This file is self-contained and works offline. Query text, decomposition, captured rankings, and run metadata are included; no live service is contacted.</p>
</main>
<script>
(()=>{{const body=document.querySelector('#queries tbody');const filters=['mode','domain','route'].map(x=>document.getElementById(x+'-filter'));const search=document.getElementById('query-filter');const count=document.getElementById('visible-count');
function apply(){{let shown=0;for(const row of body.rows){{const ok=(!filters[0].value||row.dataset.mode===filters[0].value)&&(!filters[1].value||row.dataset.domain===filters[1].value)&&(!filters[2].value||row.dataset.route===filters[2].value)&&(!search.value||row.textContent.toLowerCase().includes(search.value.toLowerCase()));row.hidden=!ok;if(ok)shown++}}count.textContent=shown+' rows';}}
filters.forEach(f=>f.addEventListener('change',apply));search.addEventListener('input',apply);apply();
document.querySelectorAll('#queries th').forEach((head,index)=>head.addEventListener('click',()=>{{const rows=[...body.rows];const asc=head.dataset.asc!=='true';head.dataset.asc=String(asc);rows.sort((a,b)=>{{const x=a.cells[index].textContent.trim(),y=b.cells[index].textContent.trim();const nx=Number(x.replace('%','')),ny=Number(y.replace('%',''));return Number.isFinite(nx)&&Number.isFinite(ny)?(asc?nx-ny:ny-nx):(asc?x.localeCompare(y):y.localeCompare(x));}});rows.forEach(row=>body.appendChild(row));}}));
}})();
</script></body></html>"""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path, help="Evaluator output directory")
    parser.add_argument("--output", type=Path, help="HTML output path (default: <run-dir>/report.html)")
    args = parser.parse_args(argv)
    root = args.run_dir.resolve(strict=True)
    output = args.output or (root / "report.html")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = build_html(root)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(report, encoding="utf-8")
    temporary.replace(output)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
