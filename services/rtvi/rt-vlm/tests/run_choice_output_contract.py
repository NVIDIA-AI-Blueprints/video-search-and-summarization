#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed device test for RT-VLM finite-choice output constraints."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

COMPACT_CHOICES = [
    "N",
    "Y collision_happening",
    "Y collision_aftermath stalled_vehicle blocks_active_lane",
]
JSON_CHOICES = [
    '{"normal":true,"collision_happening":false,"collision_aftermath":false}',
    '{"normal":false,"collision_happening":true,"collision_aftermath":false}',
    '{"normal":false,"collision_happening":false,"collision_aftermath":true}',
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        default=os.environ.get("RTVI_BACKEND", "http://localhost:8000"),
    )
    parser.add_argument(
        "--video-path", required=True, help="Use a short device-local test clip"
    )
    parser.add_argument(
        "--model", help="Defaults to the first model returned by /v1/models"
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--chunk-duration", type=int, default=10)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def gpu_identity():
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,uuid,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.SubprocessError):
        return []


def discover_model(session, backend, timeout):
    response = session.get(f"{backend}/v1/models", timeout=timeout)
    response.raise_for_status()
    models = response.json().get("data", [])
    if not models:
        raise RuntimeError("/v1/models returned no models")
    return models[0]["id"]


def upload_video(session, backend, video_path, timeout):
    with open(video_path, "rb") as video:
        response = session.post(
            f"{backend}/v1/files",
            data={"purpose": "vision", "media_type": "video"},
            files={"file": (os.path.basename(video_path), video)},
            timeout=timeout,
        )
    response.raise_for_status()
    payload = response.json()
    file_id = payload.get("id")
    if not file_id:
        results = payload.get("results", [])
        file_id = results[0].get("id") if results else None
    if not file_id:
        raise RuntimeError(f"file upload returned no ID: {payload}")
    return file_id


def extract_contents(payload):
    chunks = payload.get("chunk_responses", [])
    if not chunks:
        raise AssertionError(f"response has no chunk_responses: {payload}")
    contents = [chunk.get("content") for chunk in chunks]
    if any(not isinstance(content, str) or not content for content in contents):
        raise AssertionError(f"response has an empty chunk content: {payload}")
    return [content.strip() for content in contents]


def generation_payload(model, file_id, prompt, response_format, chunk_duration):
    return {
        "id": file_id,
        "model": model,
        "prompt": prompt,
        "stream": False,
        "temperature": 0,
        "max_tokens": 64,
        "chunk_duration": chunk_duration,
        "response_format": response_format,
    }


def run_generation_case(
    session,
    backend,
    timeout,
    name,
    payload,
    validate,
):
    started = time.monotonic()
    record = {"name": name}
    try:
        response = session.post(
            f"{backend}/v1/generate_captions",
            json=payload,
            timeout=timeout,
        )
        record["status_code"] = response.status_code
        response.raise_for_status()
        contents = extract_contents(response.json())
        validate(contents)
        record.update(status="pass", outputs=contents)
    except (
        AssertionError,
        KeyError,
        TypeError,
        ValueError,
        requests.RequestException,
    ) as exc:
        record.update(status="fail", error=str(exc))
    record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return record


def run_rejection_case(session, backend, timeout, name, payload):
    started = time.monotonic()
    record = {"name": name}
    try:
        response = session.post(
            f"{backend}/v1/generate_captions",
            json=payload,
            timeout=timeout,
        )
        record["status_code"] = response.status_code
        if response.status_code != 422:
            raise AssertionError(
                f"expected HTTP 422, got {response.status_code}: {response.text[:500]}"
            )
        record["status"] = "pass"
    except (AssertionError, requests.RequestException) as exc:
        record.update(status="fail", error=str(exc))
    record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return record


def require_exact(expected):
    def validate(contents):
        unexpected = [content for content in contents if content != expected]
        if unexpected:
            raise AssertionError(f"expected {expected!r}, got {unexpected!r}")

    return validate


def require_member(allowed):
    def validate(contents):
        unexpected = [content for content in contents if content not in allowed]
        if unexpected:
            raise AssertionError(f"outputs are outside allowed choices: {unexpected!r}")

    return validate


