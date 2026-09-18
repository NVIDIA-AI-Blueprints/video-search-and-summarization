# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import threading
from concurrent.futures import ThreadPoolExecutor

from utils.request_profiler import (
    GPUSample,
    ProcessGPUSampler,
    ProfileExportJob,
    RequestProfileExporter,
)


def test_process_gpu_sampler_starts_only_one_thread(monkeypatch):
    sampler = ProcessGPUSampler(interval_seconds=60)
    monkeypatch.setattr(sampler, "_initialize_nvml", lambda: None)

    sampler.start()
    first_thread = sampler._thread
    sampler.start()

    assert sampler._thread is first_thread
    sampler.stop()


def test_profile_export_queue_never_blocks_c64_completions(monkeypatch, tmp_path):
    release_export = threading.Event()
    export_started = threading.Event()
    exporter = RequestProfileExporter(max_pending_exports=1)

    def blocking_export(_job):
        export_started.set()
        release_export.wait(timeout=5)

    monkeypatch.setattr(exporter, "_export", blocking_export)
    job = ProfileExportJob(
        request_id="request",
        sample_start_time=0.0,
        sample_end_time=1.0,
        metrics={},
        output_dir=str(tmp_path),
    )

    try:
        assert exporter.submit(job) is True
        assert export_started.wait(timeout=1)
        with ThreadPoolExecutor(max_workers=64) as callers:
            submissions = list(callers.map(lambda _: exporter.submit(job), range(64)))
        assert submissions == [False] * 64
    finally:
        release_export.set()
        exporter.shutdown()


def test_profile_exporter_uses_one_background_worker():
    exporter = RequestProfileExporter(max_pending_exports=4)
    try:
        assert exporter.max_workers == 1
    finally:
        exporter.shutdown()


def test_profile_export_runs_off_thread_and_writes_request_window(monkeypatch, tmp_path):
    sampler = ProcessGPUSampler()
    capture_id = sampler.begin_capture()
    sampler._captures[capture_id].extend(
        (
            GPUSample(10.0, (5.0,), (20.0,), (30.0,)),
            GPUSample(11.0, (6.0,), (21.0,), (31.0,)),
            GPUSample(12.0, (7.0,), (22.0,), (32.0,)),
        )
    )
    exporter = RequestProfileExporter(sampler=sampler)
    export_thread_names = []
    monkeypatch.setattr(
        "utils.request_profiler._write_plots",
        lambda *_args: export_thread_names.append(threading.current_thread().name),
    )
    job = ProfileExportJob(
        request_id="window",
        sample_start_time=10.5,
        sample_end_time=11.5,
        metrics={"e2e_latency": 1.0},
        output_dir=str(tmp_path),
        capture_id=capture_id,
    )

    assert exporter.submit(job) is True
    exporter.shutdown()

    assert export_thread_names == ["rtvi-profile-export_0"]
    assert (tmp_path / "request_metrics_window.json").is_file()
    assert (tmp_path / "nvdec_usage_window.csv").read_text().splitlines() == [
        "elapsed_time,GPU0_AvgNVDEC",
        "0.50,6.0",
    ]


def test_queued_export_keeps_samples_frozen_at_submission(monkeypatch, tmp_path):
    sampler = ProcessGPUSampler()
    blocker_capture = sampler.begin_capture()
    queued_capture = sampler.begin_capture()
    sample = GPUSample(11.0, (6.0,), (21.0,), (31.0,))
    sampler._captures[blocker_capture].append(sample)
    sampler._captures[queued_capture].append(sample)
    exporter = RequestProfileExporter(sampler=sampler, max_pending_exports=2)
    release_first_export = threading.Event()
    first_export_started = threading.Event()
    original_export = exporter._export

    def gated_export(job):
        if job.request_id == "blocker":
            first_export_started.set()
            release_first_export.wait(timeout=5)
            return
        original_export(job)

    monkeypatch.setattr(exporter, "_export", gated_export)
    blocker = ProfileExportJob(
        "blocker", 10.0, 12.0, {}, str(tmp_path), capture_id=blocker_capture
    )
    queued = ProfileExportJob(
        "queued", 10.0, 12.0, {}, str(tmp_path), capture_id=queued_capture
    )

    try:
        assert exporter.submit(blocker) is True
        assert first_export_started.wait(timeout=1)
        assert exporter.submit(queued) is True
        assert sampler._captures == {}
    finally:
        release_first_export.set()
        exporter.shutdown()

    assert (tmp_path / "nvdec_usage_queued.csv").read_text().splitlines() == [
        "elapsed_time,GPU0_AvgNVDEC",
        "1.00,6.0",
    ]
