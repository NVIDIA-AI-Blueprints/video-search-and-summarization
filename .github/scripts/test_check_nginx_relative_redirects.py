#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_nginx_relative_redirects.py")
SPEC = importlib.util.spec_from_file_location("check_nginx_relative_redirects", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINT)

# The shape the VIOS ingress actually has: a redirect to the trailing-slash form
# nested two blocks deep, with the directive that governs it at http level.
FIXED = """\
http {
    absolute_redirect off;
    server {
        listen 30888;
        location = /vst {
            return 301 /vst/;
        }
        location /vst/assets/ {
            alias /vst-ui/assets/;
        }
    }
}
"""


def _conf(directory: str, body: str, name: str = "nginx.conf") -> Path:
    path = Path(directory) / name
    path.write_text(body)
    return path


class NginxRelativeRedirectLintTest(unittest.TestCase):
    def test_tree_emits_no_absolute_redirects(self) -> None:
        failures, _ = LINT.scan_paths(LINT.default_paths())
        self.assertEqual([], failures)

    def test_the_vios_configs_are_actually_in_scope(self) -> None:
        # A lint that silently matches nothing passes forever. These are the
        # files the defect was found in, so name them rather than trusting the
        # discovery glob.
        covered = {str(path) for path in LINT.default_paths()}
        for required in (
            "deploy/docker/services/vios/configs/nginx-vst.conf",
            "deploy/docker/services/vios/configs/nginx-vst-sdrc.conf",
            "deploy/docker/services/vios/configs/nginx-mms.conf",
            "deploy/helm/services/vios/charts/vios-ingress/configs/nginx-vst.conf.template",
        ):
            self.assertTrue(
                any(path.endswith(required) for path in covered),
                f"{required} is not in the lint's scope: {sorted(covered)}",
            )

    def test_the_fixed_shape_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            failures, checked = LINT.scan_paths([_conf(directory, FIXED)])
        self.assertEqual([], failures)
        self.assertEqual(1, checked)

    def test_removing_the_directive_fails(self) -> None:
        # The reintroduction this lint exists to catch.
        with tempfile.TemporaryDirectory() as directory:
            body = FIXED.replace("    absolute_redirect off;\n", "")
            failures, _ = LINT.scan_paths([_conf(directory, body)])
        self.assertEqual(1, len(failures))
        self.assertIn("return 301 /vst/;", failures[0])
        self.assertIn("http > server > location = /vst", failures[0])

    def test_turning_the_directive_on_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            body = FIXED.replace("absolute_redirect off;", "absolute_redirect on;")
            failures, _ = LINT.scan_paths([_conf(directory, body)])
        self.assertIn("re-enables absolutising", failures[0])

    def test_a_nearer_on_overriding_the_http_default_fails(self) -> None:
        # Inheritance runs downwards, so an `on` inside the server block beats
        # the `off` above it and nginx resumes absolutising.
        with tempfile.TemporaryDirectory() as directory:
            body = FIXED.replace(
                "    server {", "    server {\n        absolute_redirect on;"
            )
            failures, _ = LINT.scan_paths([_conf(directory, body)])
        self.assertTrue(failures)
        self.assertIn("re-enables absolutising", failures[0])

    def test_the_directive_in_a_sibling_scope_fails(self) -> None:
        # What a substring search would accept: present in the file, governing
        # a location that never redirects.
        body = """\
http {
    server {
        location /health {
            absolute_redirect off;
            return 200 'ok';
        }
        location = /vst {
            return 301 /vst/;
        }
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_paths([_conf(directory, body)])
        self.assertEqual(1, len(failures))
        self.assertIn("no `absolute_redirect off;` governs that block", failures[0])

    def test_a_commented_out_directive_fails(self) -> None:
        # The configs explain the fix in prose that quotes the directive, so a
        # lint that read comments would accept a file that has it only there.
        with tempfile.TemporaryDirectory() as directory:
            body = FIXED.replace(
                "    absolute_redirect off;", "    # absolute_redirect off;"
            )
            failures, _ = LINT.scan_paths([_conf(directory, body)])
        self.assertEqual(1, len(failures))

    def test_a_redirect_quoted_only_in_a_comment_is_not_a_redirect(self) -> None:
        # The converse, and the reason the real configs pass: they describe
        # `return 301 /vst/` in the comment that explains the fix.
        body = """\
http {
    server {
        listen 8080;
        # Without absolute_redirect off, `return 301 /vst/;` leaks port 8080.
        location /health {
            return 200 'ok';
        }
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            failures, checked = LINT.scan_paths([_conf(directory, body)])
        self.assertEqual([], failures)
        self.assertEqual(0, checked, "a 200 is not a redirect and needs no directive")

    def test_a_directory_slash_redirect_needs_the_directive_too(self) -> None:
        # `alias`/`root` never mention redirecting, but a request for a
        # directory without its trailing slash gets nginx's own 301.
        body = """\
http {
    server {
        listen 8080;
        location /videos/ {
            root /srv;
            autoindex on;
        }
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            failures, _ = LINT.scan_paths([_conf(directory, body)])
        self.assertEqual(1, len(failures))
        self.assertIn("root /srv;", failures[0])

    def test_go_template_actions_do_not_break_block_nesting(self) -> None:
        # The Helm variant is a text/template; `{{- if }}` is not an nginx block
        # and must not shift the scope the directive is measured against.
        body = """\
http {
{{- $pfx := default false .Values.useReleaseNamePrefix }}
    absolute_redirect off;
    server {
        listen 30888;
{{- if .Values.cors }}
        add_header 'Access-Control-Allow-Origin' $cors_origin always;
{{- end }}
        location = /vst {
            return 301 /vst/;
        }
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = _conf(directory, body, name="nginx.conf.template")
            failures, _ = LINT.scan_paths([path])
        self.assertEqual([], failures)

    def test_non_nginx_conf_files_are_ignored(self) -> None:
        # logstash, redis and postgres configs all end in .conf.
        body = "input {\n  kafka {\n    topics => ['x']\n  }\n}\n"
        self.assertFalse(LINT.looks_like_nginx(body))


if __name__ == "__main__":
    unittest.main()