def require_json_member(allowed):
    def validate(contents):
        unexpected = [content for content in contents if content not in allowed]
        if unexpected:
            raise AssertionError(
                f"outputs are outside allowed JSON choices: {unexpected!r}"
            )
        if any(not isinstance(json.loads(content), dict) for content in contents):
            raise ValueError("JSON choice did not decode to an object")

    return validate


def require_json(contents):
    if any(not isinstance(json.loads(content), dict) for content in contents):
        raise ValueError("json_object output did not decode to an object")


def main():
    args = parse_args()
    backend = args.backend.rstrip("/")
    video_path = os.path.abspath(args.video_path)
    if not os.path.isfile(video_path):
        raise SystemExit(f"video does not exist: {video_path}")
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be at least 1")

    report_path = args.report or Path(
        f"rtvi-choice-contract-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "backend": backend,
        "video_path": video_path,
        "gpu": gpu_identity(),
        "cases": [],
    }

    session = requests.Session()
    file_id = None
    try:
        model = args.model or discover_model(session, backend, args.timeout)
        report["model"] = model
        file_id = upload_video(session, backend, video_path, args.timeout)
        base = generation_payload(
            model,
            file_id,
            "Return exactly one allowed classification.",
            {"type": "choice", "choices": COMPACT_CHOICES},
            args.chunk_duration,
        )

        invalid_formats = [
            ("reject_missing_choices", {"type": "choice"}),
            ("reject_empty_choices", {"type": "choice", "choices": []}),
            ("reject_blank_choice", {"type": "choice", "choices": [" "]}),
            ("reject_duplicate_choices", {"type": "choice", "choices": ["N", "N"]}),
            (
                "reject_too_many_choices",
                {"type": "choice", "choices": [str(i) for i in range(257)]},
            ),
            ("reject_overlong_choice", {"type": "choice", "choices": ["x" * 2049]}),
            ("reject_choices_on_text", {"type": "text", "choices": ["N"]}),
        ]
        for name, response_format in invalid_formats:
            payload = dict(base, response_format=response_format)
            report["cases"].append(
                run_rejection_case(session, backend, args.timeout, name, payload)
            )

        for expected in COMPACT_CHOICES:
            payload = dict(
                base, response_format={"type": "choice", "choices": [expected]}
            )
            report["cases"].append(
                run_generation_case(
                    session,
                    backend,
                    args.timeout,
                    f"single_choice_{expected.replace(' ', '_')}",
                    payload,
                    require_exact(expected),
                )
            )

        for repetition in range(args.repetitions):
            report["cases"].append(
                run_generation_case(
                    session,
                    backend,
                    args.timeout,
                    f"compact_choice_repeat_{repetition + 1}",
                    base,
                    require_member(COMPACT_CHOICES),
                )
            )

        json_payload = dict(
            base,
            prompt="Return exactly one allowed JSON traffic state.",
            response_format={"type": "choice", "choices": JSON_CHOICES},
        )
        report["cases"].append(
            run_generation_case(
                session,
                backend,
                args.timeout,
                "protocol_constrained_json",
                json_payload,
                require_json_member(JSON_CHOICES),
            )
        )

        legacy_payload = dict(
            base,
            prompt='Return a JSON object with one key named "status".',
            response_format={"type": "json_object"},
        )
        report["cases"].append(
            run_generation_case(
                session,
                backend,
                args.timeout,
                "legacy_json_object_regression",
                legacy_payload,
                require_json,
            )
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        ValueError,
        requests.RequestException,
    ) as exc:
        report["setup_error"] = str(exc)
    finally:
        if file_id:
            try:
                session.delete(f"{backend}/v1/files/delete/{file_id}", timeout=30)
            except requests.RequestException as exc:
                report["cleanup_error"] = str(exc)
        session.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["passed"] = (
        bool(report["cases"])
        and "setup_error" not in report
        and "cleanup_error" not in report
        and all(case["status"] == "pass" for case in report["cases"])
    )
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"report: {report_path.resolve()}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
