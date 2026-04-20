#!/usr/bin/env python3
"""Tests for the meaningful-response validator in test_base.py.

Motivated by a real finding: vss-eval-h100-v2's spark-shared trial returned
a 389-char "502 Bad Gateway" nginx error body as the WebSocket response,
and the previous naive non-empty check accepted it as a pass.

Run manually:
    python3 -m pytest skills/deploy/scripts/tests/test_response_validator.py -v
Or directly:
    python3 skills/deploy/scripts/tests/test_response_validator.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from test_base import is_meaningful_response, looks_like_http_error  # noqa: E402


class LooksLikeHttpError(unittest.TestCase):

    def test_nginx_502_html(self):
        """Real sample from the spark-shared trial."""
        body = (
            "<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n"
            "<body>\r\n<center><h1>502 Bad Gateway</h1></center>\r\n"
            "<hr><center>nginx/1.25.3</center>\r\n</body>\r\n</html>\r\n"
        )
        self.assertTrue(looks_like_http_error(body))

    def test_503_service_unavailable(self):
        self.assertTrue(looks_like_http_error("503 Service Unavailable"))
        self.assertTrue(looks_like_http_error(
            "<html><head><title>503 Service Unavailable</title></head></html>"
        ))

    def test_504_gateway_timeout(self):
        self.assertTrue(looks_like_http_error(
            "<html><body>504 Gateway Timeout</body></html>"
        ))
        self.assertTrue(looks_like_http_error("504 Gateway Time-out"))

    def test_cloudflare_error(self):
        self.assertTrue(looks_like_http_error(
            "Cloudflare error 520: Web server returned unknown error"
        ))

    def test_nginx_error_text(self):
        self.assertTrue(looks_like_http_error(
            "nginx: upstream connection unavailable"
        ))

    def test_empty_is_error(self):
        """Empty / whitespace-only counts as error."""
        self.assertTrue(looks_like_http_error(""))
        self.assertTrue(looks_like_http_error("   \n\t  "))

    def test_short_noise_is_error(self):
        """Anything below the 20-char floor is error."""
        self.assertTrue(looks_like_http_error("hi"))
        self.assertTrue(looks_like_http_error("ok"))

    def test_real_agent_response_passes(self):
        """Typical WebSocket response from a healthy agent."""
        msg = (
            "I analyzed the warehouse video. Here's what I observed:\n"
            "- A forklift operator moving pallets across the warehouse floor\n"
            "- Multiple workers wearing safety vests\n"
            "- Several stacked crates in the background\n"
            "Let me know if you'd like more detail on any aspect."
        )
        self.assertFalse(looks_like_http_error(msg))

    def test_agent_report_passes(self):
        """A report-style response with formatting."""
        msg = (
            "# Video Report: warehouse_forklift\n\n"
            "[0.0s-4.0s] Forklift enters the frame from the left.\n"
            "[4.0s-8.0s] Operator lifts pallet of boxes.\n"
            "[8.0s-15.0s] Forklift moves to the storage area.\n"
        )
        self.assertFalse(looks_like_http_error(msg))


class IsMeaningfulResponse(unittest.TestCase):

    def test_real_response_is_meaningful(self):
        ok, reason = is_meaningful_response(
            "The video shows a forklift operator moving pallets across a "
            "warehouse. Safety vests are worn by workers in the background.",
            "Generate a report for video warehouse_x",
        )
        self.assertTrue(ok, reason)

    def test_502_not_meaningful(self):
        body = (
            "<html><head><title>502 Bad Gateway</title></head>"
            "<body><center><h1>502 Bad Gateway</h1></center>"
            "<hr><center>nginx/1.25.3</center></body></html>"
        )
        ok, reason = is_meaningful_response(body, "What videos are available?")
        self.assertFalse(ok)
        self.assertIn("error", reason.lower())

    def test_empty_not_meaningful(self):
        ok, reason = is_meaningful_response("", "q")
        self.assertFalse(ok)
        self.assertIn("empty", reason.lower())

    def test_too_short_not_meaningful(self):
        ok, reason = is_meaningful_response("ok", "q")
        self.assertFalse(ok)
        self.assertIn("short", reason.lower())

    def test_generic_refusal_not_meaningful(self):
        # Long enough to pass the min-50 check, short enough for the
        # refusal heuristic to fire (< 200 chars).
        ok, reason = is_meaningful_response(
            "I cannot access the video database at this time. "
            "Please try again later.",
            "List the videos",
        )
        self.assertFalse(ok)
        self.assertIn("refusal", reason.lower())

    def test_long_refusal_passes(self):
        """A 'refusal'-worded response that's actually elaborate and informative
        should still pass — we only flag SHORT refusals as likely errors."""
        long_text = (
            "I cannot access the external video database you mentioned, "
            "but I can describe the videos currently registered in VST. "
            "Here's a list of all videos available on this agent: "
            "warehouse_forklift_pexels_6079421 (4.1 MB), 25 seconds, "
            "1080p at 30fps. It was ingested at 2025-01-01T00:00:00Z. "
            "No other videos are currently registered. "
            "Let me know if you'd like me to help upload another video."
        )
        ok, reason = is_meaningful_response(long_text, "List videos")
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
