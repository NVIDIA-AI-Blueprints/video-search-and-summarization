#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the use-side (report-only) OSRB pass.

Two classes of test carry the weight here.

The first pins the report-only guarantee. `osrb_usage` is the one module in
the OSRB pipeline that must never fail the job, and the enforcement is
structural (see its docstring), so these tests assert the structure — that the
row constructor exposes no `change` parameter at all — rather than only
sampling the output.

The second pins the two findings that motivated the pass and that a regression
would silently erase: `import gi` in the RTVI services, and the runtime stack
`services/video-summarization` reaches but never declares. Those run against
the REAL repository tree, because the whole point of the pass is that the real
tree contains gaps a fixture would not.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

MODULE_PATH = SCRIPTS_DIR / "osrb_usage.py"
MODULE_SPEC = importlib.util.spec_from_file_location("osrb_usage", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
osrb_usage = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules["osrb_usage"] = osrb_usage
MODULE_SPEC.loader.exec_module(osrb_usage)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(REPO_ROOT), *args], text=True)


def _tracked_paths() -> list[str]:
    return _git("ls-tree", "-r", "--name-only", "HEAD").splitlines()


_BLOB_CACHE: dict[tuple[str, str], bytes | None] = {}
_CAT_FILE: subprocess.Popen[bytes] | None = None


