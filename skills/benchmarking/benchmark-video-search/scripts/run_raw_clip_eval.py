# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Raw clip mAP@10 evaluation on an existing gallery, through CLI or persistent SDK."""

from __future__ import annotations

import time

_PYTHON_ENTRY = time.perf_counter()

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import threading
from collections import Counter
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

import report_raw_clip_eval as report
from flows import clip_metrics, routing
from flows.base import REPO_ROOT
from flows.decompose import MAX_TOKENS, LiveDecomposer

MODES = ("embed-only", "attribute-only", "live-routed")


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def load_inputs(root: Path, manifest_path: Path, query_ids: Path | None):
    manifest = read(manifest_path)
    clips = manifest["clips"]
    if (
        not manifest.get("complete")
        or not clips
        or any(
            c.get("success") is False or c.get("phase", "complete") != "complete"
            for c in clips
        )
    ):
        raise ValueError("The ingestion manifest must describe a complete gallery")
    sensors = clip_metrics.gallery_sensors(clips)
    gallery = read(root / "additional_gt/clips_gt.json")["gallery"]
    gallery_ids = [c["chunk_id"] for c in gallery]
    if len(set(gallery_ids)) != len(gallery_ids) or set(gallery_ids) != set(
        sensors.values()
    ):
        raise ValueError("Dataset and ingestion manifest gallery identities differ")
    if any(not (root / "clips" / c["filename"]).is_file() for c in clips):
        raise ValueError("Dataset gallery video files are missing")
    queries = read(root / "gt/queries_gt.json")["queries"]
    indexed = {q["query_id"]: q for q in queries}
    if not queries or len(indexed) != len(queries):
        raise ValueError("Query IDs must be nonempty and unique")
    for q in queries:
        if (
            not q["query_id"]
            or q["query_domain"] not in {"event", "pas"}
            or not q["query"].strip()
        ):
            raise ValueError("Invalid query identity, domain, or text")
        clip_metrics.score_query(q, [], sensors)
    if query_ids is not None:
        requested = read(query_ids)
        if isinstance(requested, dict):
            requested = requested["queries"]
        ids = [q if isinstance(q, str) else q["query_id"] for q in requested]
        if not ids or len(set(ids)) != len(ids) or not set(ids) <= indexed.keys():
            raise ValueError(
                "Selected query IDs must be nonempty, unique, and present in dataset GT"
            )
        queries = [indexed[qid] for qid in ids]
    start = datetime.fromisoformat(manifest["upload_timestamp"])
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    durations = [float(c["duration_s"]) for c in clips]
    if any(not math.isfinite(d) or d <= 0 for d in durations):
        raise ValueError("Clip durations must be positive and finite")
    end = start + timedelta(seconds=math.ceil(max(durations)))
    scope = {
        "timestamp_start": start.isoformat().replace("+00:00", "Z"),
        "timestamp_end": end.isoformat().replace("+00:00", "Z"),
    }
    return queries, manifest, sensors, scope


def live_routing_plan(text: str, decomposition: dict[str, Any], profile: str):
    """Map a fresh response to the selected text and gallery-scope contract."""
    if profile == "product":
        return routing.plan_for(text, decomposition)
    if profile not in {"v22-original", "person-state-photo"}:
        raise ValueError(f"Unknown live-routing profile: {profile!r}")
    if profile == "person-state-photo":
        from flows.person_state import person_state_route

        return {
            "path": person_state_route(decomposition, original_query=text),
            "query": text,
            "attributes": ["A photo of " + text.strip().rstrip(".") + "."],
            "source_type": "video_file",
            "video_sources": [],
        }
    # Only route selection consumes model output. Neither rewritten text nor
    # inferred source/time/count filters enter the fixed-gallery request.
    return {
        "path": routing.route(decomposition),
        "query": text,
        "attributes": [text],
        "source_type": "video_file",
        "video_sources": [],
    }


