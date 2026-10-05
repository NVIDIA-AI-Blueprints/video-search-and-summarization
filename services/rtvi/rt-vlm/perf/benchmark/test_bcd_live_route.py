# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import ast
import json
import os
from pathlib import Path
import subprocess
import unittest

from bcd_live_route import validate_bcd_live_inputs

HERE = Path(__file__).parent


class BCDRouteTest(unittest.TestCase):
    def config(self, **video):
        return {"global": {"required_live_route": "vst_live"},
                "test_scenarios": {"live": {"benchmark_mode": "max_live_streams",
                                            "videos": [video]}}}

    def test_every_rtsp_source_form_rejects_direct_input(self):
        for field, value in (("rtsp_url", "rtsp://host:32200/nvstream/video"),
                             ("rtsp_urls", ["rtsp://host:30556/live/id", "rtsp://host/direct"]),
                             ("rtsp_url_template", "rtsp://host:32200/nvstream/{stream_num}")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_bcd_live_inputs(self.config(**{field: value}), "renamed.yaml")

    def test_canonical_routes_accepted_and_file_inputs_unchanged(self):
        config = self.config(rtsp_url="rtsp://host:30556/live/source-id")
        config["test_scenarios"]["file"] = {
            "benchmark_mode": "file_burst", "videos": [{"video_path": "/frozen/video.mp4"}]}
        validate_bcd_live_inputs(config, "renamed.yaml")

    def test_bcd_filename_cannot_bypass_guard_by_removing_marker(self):
        config = self.config(rtsp_url="rtsp://host/direct")
        config["global"] = {}
        with self.assertRaises(ValueError):
            validate_bcd_live_inputs(config, "rtvi_vlm_bcd_3_3_h100_sxm_config.yaml")

    def test_non_bcd_layer_isolation_remains_explicitly_separate(self):
        config = self.config(rtsp_url="rtsp://host/direct")
        config["global"] = {}
        validate_bcd_live_inputs(config, "layer-isolation.yaml")

    def test_path_lookalikes_and_missing_sources_rejected(self):
        for url in ("RTSP_STREAM_URL", "rtsp://host/live/", "rtsp://host/nvstream/live/id",
                    "rtsp://host/live/id?next=/nvstream/foo", "rtsp://host/live/id/extra"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_bcd_live_inputs(self.config(rtsp_url=url), "bcd.yaml")

    def test_url_file_rejects_direct_stream(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            pool = Path(directory) / "urls.txt"
            pool.write_text("rtsp://host:30556/live/id\nrtsp://host/direct\n")
            with self.assertRaises(ValueError):
                validate_bcd_live_inputs(self.config(rtsp_urls_file=str(pool)), "bcd.yaml")

    def test_actual_cli_checks_selected_inputs_before_gpu_or_execution(self):
        tree = ast.parse((HERE / "rtvi_perf_benchmark.py").read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "validate_bcd_live_inputs"]
        self.assertTrue(calls)
        gpu = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == "log_gpu_information"]
        self.assertLess(calls[0].lineno, min(gpu))


class SetupSelectionTest(unittest.TestCase):
    def select(self, urls, source_id=""):
        source = (HERE.parent / "setup_perf_env.sh").read_text()
        block = source[source.index('RTSP_URLS=""\nELAPSED=0'):]
        block = block[:block.index('\n# ---------------------------------------------------------------------------')]
        prelude = '''set -Eeuo pipefail
log() { :; }; warn() { :; }; die() { exit 71; }; section() { :; }
paste() { /usr/bin/paste "$@" -; }
_C_CYAN=; _C_RESET=
curl() { printf '%s' "$FIXTURE"; }; sleep() { :; }
VST_STREAMS_API=fixture; STREAM_POLL_TIMEOUT=0; HOST_IP=host; NVSTREAMER_RTSP_PORT=31554
'''
        return subprocess.run(["bash", "-c", prelude + block], capture_output=True,
                              env=dict(os.environ, FIXTURE=json.dumps([{"url": u} for u in urls]),
                                       BCD_VST_SOURCE_ID=source_id))

    def test_direct_fallbacks_fail(self):
        for urls in (["rtsp://host:31554/warehouse_gopro_60m_10fps"],
                     ["rtsp://host:30556/live/other"]):
            with self.subTest(urls=urls):
                self.assertNotEqual(self.select(urls).returncode, 0)

    def test_vst_named_source_passes(self):
        self.assertEqual(self.select(["rtsp://host:30556/live/warehouse_gopro_60m_10fps"]).returncode, 0)

    def test_opaque_vst_source_id_selects_exact_issued_url(self):
        self.assertEqual(self.select(["rtsp://host:30556/live/abc-123"], "abc-123").returncode, 0)
        self.assertNotEqual(self.select(["rtsp://host:30556/live/abc-123"], "other").returncode, 0)

    def test_multiple_named_sources_or_fake_live_path_refused(self):
        for urls in (["rtsp://host:30556/live/warehouse_gopro_60m_10fps-a",
                      "rtsp://host:30556/live/warehouse_gopro_60m_10fps-b"],
                     ["rtsp://host/nvstream/live/warehouse_gopro_60m_10fps"]):
            with self.subTest(urls=urls):
                self.assertNotEqual(self.select(urls).returncode, 0)


if __name__ == "__main__":
    unittest.main()