def _read(ref: str, path: str) -> bytes | None:
    """Read a blob, memoized, over one long-lived `git cat-file --batch` pipe.

    The real-tree tests walk ~2800 source files and call `undeclared` more than
    once. One `git show` per file is ~8000 subprocesses and turns this file
    into a two-minute run; the batch pipe plus the cache keeps it near ten
    seconds, which is the difference between a test people run and one they
    skip.
    """
    global _CAT_FILE
    key = (ref, path)
    if key in _BLOB_CACHE:
        return _BLOB_CACHE[key]
    if _CAT_FILE is None:
        _CAT_FILE = subprocess.Popen(
            ["git", "-C", str(REPO_ROOT), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
    assert _CAT_FILE.stdin is not None and _CAT_FILE.stdout is not None
    _CAT_FILE.stdin.write(f"{ref}:{path}\n".encode())
    _CAT_FILE.stdin.flush()
    header = _CAT_FILE.stdout.readline().decode().split()
    if len(header) < 3 or header[1] != "blob":
        _BLOB_CACHE[key] = None
        return None
    payload = _CAT_FILE.stdout.read(int(header[2]))
    _CAT_FILE.stdout.read(1)  # trailing newline
    _BLOB_CACHE[key] = payload
    return payload


def tearDownModule() -> None:
    """Close the cat-file pipe so the run does not end on a ResourceWarning."""
    if _CAT_FILE is not None:
        if _CAT_FILE.stdin is not None:
            _CAT_FILE.stdin.close()
        _CAT_FILE.wait(timeout=10)
        if _CAT_FILE.stdout is not None:
            _CAT_FILE.stdout.close()


class ReportOnlyByConstructionTest(unittest.TestCase):
    """The guarantee the owner asked for: usage rows cannot fail the job."""

    def test_row_constructor_exposes_no_change_or_source_kind_parameter(self) -> None:
        # If either becomes a parameter, a caller can set it to `added` and the
        # advisory pass starts failing PRs on heuristic evidence.
        parameters = inspect.signature(osrb_usage._report_only_row).parameters
        self.assertNotIn("change", parameters)
        self.assertNotIn("source_kind", parameters)

    def test_no_other_change_value_is_written_anywhere_in_the_module(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {
            node.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword)
            and node.arg == "change"
            and isinstance(node.value, ast.Constant)
        }
        names = {
            node.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword)
            and node.arg == "change"
            and isinstance(node.value, ast.Name)
        }
        self.assertEqual(set(), assignments, "a literal change= value was introduced")
        self.assertEqual({"CHANGE_USED_UNDECLARED"}, names)

    def test_counts_toward_failure_is_false_for_a_usage_row(self) -> None:
        row = osrb_usage._report_only_row(
            language="python",
            package="pygobject",
            module="services/rtvi/rt-vlm",
            source_file="services/rtvi/rt-vlm/src/server/rtvi_vlm_server.py#L1",
            notes="",
        )
        self.assertEqual("USED_UNDECLARED", row["change"])
        self.assertEqual("usage", row["source_kind"])
        self.assertFalse(osrb_usage.counts_toward_failure(row))

    def test_counts_toward_failure_is_true_for_a_declared_side_row(self) -> None:
        # The predicate has to keep saying "yes" for the rows that DO fail the
        # job, or the orchestrator filters away the gate it is supposed to run.
        self.assertTrue(
            osrb_usage.counts_toward_failure({"change": "added", "source_kind": "lockfile"})
        )
        self.assertTrue(
            osrb_usage.counts_toward_failure(
                {"change": "UNCOVERED_SOURCE", "source_kind": "compose"}
            )
        )


class PythonImportsTest(unittest.TestCase):
    def test_collects_absolute_imports_and_skips_stdlib(self) -> None:
        source = b"""
import os
import sys
import numpy as np
import torch.nn.functional as F
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
"""
        self.assertEqual({"numpy", "torch", "pydantic"}, osrb_usage.python_imports(source))

    def test_relative_imports_are_first_party_and_never_reported(self) -> None:
        # `from .utils import x` names the current package. Reporting it would
        # accuse every service of depending on itself.
        source = b"""
from . import sibling
from .utils import helper
from ..shared.models import Thing
from vllm import LLM
"""
        self.assertEqual({"vllm"}, osrb_usage.python_imports(source))

    def test_namespace_root_keeps_two_segments(self) -> None:
        # `google` maps to no distribution; `google.protobuf` maps to protobuf,
        # which is the only form OSRB can act on.
        self.assertEqual(
            {"google.protobuf"},
            osrb_usage.python_imports(b"from google.protobuf import descriptor\n"),
        )

    def test_syntax_error_falls_back_to_regex_and_reports_the_degradation(self) -> None:
        # A file the AST cannot read still ships whatever it imports; dropping
        # it would be a silent hole.
        broken = b"import requests\nfrom fastapi import FastAPI\ndef (:\n"
        with self.assertRaises(SyntaxError):
            ast.parse(broken)
        located, degraded = osrb_usage._python_imports_located(broken)
        self.assertTrue(degraded)
        self.assertEqual({"requests", "fastapi"}, set(located))

    def test_line_numbers_point_at_the_import(self) -> None:
        located, degraded = osrb_usage._python_imports_located(b"\n\nimport gi\n")
        self.assertFalse(degraded)
        self.assertEqual({"gi": 3}, located)


class JsImportsTest(unittest.TestCase):
    def test_es_import_export_and_require_forms(self) -> None:
        source = b"""
import React from "react";
import {
  useState,
} from 'react';
import 'whatwg-fetch';
export { thing } from "lodash";
const kafka = require('kafkajs');
const lazy = await import("recharts");
"""
        self.assertEqual(
            {"react", "whatwg-fetch", "lodash", "kafkajs", "recharts"},
            osrb_usage.js_imports(source),
        )

    def test_relative_alias_and_builtin_specifiers_are_dropped(self) -> None:
        # `@/` and `~/` resolve back into this repo via a tsconfig path alias;
        # `fs` is Node itself. None of the three has a license to review.
        source = b"""
import a from './local';
import b from '../../shared/thing';
import c from '@/components/Button';
import d from '~/lib/util';
import fs from 'fs';
import path from 'node:path';
"""
        self.assertEqual(set(), osrb_usage.js_imports(source))

    def test_subpaths_reduce_to_the_package(self) -> None:
        source = b"""
import { Icon } from '@tabler/icons-react/dist/esm/icons';
import debounce from 'lodash/debounce';
import '@nvidia/foundations-react-core/styles.css';
"""
        self.assertEqual(
            {"@tabler/icons-react", "lodash", "@nvidia/foundations-react-core"},
            osrb_usage.js_imports(source),
        )


class CIncludesTest(unittest.TestCase):
    """The narrowing is the feature: recall is traded for precision on purpose."""

    def test_single_segment_includes_never_survive(self) -> None:
        # <vector> is the standard library and "logger.h" is first-party. This
        # repo spells over 1500 includes that way; every one of them would be a
        # false positive.
        source = b'#include <vector>\n#include <string.h>\n#include "logger.h"\n'
        self.assertEqual(set(), osrb_usage.c_includes(source))

    def test_multi_segment_includes_are_candidates_only(self) -> None:
        source = b'#include <aws/core/Aws.h>\n#include  "gst/gst.h"\n'
        self.assertEqual({"aws/core/Aws.h", "gst/gst.h"}, osrb_usage.c_includes(source))

    def test_only_includes_that_resolve_inside_a_vendored_tree_get_a_package(self) -> None:
        index = osrb_usage.VendoredIndex(
            [
                "services/vios/include/3rdparty/aws/core/Aws.h",
                "services/vios/src/framework/x/third_party/abseil-cpp/absl/base/macros.h",
                "services/vios/include/logger.h",
            ]
        )
        self.assertEqual(
            ("aws", "services/vios/include/3rdparty/aws/core/Aws.h"),
            osrb_usage.vendored_include_package("aws/core/Aws.h", index),
        )
        # Resolved one level deeper, because the package dir is on the -I path.
        self.assertEqual(
            (
                "abseil-cpp",
                "services/vios/src/framework/x/third_party/abseil-cpp/absl/base/macros.h",
            ),
            osrb_usage.vendored_include_package("absl/base/macros.h", index),
        )
        # A system library that is not vendored here resolves to nothing.
        self.assertIsNone(osrb_usage.vendored_include_package("gst/gst.h", index))

    def test_bare_stdlib_header_cannot_resolve_to_an_extensionless_vendored_header(self) -> None:
        # libpqxx ships `pqxx/array`, so a child-directory search for the spec
        # `array` used to resolve `#include <array>` to libpqxx across 25 files.
        index = osrb_usage.VendoredIndex(["services/vios/include/3rdparty/pqxx/array"])
        self.assertIsNone(osrb_usage.vendored_include_package("array", index))
        self.assertEqual(set(), osrb_usage.c_includes(b"#include <array>\n"))


class JvmRubyImportsTest(unittest.TestCase):
    def test_java_group_prefixes_and_jdk_exclusion(self) -> None:
        source = b"""
package nv;
import java.util.Map;
import javax.annotation.Nullable;
import com.google.gson.Gson;
import redis.clients.jedis.Jedis;
import co.elastic.logstash.api.Configuration;
import org.apache.logging.log4j.Logger;
"""
        self.assertEqual(
            {
                "com.google.gson",
                "redis.clients",
                "co.elastic.logstash",
                "org.apache.logging",
            },
            osrb_usage.jvm_ruby_imports(source, "java"),
        )

    def test_ruby_require_maps_the_longest_aliased_prefix(self) -> None:
        # Both requires are the one gem `google-protobuf`; the first path
        # segment alone would invent a second package called `google`.
        source = b"""
require 'google/protobuf'
require 'google/protobuf/timestamp_pb'
require_relative './schema_pb'
"""
        self.assertEqual({"google-protobuf"}, osrb_usage.jvm_ruby_imports(source, "ruby"))


class ScanScopeTest(unittest.TestCase):
    def test_vendored_trees_are_not_scanned_as_sources(self) -> None:
        for path in (
            "services/vios/include/3rdparty/aws/core/Aws.h",
            "services/vios/src/framework/webrtc_streamer/inc/webrtc_headers/src/"
            "third_party/abseil-cpp/absl/base/macros.h",
            "services/agent/3rdparty/ffmpeg/setup.py",
            "services/ui/node_modules/react/index.js",
        ):
            self.assertTrue(osrb_usage.is_vendored(path), path)
            self.assertIsNone(osrb_usage.source_language(path), path)

    def test_dev_only_sources_are_skipped(self) -> None:
        # Same rule osrb_scan already applies to lockfiles: a dev dependency
        # never reaches a customer, so OSRB does not review it.
        for path in (
            "services/rtvi/rt-vlm/tests/kafka/test_kafka_consumer.py",
            "services/video-summarization/src/conftest.py",
            "services/vios/ui/vios-ui/src/App.test.tsx",
            "services/ui/vite.config.ts",
            "libs/analytics/spatialai-data-utils/release/setup.py",
            "tools/logstash-plugins/input/redis-stream/src/test/java/nv/RedisStreamTest.java",
        ):
            self.assertIsNone(osrb_usage.source_language(path), path)

    def test_shipping_sources_are_scanned(self) -> None:
        self.assertEqual(
            "python", osrb_usage.source_language("services/video-summarization/src/via_server.py")
        )
        self.assertEqual("node", osrb_usage.source_language("services/ui/packages/common/src/x.tsx"))
        self.assertEqual("c", osrb_usage.source_language("services/vios/src/app/server.cpp"))
        self.assertEqual(
            "java",
            osrb_usage.source_language(
                "tools/logstash-plugins/input/redis-stream/src/main/java/nv/RedisStream.java"
            ),
        )

    def test_type_stubs_are_not_runtime_imports(self) -> None:
        # A .pyi declares types for a type checker; `_typeshed` is not a wheel
        # anyone can install, let alone review.
        self.assertIsNone(
            osrb_usage.source_language(
                "services/analytics/behavior-analytics/src/mdx/analytics/core/"
                "typings/confluent_kafka/__init__.pyi"
            )
        )


class FirstPartyIndexTest(unittest.TestCase):
    def test_a_sys_path_root_that_is_also_a_package_yields_both_forms(self) -> None:
        # services/video-summarization/src has an __init__.py AND is on
        # sys.path, so `via_logger` and `protos.nv_pb2` are both real imports.
        paths = [
            "services/video-summarization/src/__init__.py",
            "services/video-summarization/src/via_logger.py",
            "services/video-summarization/src/protos/__init__.py",
            "services/video-summarization/src/protos/nv_pb2.py",
        ]
        names = osrb_usage.first_party_names(paths, ".py", frozenset())
        self.assertIn("via_logger", names["services/video-summarization"])
        self.assertIn("protos", names["services/video-summarization"])
        self.assertIn("src", names["services/video-summarization"])

    def test_a_declared_package_name_is_never_shadowed_by_a_local_file(self) -> None:
        # This tree really does contain vss_core/vlm/openai.py. Treating that
        # basename as first-party would erase `import openai` from the report
        # with no trace, which is the worst outcome for a compliance gate.
        paths = [
            "services/agent/packages/vss_core/src/vss_core/__init__.py",
            "services/agent/packages/vss_core/src/vss_core/vlm/__init__.py",
            "services/agent/packages/vss_core/src/vss_core/vlm/openai.py",
        ]
        names = osrb_usage.first_party_names(paths, ".py", frozenset({"openai"}))
        self.assertNotIn("openai", names["services/agent"])
        self.assertIn("vss_core", names["services/agent"])
        self.assertIn("vlm", names["services/agent"])

    def test_test_directories_do_not_register_packages(self) -> None:
        # tests/kafka/ and tests/redis/ would otherwise mask the real clients.
        paths = ["services/rtvi/rt-vlm/tests/kafka/test_kafka_consumer.py"]
        self.assertEqual({}, osrb_usage.first_party_names(paths, ".py", frozenset()))


class DistributionMatchingTest(unittest.TestCase):
    def test_import_names_map_to_distribution_names(self) -> None:
        for import_name, distribution in (
            ("cv2", "opencv-python"),
            ("PIL", "pillow"),
            ("yaml", "PyYAML"),
            ("gi", "PyGObject"),
            ("google.protobuf", "protobuf"),
            ("pkg_resources", "setuptools"),
        ):
            self.assertEqual(
                distribution,
                osrb_usage.candidate_distributions("python", import_name)[0],
                import_name,
            )

    def test_underscore_import_matches_a_hyphenated_distribution(self) -> None:
        self.assertTrue(
            osrb_usage._is_declared("python", "sse_starlette", {"sse-starlette"})
        )

    def test_family_prefix_satisfies_a_namespace_import(self) -> None:
        # `import opentelemetry.trace` is satisfied by opentelemetry-api: one
        # upstream project, one license, many wheels.
        self.assertTrue(
            osrb_usage._is_declared("python", "opentelemetry", {"opentelemetry-api"})
        )
        # The prefix rule is deliberately narrow; `redis` is NOT satisfied by
        # `redis-lock`, which is a different project under a different license.
        self.assertFalse(osrb_usage._is_declared("python", "redis", {"redis-lock"}))

    def test_java_matching_is_token_based_because_group_ids_are_approximate(self) -> None:
        self.assertTrue(
            osrb_usage._is_declared("java", "com.google.gson", {"com-google-code-gson-gson"})
        )
        self.assertFalse(
            osrb_usage._is_declared("java", "co.elastic.logstash", {"redis-clients-jedis"})
        )

    def test_c_never_matches_a_manifest(self) -> None:
        # services/vios pins the PyPI package `minio` in a Python lockfile; the
        # vendored C++ minio-cpp headers are a separate dependency and must not
        # be suppressed by that collision.
        self.assertFalse(osrb_usage._is_declared("c", "minio", {"minio"}))


class UndeclaredAgainstTheRealRepoTest(unittest.TestCase):
    """The findings that motivated the pass, checked against the real tree."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = _tracked_paths()
        # Declared side kept intentionally EMPTY: this asserts what the use
        # side extracts, independently of how osrb_scan builds its inventory.
        cls.rows = osrb_usage.undeclared("HEAD", _read, cls.paths, {})
        cls.by_key = {(r["module"], r["package"]): r for r in cls.rows}

    def test_every_row_is_report_only(self) -> None:
        self.assertTrue(self.rows)
        for row in self.rows:
            self.assertEqual("USED_UNDECLARED", row["change"])
            self.assertEqual("usage", row["source_kind"])
            self.assertFalse(osrb_usage.counts_toward_failure(row))

    def test_pygobject_is_found_in_both_rtvi_services(self) -> None:
        # `import gi` pulls PyGObject (LGPL-2.1-or-later) into two shipped
        # services and appears in no manifest anywhere in this repo. It is
        # invisible to every declared-side pass.
        for module in ("services/rtvi/rt-embed", "services/rtvi/rt-vlm"):
            row = self.by_key.get((module, "PyGObject"))
            self.assertIsNotNone(row, f"PyGObject not reported for {module}")
            self.assertIn("imported as `gi`", row["notes"])
            self.assertTrue(row["source_file"].startswith(module + "/"))
            self.assertIn("#L", row["source_file"])

    def test_video_summarization_runtime_stack_is_reported(self) -> None:
        # Its own pyproject/uv.lock pin 17 packages, none of which is the web
        # stack the service actually runs on.
        for package in (
            "fastapi",
            "uvicorn",
            "starlette",
            "pydantic",
            "httpx",
            "mcp",
            "protobuf",
        ):
            self.assertIn(
                ("services/video-summarization", package),
                self.by_key,
                f"{package} missing from the video-summarization report",
            )

    def test_vendored_cpp_is_attributed_to_a_committed_path(self) -> None:
        row = self.by_key.get(("services/vios", "aws"))
        self.assertIsNotNone(row)
        self.assertEqual("c", row["language"])
        self.assertIn("services/vios/include/3rdparty/aws/", row["notes"])

    def test_first_party_modules_are_not_reported_as_packages(self) -> None:
        # Regressions here are how a report becomes unreadable: every one of
        # these is a file inside the module that reports it.
        for module, package in (
            ("services/video-summarization", "via_logger"),
            ("services/video-summarization", "via_stream_handler"),
            ("services/video-summarization", "protos"),
            ("services/rtvi/rt-vlm", "api_models"),
            ("services/rtvi/rt-vlm", "vlm_pipeline"),
            ("services/rtvi/rt-embed", "utils"),
            ("libs/analytics/spatialai-data-utils", "spatialai_data_utils"),
            ("deploy", "schema_pb"),
        ):
            self.assertNotIn((module, package), self.by_key, f"{module}: {package}")

    def test_stdlib_and_node_builtins_are_absent(self) -> None:
        packages = {r["package"] for r in self.rows}
        self.assertTrue(
            packages.isdisjoint({"os", "sys", "json", "fs", "path", "http", "crypto"}),
            sorted(packages & {"os", "sys", "json", "fs", "path", "http", "crypto"}),
        )

    def test_a_declared_dependency_is_not_reported_for_its_own_module(self) -> None:
        # services/sdrc pins python-redis-lock and dnspython; `import
        # redis_lock` / `import dns` must resolve to them through the alias
        # table rather than being reported as gaps.
        rows = osrb_usage.undeclared(
            "HEAD",
            _read,
            self.paths,
            {"services/sdrc": {"python-redis-lock", "dnspython"}},
        )
        keys = {(r["module"], r["package"]) for r in rows}
        self.assertNotIn(("services/sdrc", "python-redis-lock"), keys)
        self.assertNotIn(("services/sdrc", "dnspython"), keys)

    def test_a_package_declared_only_elsewhere_is_still_reported_and_says_so(self) -> None:
        # An import satisfied by another service's lockfile is still a gap for
        # this module: the two ship as separate containers.
        rows = osrb_usage.undeclared(
            "HEAD", _read, self.paths, {"services/agent": {"fastapi"}}
        )
        row = next(
            r
            for r in rows
            if r["module"] == "services/video-summarization" and r["package"] == "fastapi"
        )
        self.assertIn("declared only by services/agent", row["notes"])


if __name__ == "__main__":
    unittest.main()