def build_request(
    text: str, mode: str, plan: dict[str, Any], scope: dict[str, str], args
):
    path = (
        plan["path"]
        if mode == "live-routed"
        else {"embed-only": "embed", "attribute-only": "attribute"}[mode]
    )
    if path not in {"embed", "attribute", "fusion"}:
        raise ValueError(
            f"Unsupported live route {path!r}; raw evaluation never silently falls back"
        )
    payload = {
        "source_type": plan.get("source_type", "video_file"),
        "top_k": args.top_k,
    }
    if path in {"embed", "fusion"}:
        payload.update(
            query=plan["query"] if mode == "live-routed" else text,
            min_cosine_similarity=args.min_cosine_similarity,
        )
    if path in {"attribute", "fusion"}:
        payload["attributes"] = plan["attributes"] if mode == "live-routed" else [text]
        if path == "attribute" and getattr(args, "attribute_frame_pool_size", 1) != 1:
            payload["attribute_frame_pool_size"] = args.attribute_frame_pool_size
    if mode == "live-routed" and plan.get("video_sources"):
        payload["video_sources"] = plan["video_sources"]
    for key in ("timestamp_start", "timestamp_end"):
        payload[key] = plan.get(key) or scope[key]
    if path == "attribute":
        payload.update(
            candidate_limit=args.candidate_limit,
            max_hits_per_source=args.max_hits_per_source,
        )
    return path, payload


def cli_argv(
    command: list[str], path: str, payload: dict[str, Any], merge: bool
) -> list[str]:
    argv = [*command, "search", "run", path]
    for key, value in payload.items():
        flag = "--" + {"attributes": "attribute", "video_sources": "video-source"}.get(
            key, key.replace("_", "-")
        )
        for item in value if isinstance(value, list) else [value]:
            argv += [flag, str(item)]
    if not merge:
        argv.append("--no-merge-adjacent")
    return [*argv, "--no-critic", "--no-persist", "--raw"]


