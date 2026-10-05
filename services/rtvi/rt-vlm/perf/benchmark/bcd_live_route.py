# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reject non-VST BCD live inputs before measurement.

This syntax guard does not prove VST ownership, issued-source identity, fresh
frames, or clock acceptance. Those remain mandatory runtime preflight gates.
"""
from pathlib import Path
import re
from urllib.parse import urlsplit


def validate_bcd_live_inputs(config, config_path):
    global_config = config.get("global", {})
    policy = global_config.get("required_live_route")
    is_bcd = (policy is not None or "bcd" in Path(config_path).name.lower()
              or "bcd" in str(global_config.get("output_dir", "")).lower())
    if not is_bcd:
        return
    if policy not in (None, "vst_live"):
        raise ValueError("BCD required_live_route must be vst_live")
    for name, scenario in config.get("test_scenarios", {}).items():
        for video in scenario.get("videos", []):
            urls = []
            # Match the live benchmarks' source selection order; unused fields
            # may still contain setup placeholders or a different source mode.
            if (scenario.get("benchmark_mode") == "single_live_stream"
                    or not video.get("unique_rtsp_url_per_stream", True)):
                if "rtsp_url" in video:
                    urls = [video["rtsp_url"]]
            elif video.get("rtsp_urls"):
                if not isinstance(video["rtsp_urls"], list):
                    raise ValueError("BCD rtsp_urls must be a non-empty list")
                urls = video["rtsp_urls"]
            elif video.get("rtsp_urls_file"):
                urls.extend(line.strip() for line in Path(video["rtsp_urls_file"]).read_text().splitlines()
                            if line.strip())
            elif video.get("rtsp_url_template"):
                raise ValueError("BCD live inputs require explicit VST-issued URLs, not templates")
            if "live" in scenario.get("benchmark_mode", "") and not urls:
                raise ValueError(f"BCD live scenario {name} has no VST source URLs")
            for url in urls:
                try:
                    parsed = urlsplit(url)
                    valid = (parsed.scheme == "rtsp" and parsed.hostname and parsed.port
                             and not parsed.username and not parsed.password
                             and not parsed.query and not parsed.fragment
                             and re.fullmatch(r"/live/[A-Za-z0-9_-]+", parsed.path))
                except (TypeError, ValueError, AttributeError):
                    valid = False
                if not valid:
                    # Never echo a possibly credential-bearing URL in an error.
                    raise ValueError(f"BCD scenario {name} requires canonical VST /live/<source-id> URLs")