def capture_cli(argv: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stderr = []
    reader = threading.Thread(target=lambda: stderr.append(proc.stderr.read()))
    reader.start()
    lines, times = [], []
    try:
        for line in proc.stdout:
            lines.append(line)
            times.append(time.perf_counter() - started)
        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        reader.join()
    exited = time.perf_counter() - started
    if code:
        raise RuntimeError(f"CLI exited {code}: {''.join(stderr).strip()}")
    documents = [(i, json.loads(line)) for i, line in enumerate(lines) if line.strip()]
    results = [
        (i, doc) for i, doc in documents if isinstance(doc, dict) and "data" in doc
    ]
    if len(results) != 1 or not isinstance(results[0][1]["data"], list):
        raise ValueError("CLI must return exactly one result envelope")
    index, output = results[0]
    if output.get("persisted") is not False:
        raise ValueError("Raw evaluation requires persistence disabled")
    return {
        "exit_code": 0,
        "data": output["data"],
        "output": output,
        "response_s": times[index],
        "process_exit_s": exited,
        "stderr": "".join(stderr),
        "argv": argv,
    }


def inventory(command: list[str], sensors: dict[str, str]):
    result = subprocess.run(
        [*command, "vios", "list", "--type", "video", "--raw"],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = json.loads(result.stdout)["sensors"]
    ids = [r["sensor_id"] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(sensors):
        raise ValueError(
            "Registered gallery differs from ingestion manifest; do not score stale or foreign sensors"
        )
    return sorted(r["name"] for r in rows)


def latency_summary(records):
    def stats(values):
        return {
            "mean_ms": statistics.mean(values) * 1000,
            "median_ms": statistics.median(values) * 1000,
            "p95_ms": sorted(values)[math.ceil(0.95 * len(values)) - 1] * 1000,
            "p99_ms": sorted(values)[math.ceil(0.99 * len(values)) - 1] * 1000,
        }

    return {
        "retrieval": stats([r["response_s"] for r in records]),
        "routing": stats([r["routing_s"] for r in records]),
        "end_to_end": stats([r["response_s"] + r["routing_s"] for r in records]),
    }


def compact_metrics(summary):
    if "by_top_k" in summary:
        return {
            "by_top_k": {
                k: compact_metrics(value) for k, value in summary["by_top_k"].items()
            }
        }
    return {
        "accuracy": summary["summary"],
        "latency": summary["latency"],
        "routes": summary["routes"],
    }


def validate_reproduction_config(deployment, recorded, modes):
    from vss_cli.search.group import _runtime_from

    if deployment.base_url.rstrip("/") != recorded["origin"]:
        raise ValueError("Configured deployment differs from the reproduction manifest")
    if (
        any(mode in modes for mode in ("attribute-only", "live-routed"))
        and _runtime_from(deployment).frames_index is None
    ):
        raise ValueError(
            "Raw-frame index missing from CLI configuration; finish ingestion and rerun vss configure before benchmarking"
        )


def source_commit():
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()


def append_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(value) + "\n")


def top_k_order(values, index):
    """Balance every position, using six permutations for three cutoffs."""
    orders = []
    for base in (list(values), list(reversed(values))):
        for shift in range(len(base)):
            order = base[shift:] + base[:shift]
            if order not in orders:
                orders.append(order)
    return orders[index % len(orders)]


async def run(args):
    import asyncio
    import uuid

    import vss_cli
    import vss_core
    from flows.decompose import load_prompt
    from flows.eval_observation import GpuSampler, token_summary
    from vss_cli import config

    for module in (vss_cli, vss_core):
        if not Path(module.__file__).resolve().is_relative_to(REPO_ROOT):
            raise ValueError(
                "Install CLI/core from this checkout before running an execution comparison"
            )
    queries, _manifest, sensors, scope = load_inputs(
        args.dataset_root, args.ingestion_manifest, args.query_ids
    )
    ks = (
        [len(sensors)]
        if getattr(args, "full_gallery", False)
        else args.top_k_values or [args.top_k]
    )
    if max(ks) > 1000:
        raise ValueError(
            "Full-gallery evaluation supports at most 1000 registered clips"
        )
    multi = args.top_k_values is not None
    command = [str(Path(sys.executable).with_name("vss"))]
    if not Path(command[0]).is_file():
        raise ValueError(
            "The active Python environment must have the matching vss CLI installed"
        )
    deployment = config.load()
    if getattr(args, "deployment_manifest", None):
        recorded = read(args.deployment_manifest)
        validate_reproduction_config(deployment, recorded, args.modes)
    sources = inventory(command, sensors)
    v22_original = args.live_routing_profile == "v22-original"
    person_state = args.live_routing_profile == "person-state-photo"
    prompt_variant = (
        "person-state" if person_state else "v22" if v22_original else "product"
    )
    llm = (
        LiveDecomposer(
            args.llm_url,
            repo_root=REPO_ROOT,
            model=args.llm_model,
            prompt_variant=prompt_variant,
            max_tokens=32768 if v22_original or person_state else MAX_TOKENS,
        )
        if "live-routed" in args.modes
        else None
    )
    control_ids = None
    if args.compare_cli_query_ids is not None:
        selection = read(args.compare_cli_query_ids)
        if isinstance(selection, dict):
            selection = selection["queries"]
        control_ids = {x if isinstance(x, str) else x["query_id"] for x in selection}
        if not control_ids <= {q["query_id"] for q in queries}:
            raise ValueError("CLI comparison IDs must be in the selected cohort")
    inputs = [
        args.ingestion_manifest,
        args.dataset_root / "gt/queries_gt.json",
        args.dataset_root / "additional_gt/clips_gt.json",
    ]
    if args.query_ids:
        inputs.append(args.query_ids)
    if getattr(args, "deployment_manifest", None):
        inputs.append(args.deployment_manifest)
    run_id = str(uuid.uuid4())
    config_view = (
        deployment.to_json() if isinstance(deployment, config.Deployment) else None
    )
    identity = {
        "run_id": run_id,
        "execution_backend": args.execution_backend,
        "attribute_gallery": "native-index",
        "attribute_frame_pool_size": args.attribute_frame_pool_size,
        "modes": args.modes,
        "top_k_values": ks,
        "multi_k": multi,
        "queries": len(queries),
        "query_ids": [q["query_id"] for q in queries],
        "domains": dict(Counter(q["query_domain"] for q in queries)),
        "gallery": len(sensors),
        "scope": scope,
        "scorer": "raw-clips-v2-minimal",
        "deployment_config_sha256": hashlib.sha256(
            json.dumps(config_view, sort_keys=True).encode()
        ).hexdigest()
        if config_view is not None
        else None,
        "retrieval_settings": {
            "fusion_strategy": args.fusion_strategy,
            "fusion_recipe": None,
            "min_cosine_similarity": args.min_cosine_similarity,
            "attribute_candidate_limit": args.candidate_limit,
            "attribute_max_hits_per_source": args.max_hits_per_source,
            "merge_adjacent": args.merge_adjacent,
        },
        "critic": False,
        "reranker": False,
        "persistence": False,
        "concurrency": 1,
        "embedding_cache_policy": args.embedding_cache_policy
        if args.execution_backend == "sdk"
        else "fresh-process",
        "compare_cli": args.compare_cli,
        "comparison_query_ids": sorted(control_ids)
        if control_ids is not None
        else None,
        "warmup_queries_per_mode": min(args.warmup_queries, len(queries)),
        "llm": llm.describe() if llm else None,
        "live_routing_profile": args.live_routing_profile,
        "live_retrieval_policy": (
            "Original embedding text; fixed photo prefix on original attribute text; model caption and metadata ignored; fixed gallery scope"
            if person_state
            else "Original text unchanged on both applicable legs; extracted metadata ignored; fixed gallery timestamps, video_file scope and requested k"
            if v22_original
            else "Product decomposition text and extracted filters"
        ),
        "source_commit": source_commit(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "versions": {
            "python": sys.version,
            **{
                name: version(name)
                for name in ("elasticsearch", "elastic-transport", "orjson")
            },
        },
        "input_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
        },
        "latency_definition": "routing+plan mapping plus each k request preparation/search/JSON serialization; excludes other k trials and artifact IO",
        "tokens_definition": "actual routing calls counted once; per-k latency shares routing duration, not additional paid calls",
    }
    if llm and v22_original:
        pinned = read(Path(__file__).parent / "flows/v22_prompt.json")
        identity["live_prompt_pin"] = {
            key: pinned[key]
            for key in ("source_commit", "source_path", "template_sha256")
        }
    if llm and person_state:
        pinned = read(Path(__file__).parent / "flows/person_state_prompt.json")
        identity["live_prompt_pin"] = {
            key: pinned[key]
            for key in ("source_path", "template_sha256", "inline_examples")
        }
    save(args.output_dir / "identity.json", identity)
    save(args.output_dir / "queries.json", {"queries": queries})
    if llm:
        template, examples = load_prompt(REPO_ROOT, prompt_variant)
        save(
            args.output_dir / "prompt.json",
            {
                "template": template,
                "few_shot_examples": examples,
                "video_sources": sources,
                "sha256": hashlib.sha256((template + examples).encode()).hexdigest(),
                "source_commit": pinned["source_commit"]
                if v22_original
                else identity["source_commit"],
                "variant": prompt_variant,
            },
        )
    summaries = {}
    route_calls = {"warmup": [], "measured": []}
    events = []
    sampler = GpuSampler(
        args.output_dir, enabled=args.gpu_telemetry, sample_ms=args.gpu_sample_ms
    )
    wall_started = time.perf_counter()
    try:
        async with AsyncExitStack() as stack:
            stack.enter_context(sampler)
            if args.gpu_telemetry and sampler.status == "sampling":
                await asyncio.sleep(args.gpu_baseline_seconds)
            sdk = None
            if args.execution_backend == "sdk":
                from flows.raw_sdk import RawSdkRunner

                backend = RawSdkRunner(
                    deployment=deployment,
                    tuning={"no_merge_adjacent": not args.merge_adjacent},
                    embedding_cache_policy=args.embedding_cache_policy,
                )
                sdk = await stack.enter_async_context(backend)
            save(
                args.output_dir / "startup.json",
                {
                    "python_entry_to_ready_s": time.perf_counter() - _PYTHON_ENTRY,
                    "includes_interpreter_startup": False,
                    "gpu_baseline_seconds": args.gpu_baseline_seconds
                    if args.gpu_telemetry
                    else 0,
                },
            )
            for mode in args.modes:
                records = {k: {} for k in ks}
                mode_routes = []
                entries = [(True, q) for q in queries[: args.warmup_queries]] + [
                    (False, q) for q in queries
                ]
                for index, (warming, query) in enumerate(entries):
                    qid = query["query_id"]
                    if Path(qid).name != qid or qid in {".", ".."}:
                        raise ValueError("Query IDs must be safe filename components")
                    stage = "warmup" if warming else "measured"
                    stage_root = (
                        args.output_dir / "warmup" if warming else args.output_dir
                    )
                    plan, decomposition, routing_s, metadata = {}, None, 0.0, None
                    route_started = time.perf_counter()
                    route_id = f"{run_id}:{stage}:{mode}:{qid}"
                    if mode == "live-routed":
                        try:
                            if hasattr(llm, "decompose_detailed"):
                                trace = llm.decompose_detailed(
                                    query["query"], video_sources=sources
                                )
                                decomposition, metadata = (
                                    trace.decomposition,
                                    trace.metadata,
                                )
                            else:
                                decomposition, _ = llm.decompose(
                                    query["query"], video_sources=sources
                                )
                            plan = live_routing_plan(
                                query["query"], decomposition, args.live_routing_profile
                            )
                        except BaseException as error:
                            failed = {
                                "query_id": qid,
                                "route_id": route_id,
                                "status": "failed",
                                "error": str(error),
                                "llm_metadata": None,
                                "llm_response": getattr(error, "response", None),
                            }
                            route_calls[stage].append(failed)
                            save(stage_root / "routing" / f"{qid}.failed.json", failed)
                            raise
                        route_ended = time.perf_counter()
                        routing_s = route_ended - route_started
                        route_record = {
                            "query_id": qid,
                            "query": query["query"],
                            "domain": query["query_domain"],
                            "route_id": route_id,
                            "chosen_route": plan["path"],
                            "decomposition": decomposition,
                            "plan": plan,
                            "elapsed_s": routing_s,
                            "llm_metadata": metadata,
                            "stage": stage,
                            "start_monotonic_s": route_started,
                            "end_monotonic_s": route_ended,
                        }
                        route_calls[stage].append(route_record)
                        if not warming:
                            mode_routes.append(route_record)
                        save(stage_root / "routing" / f"{qid}.json", route_record)
                        append_json(stage_root / "routes.jsonl", route_record)
                        event = {
                            "phase": "routing",
                            "stage": stage,
                            "mode": mode,
                            "query_id": qid,
                            "start": route_started,
                            "end": route_ended,
                        }
                        events.append(event)
                        append_json(args.output_dir / "phase-events.jsonl", event)
                    measured_index = (
                        index - min(args.warmup_queries, len(queries))
                        if not warming
                        else index
                    )
                    order = top_k_order(ks, measured_index)
                    for k in order:
                        request_args = argparse.Namespace(**(vars(args) | {"top_k": k}))
                        compare = (
                            args.compare_cli
                            and not warming
                            and (control_ids is None or qid in control_ids)
                        )
                        baseline = None
                        if compare and measured_index % 2 == 0:
                            path, payload = build_request(
                                query["query"], mode, plan, scope, request_args
                            )
                            baseline = capture_cli(
                                cli_argv(command, path, payload, args.merge_adjacent)
                            )
                        started = time.perf_counter()
                        path, payload = build_request(
                            query["query"], mode, plan, scope, request_args
                        )
                        prepare_s = time.perf_counter() - started
                        if sdk:
                            output = await sdk.search(path, payload)
                            serialized = json.dumps(output)
                            record = {
                                "exit_code": 0,
                                "data": output["data"],
                                "output": output,
                                "response_s": time.perf_counter() - started,
                                "response_bytes": len(serialized.encode()),
                            }
                        else:
                            record = capture_cli(
                                cli_argv(command, path, payload, args.merge_adjacent)
                            )
                            record["response_s"] += prepare_s
                        ended = time.perf_counter()
                        record.update(
                            query_id=qid,
                            path=path,
                            payload=payload,
                            routing_s=routing_s,
                            route_id=route_id if mode == "live-routed" else None,
                            k=k,
                            stage=stage,
                            execution_backend=args.execution_backend,
                            request_preparation_s=prepare_s,
                            request_start_monotonic_s=started,
                            response_end_monotonic_s=ended,
                            observed_route_start_to_response_s=ended - route_started,
                        )
                        score = clip_metrics.score_query(
                            query, record["data"], sensors, k=k
                        )
                        record["score"] = score
                        suffix = Path(f"top-k-{k}") if multi else Path()
                        save(
                            stage_root / "captures" / mode / suffix / f"{qid}.json",
                            record,
                        )
                        event = {
                            "phase": "retrieval",
                            "stage": stage,
                            "mode": mode,
                            "query_id": qid,
                            "k": k,
                            "route": path,
                            "start": started,
                            "end": ended,
                        }
                        events.append(event)
                        append_json(args.output_dir / "phase-events.jsonl", event)
                        if compare:
                            baseline = (
                                baseline
                                if baseline is not None
                                else capture_cli(
                                    cli_argv(
                                        command, path, payload, args.merge_adjacent
                                    )
                                )
                            )
                            baseline["routing_s"] = routing_s
                            save(
                                args.output_dir
                                / "comparison-cli"
                                / mode
                                / suffix
                                / f"{qid}.json",
                                baseline,
                            )
                            if record["data"] != baseline["data"]:
                                raise ValueError(
                                    f"SDK/CLI result mismatch: {mode}/{qid}/k={k}; captures retained"
                                )
                        if not warming:
                            records[k][qid] = record
                        save(
                            args.output_dir / "progress.json",
                            {
                                "stage": stage,
                                "mode": mode,
                                "completed_queries_in_mode": measured_index + 1
                                if not warming
                                else 0,
                                "queries_per_mode": len(queries),
                                "query_id": qid,
                                "k": k,
                                "order": order,
                                "completed_measured_searches_in_mode": sum(
                                    len(v) for v in records.values()
                                ),
                            },
                        )
                        print(
                            f"{stage} {mode} {measured_index + 1}/{len(queries)} {qid} k={k} {path} {record['response_s']:.3f}s",
                            flush=True,
                        )
                by_k = {}
                for k in ks:
                    summary = clip_metrics.summarize(queries, records[k], sensors, k=k)
                    rs = list(records[k].values())
                    summary["latency"] = latency_summary(rs)
                    summary["latency_by_domain"] = {
                        domain: latency_summary(
                            [
                                records[k][q["query_id"]]
                                for q in queries
                                if q["query_domain"] == domain
                            ]
                        )
                        for domain in ("event", "pas")
                        if any(q["query_domain"] == domain for q in queries)
                    }
                    summary["latency_by_selected_route"] = {
                        path: latency_summary([r for r in rs if r["path"] == path])
                        for path in {r["path"] for r in rs}
                    }
                    summary["routes"] = dict(Counter(r["path"] for r in rs))
                    if args.compare_cli:
                        control_paths = list(
                            (
                                args.output_dir
                                / "comparison-cli"
                                / mode
                                / (Path(f"top-k-{k}") if multi else Path())
                            ).glob("*.json")
                        )
                        controls = [read(p) for p in control_paths]
                        summary["comparison_cli_latency"] = latency_summary(controls)
                        summary["all_cli_data_identical"] = True
                        summary["cli_comparison_queries"] = len(controls)
                    by_k[str(k)] = summary
                summary = {"by_top_k": by_k} if multi else by_k[str(ks[0])]
                if mode == "live-routed":
                    summary["token_usage_actual_routing_calls"] = token_summary(
                        mode_routes
                    )
                summaries[mode] = summary
                save(args.output_dir / "summary.json", summaries)
        inventory(command, sensors)
        for p in inputs:
            if (
                hashlib.sha256(p.read_bytes()).hexdigest()
                != identity["input_sha256"][str(p)]
            ):
                raise ValueError(f"Input changed during evaluation: {p}")
        windows = [(e["start"], e["end"]) for e in events if e["stage"] == "measured"]
        save(
            args.output_dir / "gpu-summary.json",
            {
                "metadata": sampler.metadata(),
                "measured_windows": sampler.window_summary(windows),
                "retrieval_windows_by_k": {
                    str(k): sampler.window_summary(
                        [
                            (e["start"], e["end"])
                            for e in events
                            if e["stage"] == "measured"
                            and e["phase"] == "retrieval"
                            and e["k"] == k
                        ]
                    )
                    for k in ks
                },
            },
        )
        save(
            args.output_dir / "metrics.json",
            {mode: compact_metrics(summary) for mode, summary in summaries.items()},
        )
        save(
            args.output_dir / "complete.json",
            {
                "queries_per_mode": len(queries),
                "modes": args.modes,
                "top_k_values": ks,
                "execution_backend": args.execution_backend,
                "registered_gallery_unchanged": True,
                "all_modes_complete": True,
                "measured_retrieval_calls": len(queries) * len(ks) * len(args.modes),
                "actual_measured_routing_calls": len(route_calls["measured"]),
                "campaign_wall_s": time.perf_counter() - wall_started,
            },
        )
    finally:
        save(
            args.output_dir / "token-usage.json",
            {stage: token_summary(rows) for stage, rows in route_calls.items()},
        )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset-root", type=Path, required=True)
    p.add_argument("--ingestion-manifest", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument(
        "--deployment-manifest",
        type=Path,
        help="Prepared reproduction manifest supplying the declared LLM URL/model; configured origin must match",
    )
    p.add_argument("--execution-backend", choices=("cli", "sdk"), default="cli")
    p.add_argument("--modes", choices=MODES, nargs="+", default=list(MODES))
    p.add_argument(
        "--fusion-strategy",
        # Keep this limited to implementations shipped with this commit.
        choices=("native",),
        default="native",
        help="native VSS fusion behavior",
    )
    p.add_argument(
        "--query-ids",
        type=Path,
        help="JSON ID list or cohort JSON; only IDs are read, never routes or GT overrides",
    )
    p.add_argument(
        "--llm-url",
        help="Explicit existing LLM URL; required for live-routed, never inferred",
    )
    p.add_argument("--llm-model", default="nvidia/nemotron-3.5-lightning-30b-a3b")
    p.add_argument(
        "--live-routing-profile",
        # Experimental profiles with uncommitted runtime modules stay unavailable.
        choices=("product", "v22-original", "person-state-photo"),
        default="product",
        help="v22-original: route then use original text; person-state-photo: person-image/scene expert selection with a fixed photo prefix on attribute text; both pin prompts and gallery scope, product keeps existing behavior",
    )
    depths = p.add_mutually_exclusive_group()
    depths.add_argument("--top-k", type=int, default=10)
    depths.add_argument("--top-k-values", type=int, nargs="+")
    depths.add_argument(
        "--full-gallery",
        action="store_true",
        help="Retrieve and score every registered gallery clip (maximum 1000)",
    )
    p.add_argument("--warmup-queries", type=int, default=0)
    p.add_argument("--gpu-telemetry", action="store_true")
    p.add_argument("--gpu-sample-ms", type=int, default=200)
    p.add_argument("--gpu-baseline-seconds", type=float, default=3.0)
    p.add_argument("--compare-cli-query-ids", type=Path)
    p.add_argument("--min-cosine-similarity", type=float, default=-1.0)
    p.add_argument("--candidate-limit", type=int, default=100000)
    p.add_argument(
        "--attribute-frame-pool-size", type=int, choices=range(1, 33), default=1
    )
    p.add_argument("--max-hits-per-source", type=int, default=1)
    p.add_argument("--merge-adjacent", action="store_true")
    p.add_argument(
        "--embedding-cache-policy",
        choices=("query", "native"),
        default="query",
        help="SDK query policy clears cross-query embeddings for CLI-comparable latency; native retains SDK caches",
    )
    p.add_argument(
        "--compare-cli",
        action="store_true",
        help="SDK acceptance check: same fresh LLM plan, alternating paired CLI controls",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print the plan without any backend calls or output files",
    )
    p.add_argument(
        "--no-report",
        action="store_true",
        help="Do not write report.html into the output directory after a completed run",
    )
    args = p.parse_args(argv)
    if args.deployment_manifest:
        recorded = read(args.deployment_manifest)
        if args.llm_url and args.llm_url != recorded["llm_url"]:
            p.error("--llm-url differs from --deployment-manifest")
        if args.llm_model != recorded["llm_model"]:
            p.error("--llm-model differs from --deployment-manifest")
        args.llm_url = recorded["llm_url"]
    ks = args.top_k_values or [args.top_k]
    if any(k < 1 or k > 1000 for k in ks) or len(set(ks)) != len(ks):
        p.error("top-k values must be unique integers between 1 and 1000")
    if args.candidate_limit < 1 or args.max_hits_per_source < 1:
        p.error("candidate/source limits must be positive")
    if (
        args.warmup_queries < 0
        or args.gpu_sample_ms < 100
        or not math.isfinite(args.gpu_baseline_seconds)
        or args.gpu_baseline_seconds < 0
    ):
        p.error("invalid warmup count or GPU sampling/baseline interval")
    if args.compare_cli_query_ids and not args.compare_cli:
        p.error("--compare-cli-query-ids requires --compare-cli")
    if (
        not math.isfinite(args.min_cosine_similarity)
        or not -1 <= args.min_cosine_similarity <= 1
    ):
        p.error("min-cosine-similarity must be finite and between -1 and 1")
    if len(set(args.modes)) != len(args.modes):
        p.error("modes must not repeat")
    if "live-routed" in args.modes and not args.llm_url:
        p.error("live-routed requires --llm-url")
    if args.compare_cli and args.execution_backend != "sdk":
        p.error("--compare-cli requires --execution-backend sdk")
    return args


def main():
    import asyncio

    args = parse_args()
    if args.dry_run:
        queries, _manifest, sensors, scope = load_inputs(
            args.dataset_root, args.ingestion_manifest, args.query_ids
        )
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "execution_backend": args.execution_backend,
                    "attribute_gallery": "native-index",
                    "top_k_values": [len(sensors)]
                    if args.full_gallery
                    else args.top_k_values or [args.top_k],
                    "queries": len(queries),
                    "domains": dict(Counter(q["query_domain"] for q in queries)),
                    "gallery": len(sensors),
                    "modes": args.modes,
                    "live_routing_profile": args.live_routing_profile,
                    "fusion_strategy": args.fusion_strategy,
                    "scope": scope,
                    "critic": False,
                    "reranker": False,
                    "persistence": False,
                    "concurrency": 1,
                },
                indent=2,
            )
        )
        return
    args.output_dir.mkdir(parents=True, exist_ok=False)
    try:
        asyncio.run(run(args))
    except BaseException as error:
        save(
            args.output_dir / "failed.json",
            {"type": type(error).__name__, "message": str(error), "complete": False},
        )
        raise
    if not args.no_report:
        print(f"Wrote {report.write_report(args.output_dir)}")


if __name__ == "__main__":
    main()
