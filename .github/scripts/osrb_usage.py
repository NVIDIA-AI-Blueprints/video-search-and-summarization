#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Use-side OSRB pass: third-party code the source reaches that no manifest declares.

The lockfile/manifest passes in ``osrb_scan`` answer "what did we say we depend
on". They cannot answer "what does the code actually import", and that gap is
how a dependency ships to a customer without ever reaching the Open Source
Review Board: ``import gi`` pulls PyGObject (LGPL-2.1-or-later) into two RTVI
services and appears in no ``pyproject.toml``, ``pdm.lock`` or
``requirements.txt`` anywhere in this repo. Nothing in the declared-side scan
can see that, because there is nothing declared to diff.

REPORT-ONLY BY CONSTRUCTION — this is an owner decision, not a default
=====================================================================
Import-graph evidence is heuristic. A name can be a build-time-generated
module, a wheel installed by a Dockerfile ``pip install`` line, or a sibling
package on ``sys.path``. Failing CI on a heuristic would train reviewers to
ignore the gate, which is the exact failure this whole change exists to
prevent. So every row this module produces is advisory.

That is enforced structurally, not by convention:

* ``_report_only_row()`` is the ONLY row constructor here, and it takes no
  ``change`` and no ``source_kind`` argument. Both are literals inside its
  body. There is no parameter a caller — or a future edit to this file — can
  set to turn a usage row into a failing one.
* ``undeclared()`` re-checks every row it is about to return and raises
  ``AssertionError`` if either literal drifted. A refactor that breaks the
  guarantee fails the unit tests loudly instead of silently arming the gate.
* ``counts_toward_failure()`` is exported so the orchestrator can filter on an
  explicit predicate rather than on a string comparison it has to remember.

Precision over recall, on purpose
=================================
Every extractor here deliberately under-reports rather than guess:

* C/C++ includes are reported ONLY when the spelled path resolves to a file
  inside a vendored third-party directory committed to this repo. ``#include
  <vector>``, ``#include "logger.h"`` and ``#include <gst/gst.h>`` all produce
  nothing — the first two are not third-party, and the third is an apt-provided
  system library whose license is reviewed as part of the base image, not the
  source tree. See ``vendored_include_package``.
* Test files and build/lint config files are not scanned. Dev-only
  dependencies do not ship, which is the same rule ``osrb_scan`` already
  applies when it inventories only the runtime group of a lockfile. Scanning
  ``vite.config.ts`` would bury the real findings under rollup plugins.
* Vendored third-party trees are not scanned as sources. abseil's own includes
  are abseil's business; what matters is that OUR code reaches abseil.

Comparison is per MODULE, and that is the point
===============================================
``services/video-summarization`` importing ``fastapi`` is a gap even though
``services/agent`` locks ``fastapi``: the two ship as different containers, and
OSRB reviews per shipped artifact. A row whose package is declared by some
other module says so in ``notes`` rather than being suppressed.

Public API (see .github/scripts CONTRACT):
    python_imports(data) / js_imports(data) / c_includes(data)
    jvm_ruby_imports(data, lang)
    DISTRIBUTION_ALIASES
    undeclared(ref, read, source_paths, declared_by_module)
"""

from __future__ import annotations

import ast
import json
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Mapping

# CI runs these scripts as plain files (`python .github/scripts/osrb_scan.py`),
# and the unit tests load them by path with importlib, so neither entry point
# leaves this directory importable by name. Adding it explicitly is what lets
# the risk model and the module-ownership rule live in exactly one place.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from osrb_scan import license_risk, make_row, owning_module  # noqa: E402

# The two literals that make this pass advisory. They are duplicated nowhere
# else in this file; `_report_only_row` is the only place they are written into
# a row, and it exposes neither as a parameter.
CHANGE_USED_UNDECLARED = "USED_UNDECLARED"
SOURCE_KIND_USAGE = "usage"


def _log(msg: str) -> None:
    print(f"[osrb-usage] {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# Name tables. Apart from the seed set the OSRB contract mandates, every entry
# is a name that actually appears in this repository: a speculative table rots
# silently, and a wrong alias turns a real finding into "already declared".
# --------------------------------------------------------------------------

#: Python import name -> PyPI distribution name. Without this table every one
#: of these reads as an undeclared dependency even when the lockfile pins it,
#: because `import cv2` and `opencv-python` share no characters.
DISTRIBUTION_ALIASES: dict[str, str] = {
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "PyYAML",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "attr": "attrs",
    "OpenSSL": "pyOpenSSL",
    "serial": "pyserial",
    "Crypto": "pycryptodome",
    "dateutil": "python-dateutil",
    "gi": "PyGObject",
    "google.protobuf": "protobuf",
    "kafka": "kafka-python",
    "pkg_resources": "setuptools",
    "importlib_metadata": "importlib-metadata",
    # Present in this tree on top of the mandated seed set.
    "mpl_toolkits": "matplotlib",
    "dns": "dnspython",
    "redis_lock": "python-redis-lock",
    # Two PyPI projects install a module called `sseclient`; every manifest in
    # this repo that pins one pins `sseclient-py`, so that is the mapping that
    # matches reality here.
    "sseclient": "sseclient-py",
    "paho": "paho-mqtt",
    "paho.mqtt": "paho-mqtt",
    "google.cloud": "google-cloud",
    "grpc": "grpcio",
    "cuda": "cuda-python",
    "nat": "nvidia-nat",
    "riva": "nvidia-riva-client",
}

#: Ruby require path -> gem name. `require 'google/protobuf'` is the gem
#: `google-protobuf`, not a gem called `google`.
RUBY_GEM_ALIASES: dict[str, str] = {
    "google/protobuf": "google-protobuf",
}

#: Python namespace packages: the top-level name alone identifies no
#: distribution, so two segments are kept. `google` is meaningless;
#: `google.protobuf` maps to `protobuf`.
PYTHON_NAMESPACE_ROOTS = frozenset({"google", "ruamel"})

#: Distribution families published as many hyphenated wheels from one upstream
#: project under one license. `import opentelemetry.sdk` is satisfied by
#: `opentelemetry-sdk`; requiring an exact name match would report every one of
#: them as a gap. Deliberately short — each entry trades a little recall for a
#: lot less noise, and each is one upstream license.
DECLARED_FAMILY_PREFIXES = frozenset(
    {"opentelemetry", "google", "azure", "langchain", "nvidia", "llama-index"}
)

#: Node's built-in modules. Python has `sys.stdlib_module_names`; Node exposes
#: no equivalent to a Python process, and un-prefixed builtins (`require("fs")`
#: rather than `require("node:fs")`) are still the majority style in this repo,
#: so the list has to be carried here. Missing an entry means reporting `fs` as
#: an undeclared npm dependency.
NODE_BUILTINS = frozenset(
    """assert async_hooks buffer child_process cluster console constants crypto
    dgram diagnostics_channel dns domain events fs http http2 https inspector
    module net os path perf_hooks process punycode querystring readline repl
    stream string_decoder sys timers tls trace_events tty url util v8 vm wasi
    worker_threads zlib""".split()
)

#: JDK-supplied package roots. `import java.util.Map;` is not a dependency.
JDK_PACKAGE_PREFIXES = ("java.", "javax.", "jdk.", "sun.", "com.sun.")

#: Dotted first segments that take three segments as the Maven group id
#: (`com.google.gson`), versus two for everything else (`redis.clients`).
_JVM_TLD_SEGMENTS = frozenset(
    {"com", "org", "net", "io", "co", "me", "dev", "cloud", "ai", "app", "uk", "eu"}
)

LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".ts": "node",
    ".tsx": "node",
    ".js": "node",
    ".jsx": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".c": "c",
    ".cc": "c",
    ".cpp": "c",
    ".cxx": "c",
    ".h": "c",
    ".hh": "c",
    ".hpp": "c",
    ".hxx": "c",
    ".cu": "c",
    ".cuh": "c",
    ".inl": "c",
    ".java": "java",
    ".rb": "ruby",
}


# --------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------

_PY_IMPORT_FALLBACK_RE = re.compile(
    rb"^[ \t]*(?:import[ \t]+([A-Za-z_][\w.]*)|from[ \t]+([A-Za-z_][\w.]*)[ \t]+import)",
    re.M,
)


def _python_top_level(dotted: str) -> str:
    """Collapse a dotted import to the name that identifies a distribution.

    Two segments are kept for namespace roots only. `google` on its own maps to
    no distribution, so collapsing `google.protobuf` to `google` would lose the
    single fact that makes the row actionable — that this is protobuf.
    """
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        return ""
    if parts[0] in PYTHON_NAMESPACE_ROOTS and len(parts) > 1:
        return f"{parts[0]}.{parts[1]}"
    return parts[0]


def _python_imports_located(data: bytes) -> tuple[dict[str, int], bool]:
    """Return ({import name: first line}, degraded?) for one Python file.

    Only ``level == 0`` imports are collected. A relative import
    (``from .utils import x``, level >= 1) is first-party by definition, and
    reporting the current package as a third-party dependency would be pure
    noise — `ast` is the only way to tell the two apart reliably, which is why
    the regex path below is flagged as degraded.

    Standard-library names are dropped against ``sys.stdlib_module_names``
    rather than a hand-list, so the exclusion tracks the interpreter CI runs.

    On ``SyntaxError`` the file is re-scanned with a line-oriented regex. That
    happens for a file that is not valid Python 3.12 (a Jinja-templated
    ``.py``, or a stray Python 2 script). Losing the file entirely would be the
    worse outcome: a container that installs a dependency for a file the AST
    could not read still ships that dependency. The caller records the
    degradation in the row's notes, because a regex cannot see ``level`` and so
    may over-report relative imports.
    """
    stdlib = sys.stdlib_module_names
    found: dict[str, int] = {}

    def remember(name: str, lineno: int) -> None:
        top = _python_top_level(name)
        if not top or top.split(".")[0] in stdlib:
            return
        if top not in found or lineno < found[top]:
            found[top] = lineno

    try:
        with warnings.catch_warnings():
            # Compiling a source file emits its SyntaxWarnings (a stray `"\."`
            # in a non-raw string, say). Those belong to the file's own author,
            # not to this scan, and one line per file would bury the OSRB
            # annotations in the Actions log.
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(data)
    except (SyntaxError, ValueError):
        for match in _PY_IMPORT_FALLBACK_RE.finditer(data):
            raw = (match.group(1) or match.group(2) or b"").decode(
                "utf-8", errors="replace"
            )
            lineno = data.count(b"\n", 0, match.start()) + 1
            remember(raw, lineno)
        return found, True

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                remember(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is `from . import x` / `from ..pkg import y`: first-party.
            if node.level == 0 and node.module:
                remember(node.module, node.lineno)
    return found, False


def python_imports(data: bytes) -> set[str]:
    """Third-party top-level module names imported by one Python source file."""
    located, _degraded = _python_imports_located(data)
    return set(located)


# `from` on its own line is how a multi-line `import { a, b } from "pkg"` ends,
# so the specifier is matched off `from`/`require`/`import(` rather than off the
# `import` keyword, which may be many lines earlier.
_JS_SPECIFIER_RES = (
    re.compile(r"""\bfrom\s*["']([^"'\n]+)["']"""),
    re.compile(r"""^\s*import\s+["']([^"'\n]+)["']""", re.M),
    re.compile(r"""\bimport\s*\(\s*["']([^"'\n]+)["']"""),
    re.compile(r"""\brequire\s*\(\s*["']([^"'\n]+)["']"""),
)


def _js_package(specifier: str) -> str | None:
    """Reduce an ES/CJS specifier to the npm package name, or None if it is not one.

    Dropped, in order: relative paths (``./x``, ``../x``, ``/x``), TypeScript /
    bundler path aliases (``@/components``, ``~/lib``) which resolve back into
    this repo, URL imports, and ``node:`` builtins. A scoped specifier keeps two
    segments (``@scope/pkg/sub`` -> ``@scope/pkg``) because the scope alone is
    not a package; an unscoped one keeps one (``pkg/sub`` -> ``pkg``).
    """
    spec = specifier.strip()
    if not spec or spec.startswith((".", "/", "~", "@/", "#")):
        return None
    if "://" in spec or spec.startswith("node:") or spec.startswith("data:"):
        return None
    parts = [p for p in spec.split("/") if p]
    if not parts:
        return None
    if spec.startswith("@"):
        if len(parts) < 2:
            return None  # a bare "@scope" is not importable
        name = f"{parts[0]}/{parts[1]}"
    else:
        name = parts[0]
    if name in NODE_BUILTINS:
        return None
    return name


def _js_imports_located(data: bytes) -> dict[str, int]:
    text = data.decode("utf-8", errors="replace")
    found: dict[str, int] = {}
    for pattern in _JS_SPECIFIER_RES:
        for match in pattern.finditer(text):
            name = _js_package(match.group(1))
            if not name:
                continue
            lineno = text.count("\n", 0, match.start()) + 1
            if name not in found or lineno < found[name]:
                found[name] = lineno
    return found


def js_imports(data: bytes) -> set[str]:
    """npm package names reached by one JS/TS source file."""
    return set(_js_imports_located(data))


_C_INCLUDE_RE = re.compile(rb"""^[ \t]*#[ \t]*include[ \t]*[<"]([^>"\n]+)[>"]""", re.M)


def _c_includes_located(data: bytes) -> dict[str, int]:
    found: dict[str, int] = {}
    for match in _C_INCLUDE_RE.finditer(data):
        spec = match.group(1).decode("utf-8", errors="replace").strip()
        # A single-segment include is either a standard header (<vector>,
        # <string.h>) or a sibling header ("logger.h"). Neither can be
        # attributed to a package from the spelling alone, and both are the
        # dominant shape in this repo (over 1500 of them), so they are dropped
        # here rather than being handed to the resolver as noise.
        if "/" not in spec:
            continue
        found.setdefault(spec, data.count(b"\n", 0, match.start()) + 1)
    return found


def c_includes(data: bytes) -> set[str]:
    """Multi-segment ``#include`` targets, quoted and angle-bracketed.

    NOT REPORTABLE ON ITS OWN. This returns the include as spelled; most of
    them (``gst/gst.h``, ``opentelemetry/version.h``, ``rtc_base/checks.h``)
    are system or first-party headers. A spelling becomes a row only after
    ``vendored_include_package`` resolves it to a file that is actually
    committed under a vendored third-party directory. See the module docstring
    for why the C pass is narrowed this hard.
    """
    return set(_c_includes_located(data))


_JAVA_IMPORT_RE = re.compile(r"""^\s*import\s+(?:static\s+)?([\w.$]+)\s*;""", re.M)
_RUBY_REQUIRE_RE = re.compile(
    r"""^\s*require(?:_relative)?\s*\(?\s*["']([^"'\n]+)["']""", re.M
)


def _jvm_group_id(package: str) -> str | None:
    """Best-effort Maven group id for an imported Java package.

    Three segments when the first is a reverse-DNS TLD token
    (``com.google.gson``), two otherwise (``redis.clients.jedis`` ->
    ``redis.clients``). This is a heuristic and it is wrong for some real
    coordinates — ``org.apache.logging.log4j`` is a four-segment group id, and
    ``org.logstash.plugins`` belongs to the two-segment group ``org.logstash``.
    Matching in ``undeclared`` is therefore token-based rather than exact, and
    the full imported package is carried in the row's notes so a reviewer can
    resolve it. Only ``tools/logstash-plugins`` builds JVM code today, so the
    blast radius of the heuristic is four files.
    """
    if package.startswith(JDK_PACKAGE_PREFIXES):
        return None
    parts = [p for p in package.split(".") if p]
    if len(parts) < 2:
        return None
    keep = 3 if parts[0] in _JVM_TLD_SEGMENTS and len(parts) >= 3 else 2
    return ".".join(parts[:keep])


def _ruby_gem(require_path: str) -> str:
    """Gem name for a Ruby require path.

    ``require 'google/protobuf/timestamp_pb'`` is the same gem as
    ``require 'google/protobuf'``; matching only the exact string would report
    a phantom gem called ``google`` alongside the real ``google-protobuf``.
    So the longest aliased prefix wins, and the first path segment is the
    fallback.
    """
    segments = require_path.split("/")
    for stop in range(len(segments), 0, -1):
        alias = RUBY_GEM_ALIASES.get("/".join(segments[:stop]))
        if alias:
            return alias
    return segments[0]


def _jvm_ruby_located(data: bytes, lang: str) -> dict[str, int]:
    text = data.decode("utf-8", errors="replace")
    found: dict[str, int] = {}
    if lang == "java":
        for match in _JAVA_IMPORT_RE.finditer(text):
            group = _jvm_group_id(match.group(1).rstrip(".*"))
            if not group:
                continue
            lineno = text.count("\n", 0, match.start()) + 1
            if group not in found or lineno < found[group]:
                found[group] = lineno
    elif lang == "ruby":
        for match in _RUBY_REQUIRE_RE.finditer(text):
            spec = match.group(1).strip()
            if spec.startswith((".", "/")):
                continue
            gem = _ruby_gem(spec)
            lineno = text.count("\n", 0, match.start()) + 1
            if gem not in found or lineno < found[gem]:
                found[gem] = lineno
    return found


def jvm_ruby_imports(data: bytes, lang: str) -> set[str]:
    """Maven group ids (``lang="java"``) or gem names (``lang="ruby"``).

    Ruby ``require`` is a file path, not a gem name, so an entry in
    ``RUBY_GEM_ALIASES`` is what turns ``google/protobuf`` into the gem
    ``google-protobuf``; anything unmapped falls back to the first path
    segment. ``require_relative`` and paths starting with ``.`` or ``/`` are
    first-party and dropped.
    """
    return set(_jvm_ruby_located(data, lang))


# --------------------------------------------------------------------------
# Repository shape
# --------------------------------------------------------------------------

#: Vendored third-party trees committed to this repo. Copied verbatim from the
#: OSRB scan contract. Anything under one of these is upstream code: it is not
#: scanned as a source (abseil's own includes are abseil's problem), and it is
#: the only place a C include is allowed to resolve to.
_VENDORED_PREFIXES = (
    "services/vios/include/3rdparty/",
    "services/agent/3rdparty/",
)
_VENDORED_VIOS_SRC_PREFIX = "services/vios/src/"
_VENDOR_DIR_NAMES = frozenset({"3rdparty", "third_party", "thirdparty"})

_TEST_DIR_SEGMENTS = frozenset({"test", "tests", "__tests__"})
#: Whole filenames that are build scripts rather than shipped modules.
_CONFIG_BASENAMES = frozenset({"setup.py", "noxfile.py"})

_CONFIG_STEMS = frozenset(
    {
        "vite.config",
        "vitest.config",
        "rollup.config",
        "webpack.config",
        "next.config",
        "jest.config",
        "eslint.config",
        "tailwind.config",
        "postcss.config",
        "babel.config",
        "playwright.config",
        "commitlint.config",
        "metro.config",
        "svgo.config",
    }
)


def is_vendored(path: str) -> bool:
    """True for upstream code committed into this tree (and npm installs)."""
    if "/node_modules/" in path or path.startswith("node_modules/"):
        return True
    if path.startswith(_VENDORED_PREFIXES):
        return True
    return path.startswith(_VENDORED_VIOS_SRC_PREFIX) and "/third_party/" in path


def is_test_path(path: str) -> bool:
    """True for test-only sources, which are excluded for the same reason
    ``osrb_scan`` inventories only the runtime group of a lockfile: a dev
    dependency never reaches a customer, so OSRB does not review it. Scanning
    them would put pytest, chai and sinon at the top of the report."""
    segments = path.split("/")
    if any(seg in _TEST_DIR_SEGMENTS for seg in segments[:-1]):
        return True
    base = segments[-1]
    stem = base.rsplit(".", 1)[0]
    if base == "conftest.py":
        return True
    if base.endswith(".py") and (stem.startswith("test_") or stem.endswith("_test")):
        return True
    if base.endswith(".java") and stem.endswith(("Test", "Tests", "IT")):
        return True
    return any(
        stem.endswith(suffix) for suffix in (".test", ".spec")
    ) and base.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))


def is_build_config(path: str) -> bool:
    """True for bundler/lint config modules.

    ``vite.config.ts`` imports vite and every rollup plugin the build uses.
    Those are devDependencies: they run on a build agent and are absent from
    the shipped image, so treating their imports as shipped dependencies would
    add ~15 tool packages per UI module and bury the runtime findings.
    """
    base = path.split("/")[-1]
    if base in _CONFIG_BASENAMES:
        # `setup.py` imports setuptools to describe the build; setuptools is
        # not a dependency of the code that ships.
        return True
    if base.startswith(".eslintrc") or base.startswith(".prettierrc"):
        return True
    parts = base.split(".")
    return len(parts) >= 3 and ".".join(parts[:2]) in _CONFIG_STEMS


def source_language(path: str) -> str | None:
    """Language of a scannable source file, or None to skip it entirely."""
    if is_vendored(path) or is_test_path(path) or is_build_config(path):
        return None
    dot = path.rfind(".")
    if dot < 0:
        return None
    return LANGUAGE_BY_EXT.get(path[dot:].lower())


class VendoredIndex:
    """Resolver for ``#include`` spellings against committed vendored trees.

    Holds every vendored root (``.../3rdparty/``) plus the set of files under
    it, so ``aws/core/Aws.h`` can be checked as a real committed path instead
    of being guessed at from its first segment.
    """

    def __init__(self, paths: Iterable[str]) -> None:
        self._files: set[str] = set()
        self._roots: set[str] = set()
        for path in paths:
            if not is_vendored(path):
                continue
            self._files.add(path)
            segments = path.split("/")
            for index, segment in enumerate(segments):
                if segment in _VENDOR_DIR_NAMES:
                    self._roots.add("/".join(segments[: index + 1]) + "/")
        self._children: dict[str, list[str]] = {}
        for root in self._roots:
            children = {
                p[len(root) :].split("/")[0]
                for p in self._files
                if p.startswith(root)
            }
            self._children[root] = sorted(children)
        self._sorted_roots = sorted(self._roots)

    @property
    def roots(self) -> list[str]:
        return list(self._sorted_roots)

    def resolve(self, spec: str) -> tuple[str, str] | None:
        """Return (package, committed path) for an include, or None.

        Two resolutions are tried, in order:

        1. ``<root>/<spec>`` — how ``aws/core/Aws.h`` is spelled when the
           vendored root itself is on the include path.
        2. ``<root>/<pkg>/<spec>`` — how ``absl/base/macros.h`` is spelled when
           the package directory (``third_party/abseil-cpp/``) is on the
           include path.

        Nothing else is attempted. In particular a single-segment spec never
        reaches here (``c_includes`` drops it), which is what stops
        ``#include <array>`` from resolving to libpqxx's extensionless
        ``3rdparty/pqxx/array`` header — a false positive this resolver
        produced before that guard existed.
        """
        if "/" not in spec or spec.startswith("/") or ".." in spec.split("/"):
            return None
        for root in self._sorted_roots:
            candidate = root + spec
            if candidate in self._files:
                return spec.split("/")[0], candidate
        for root in self._sorted_roots:
            for child in self._children.get(root, ()):
                candidate = f"{root}{child}/{spec}"
                if candidate in self._files:
                    return child, candidate
        return None


def vendored_include_package(spec: str, index: VendoredIndex) -> tuple[str, str] | None:
    """(package, resolved path) for an include that lands in a vendored tree."""
    return index.resolve(spec)


def first_party_names(
    paths: Iterable[str], suffix: str, protected: frozenset[str] | set[str]
) -> dict[str, set[str]]:
    """Map owning module -> names its own source files provide, per language.

    Without this, every intra-service import (``import via_logger``,
    ``from api_models.captions import ...``, ``require 'schema_pb'``) is
    reported as an undeclared third-party package.

    A file contributes its own basename AND every directory in its package
    chain. Both are needed in this repo and neither is redundant:
    ``services/video-summarization/src/`` has an ``__init__.py`` yet is also a
    ``sys.path`` root (``pyproject.toml`` sets ``pythonpath = ["src"]``), so
    ``src/via_logger.py`` is imported as top-level ``via_logger``, while
    ``src/protos/nv_pb2.py`` is imported as ``protos.nv_pb2``. Deriving only
    package names would miss the first; deriving only basenames would miss the
    second.

    `protected` is what stops that breadth from becoming a false NEGATIVE, and
    it is the more dangerous direction: masking a real dependency removes it
    from the report with no trace. This tree really does contain
    ``vss_core/vlm/openai.py``, ``vss_core/memory/backends/elasticsearch.py``,
    ``lib/messaging/kafka.py`` and ``models/requests.py`` — four filenames that
    collide with four packages OSRB must see. Any name that some manifest in
    this repo declares, or that appears in ``DISTRIBUTION_ALIASES``, is
    therefore never treated as first-party: a name the repo pays for from a
    registry is a dependency no matter what a local file is called.

    Test files are excluded from the index as well as from the scan. Otherwise
    the test directories ``tests/kafka/`` and ``tests/redis/`` would register
    ``kafka`` and ``redis`` as first-party packages for two RTVI services.
    """
    all_paths = set(paths)
    package_dirs = {
        p[: -len("/__init__.py")] for p in all_paths if p.endswith("/__init__.py")
    }
    by_module: dict[str, set[str]] = defaultdict(set)
    for path in all_paths:
        if not path.endswith(suffix) or is_vendored(path) or is_test_path(path):
            continue
        module = owning_module(path)
        # Directory names are always first-party: a directory in this tree is
        # code we wrote, and `spatialai_data_utils/` stays first-party even
        # though another service happens to depend on the built wheel.
        directory_names: set[str] = set()
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        while directory:
            directory_names.add(directory.rsplit("/", 1)[-1])
            parent = directory.rsplit("/", 1)[0] if "/" in directory else ""
            if directory in package_dirs and parent in package_dirs:
                directory = parent
                continue
            break
        by_module[module].update(n for n in directory_names if n)
        # A leaf filename is the shadowing risk, so `protected` applies only
        # here — `vss_core/vlm/openai.py` must not mask `import openai`.
        leaf = path.rsplit("/", 1)[-1][: -len(suffix)]
        if leaf and _normalize(leaf) not in protected:
            by_module[module].add(leaf)
    return dict(by_module)


def node_first_party_names(
    ref: str, read: Callable[[str, str], bytes | None], paths: Iterable[str]
) -> set[str]:
    """npm package names this repo publishes itself, from every package.json.

    The UI is an npm workspace: ``@nv-metropolis-bp-vss-ui/dashboard`` and
    ``vst-streaming-lib`` are directories in this tree, not registry packages.
    Reporting them as undeclared third-party dependencies would be wrong in
    both directions — wrong package, and no license to review.
    """
    names: set[str] = set()
    for path in paths:
        if is_vendored(path) or path.rsplit("/", 1)[-1] != "package.json":
            continue
        data = read(ref, path)
        if data is None:
            continue
        try:
            name = json.loads(data.decode("utf-8", errors="replace")).get("name")
        except (ValueError, AttributeError):
            continue
        if isinstance(name, str) and name:
            names.add(name)
    return names


# --------------------------------------------------------------------------
# Declared-name matching
# --------------------------------------------------------------------------


def _normalize(name: str) -> str:
    """PEP 503-ish key: case- and separator-insensitive."""
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def candidate_distributions(language: str, name: str) -> list[str]:
    """Distribution names that would satisfy this import, most specific first.

    ``import cv2`` is satisfied by ``opencv-python``; ``import sse_starlette``
    by ``sse-starlette``. Checking only the literal import name would report
    both as gaps even though the lockfile pins them.
    """
    candidates: list[str] = []
    if language == "python":
        # Try the full dotted name first (`google.protobuf` -> protobuf), then
        # its head (`mpl_toolkits.mplot3d` -> mpl_toolkits -> matplotlib).
        for key in (name, name.split(".")[0]):
            alias = DISTRIBUTION_ALIASES.get(key)
            if alias:
                candidates.append(alias)
                break
    candidates.append(name)
    if "_" in name:
        candidates.append(name.replace("_", "-"))
    if "." in name and language == "python":
        candidates.append(name.replace(".", "-"))
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def distribution_for(language: str, name: str) -> str:
    """Distribution name a row should carry for this import name.

    Rows are keyed on this, not on the import name. `matplotlib` and
    `mpl_toolkits.mplot3d` are two spellings of one wheel, and keying on the
    spelling emitted two identical `matplotlib` rows for
    tools/sdg-postprocessing in the first full-pipeline run.
    """
    if language != "python":
        return name
    for key in (name, name.split(".")[0]):
        if key in DISTRIBUTION_ALIASES:
            return DISTRIBUTION_ALIASES[key]
    return name


def _is_declared(language: str, name: str, declared: set[str]) -> bool:
    """True when `declared` (already normalized) satisfies this import.

    C/C++ is exempt and always reports. A vendored third-party tree committed
    under `3rdparty/` has no lockfile entry to match against — it is covered by
    an attribution file, if at all — so "not in a manifest" is its normal
    state, not a signal. Matching it against manifest names is also actively
    harmful across ecosystems: `services/vios` pins the PyPI package `minio`
    in a Python lockfile, which silently suppressed the row for the vendored
    C++ `minio-cpp` headers until this exemption existed.
    """
    if language == "c":
        return False
    if language == "java":
        # The group-id heuristic is approximate, so match on tokens: an import
        # of `com.google.gson` is satisfied by the coordinate
        # `com.google.code.gson:gson`, which shares no prefix with it.
        tokens = {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t}
        distinctive = name.lower().rsplit(".", 1)[-1]
        for entry in declared:
            entry_tokens = {t for t in re.split(r"[^a-z0-9]+", entry) if t}
            if distinctive in entry_tokens or tokens <= entry_tokens:
                return True
        return False
    for candidate in candidate_distributions(language, name):
        key = _normalize(candidate)
        if key in declared:
            return True
        if key in DECLARED_FAMILY_PREFIXES and any(
            entry.startswith(key + "-") for entry in declared
        ):
            return True
    return False


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------


def _report_only_row(
    *,
    language: str,
    package: str,
    module: str,
    source_file: str,
    notes: str,
    license_expr: str = "",
    repository_url: str = "",
) -> dict[str, str]:
    """Build one usage row. THE ONLY ROW CONSTRUCTOR IN THIS MODULE.

    `change` and `source_kind` are literals in this body and are not
    parameters. That is the whole mechanism that makes the use-side pass
    incapable of failing the job: there is no argument to get wrong, no default
    to override, and no code path here that writes any other `change` value.
    Adding one would be a visible edit to this function, not a caller mistake.
    """
    return make_row(
        language=language,
        package=package,
        change=CHANGE_USED_UNDECLARED,
        old_version="",
        new_version="",
        old_license="",
        new_license=license_expr,
        repository_url=repository_url,
        notes=notes,
        source_kind=SOURCE_KIND_USAGE,
        source_file=source_file,
        module=module,
        risk=license_risk(license_expr),
    )


def counts_toward_failure(row: Mapping[str, str]) -> bool:
    """False for every row this module produces.

    Exported so the orchestrator filters on a predicate that lives next to the
    guarantee, instead of re-deriving `row["change"] != "USED_UNDECLARED"` at
    the call site where a typo fails open and arms the gate on advisory rows.
    """
    return not (
        row.get("change") == CHANGE_USED_UNDECLARED
        and row.get("source_kind") == SOURCE_KIND_USAGE
    )


def _note(parts: Iterable[str]) -> str:
    return "; ".join(p for p in parts if p)


def undeclared(
    ref: str,
    read: Callable[[str, str], bytes | None],
    source_paths: Iterable[str],
    declared_by_module: Mapping[str, Iterable[str]],
    *,
    license_lookup: Callable[[str], Mapping[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Rows for third-party names the source reaches that its module never declares.

    `source_paths` must be the COMPLETE listing of the tree at `ref` (what
    ``git ls-tree -r --name-only`` prints), not just the PR's changed files:
    the first-party index and the vendored include resolver are both built from
    it, and a partial listing makes every intra-service import look
    third-party.

    `declared_by_module` maps an owning module to the package names its own
    manifests declare. Comparison is per module and never falls back to the
    union: ``services/video-summarization`` reaching ``fastapi`` is a real gap
    even though ``services/agent`` locks ``fastapi``, because the two ship as
    separate containers and OSRB reviews per shipped artifact. When another
    module does declare the name, the row says so in `notes` instead of being
    dropped — that sentence is the reviewer's shortcut to "copy the entry
    across", and suppressing the row would hide the gap.

    `license_lookup` is optional and OFF by default. Resolving licenses would
    mean a few hundred PyPI round-trips for a report-only pass; a network
    wobble would then slow or flake a gate whose rows cannot fail the job
    anyway. Without it every usage row carries risk=Unknown, which is the
    conservative reading and tells OSRB to look.
    """
    paths = list(source_paths)
    vendored_index = VendoredIndex(paths)

    normalized_declared: dict[str, set[str]] = {
        module: {_normalize(n) for n in names}
        for module, names in declared_by_module.items()
    }
    declaring_modules: dict[str, set[str]] = defaultdict(set)
    for module, names in normalized_declared.items():
        for name in names:
            declaring_modules[name].add(module)

    js_first_party = node_first_party_names(ref, read, paths)
    # Names no local file is allowed to shadow — see `first_party_names`.
    # Packages this repo publishes itself are removed again: services/ui
    # depends on a workspace package literally named `common`, and leaving it
    # in would stop `src/api_models/common.py` from masking `import common`
    # for two unrelated Python services.
    protected = (
        frozenset(declaring_modules)
        | {_normalize(n) for n in DISTRIBUTION_ALIASES}
    ) - {_normalize(n) for n in js_first_party}
    py_first_party = first_party_names(paths, ".py", protected)
    rb_first_party = first_party_names(paths, ".rb", protected)

    # (module, language, package) -> evidence
    hits: dict[tuple[str, str, str], dict[str, object]] = {}

    def record(
        module: str,
        language: str,
        name: str,
        path: str,
        lineno: int,
        *,
        degraded: bool = False,
        detail: str = "",
    ) -> None:
        distribution = distribution_for(language, name)
        key = (module, language, distribution)
        entry = hits.get(key)
        if entry is None:
            entry = {
                "files": 0,
                "source_file": f"{path}#L{lineno}",
                "degraded": False,
                "detail": detail,
                "imported_as": set(),
            }
            hits[key] = entry
        entry["files"] = int(entry["files"]) + 1
        entry["degraded"] = bool(entry["degraded"]) or degraded
        if detail and not entry["detail"]:
            entry["detail"] = detail
        if name != distribution:
            cast_set = entry["imported_as"]
            assert isinstance(cast_set, set)
            cast_set.add(name)

    for path in sorted(paths):
        language = source_language(path)
        if language is None:
            continue
        data = read(ref, path)
        if data is None:
            continue
        module = owning_module(path)

        if language == "python":
            located, degraded = _python_imports_located(data)
            first_party = py_first_party.get(module, set())
            for name, lineno in located.items():
                if name.split(".")[0] in first_party:
                    continue
                record(module, "python", name, path, lineno, degraded=degraded)
        elif language == "node":
            for name, lineno in _js_imports_located(data).items():
                if name in js_first_party:
                    continue
                record(module, "node", name, path, lineno)
        elif language == "c":
            for spec, lineno in _c_includes_located(data).items():
                resolved = vendored_include_package(spec, vendored_index)
                if resolved is None:
                    continue
                package, target = resolved
                record(
                    module,
                    "c",
                    package,
                    path,
                    lineno,
                    detail=f"#include \"{spec}\" resolves to {target}",
                )
        elif language in ("java", "ruby"):
            first_party = rb_first_party.get(module, set()) if language == "ruby" else set()
            for name, lineno in _jvm_ruby_located(data, language).items():
                if name in first_party:
                    continue
                record(module, language, name, path, lineno)

    rows: list[dict[str, str]] = []
    for (module, language, distribution), entry in sorted(hits.items()):
        declared = normalized_declared.get(module, set())
        imported_as = sorted(entry["imported_as"])  # type: ignore[arg-type]
        # A row can be reached under several spellings (`matplotlib` and
        # `mpl_toolkits.mplot3d`); any one of them being declared closes it.
        if any(
            _is_declared(language, spelling, declared)
            for spelling in {distribution, *imported_as}
        ):
            continue
        # A C package name that collides with a PyPI/npm name says nothing
        # useful ("minio-cpp is declared by services/agent" is not true), so
        # the cross-module hint is Python/Node/JVM/Ruby only.
        elsewhere = (
            sorted(
                {
                    other
                    for spelling in [distribution, *imported_as]
                    for candidate in candidate_distributions(language, spelling)
                    for other in declaring_modules.get(_normalize(candidate), ())
                    if other != module
                }
            )
            if language != "c"
            else []
        )
        if len(elsewhere) > 3:
            elsewhere = elsewhere[:3] + [f"and {len(elsewhere) - 3} more"]
        license_expr = ""
        repository_url = ""
        if license_lookup is not None:
            meta = license_lookup(distribution) or {}
            license_expr = str(meta.get("license", ""))
            repository_url = str(meta.get("repository_url", ""))
        file_count = int(entry["files"])
        notes = _note(
            [
                "imported as " + ", ".join(f"`{n}`" for n in imported_as)
                if imported_as
                else "",
                f"reached by {file_count} source file(s) in {module}",
                "no manifest in this module declares it",
                (
                    "declared only by " + ", ".join(elsewhere) + " — a separate "
                    "shipped artifact, so this module is still uncovered"
                )
                if elsewhere
                else "",
                "vendored third-party source reached from first-party code; "
                "no language manifest can declare it"
                if language == "c"
                else "",
                str(entry["detail"]),
                "AST parse failed; names recovered by regex, may include "
                "relative imports"
                if entry["degraded"]
                else "",
            ]
        )
        rows.append(
            _report_only_row(
                language=language,
                package=distribution,
                module=module,
                source_file=str(entry["source_file"]),
                notes=notes,
                license_expr=license_expr,
                repository_url=repository_url,
            )
        )

    # Structural guarantee, re-checked before the rows escape this module. If a
    # future edit makes a usage row look like a declared-side change, this
    # raises here rather than silently arming the gate on heuristic evidence.
    for row in rows:
        assert row["change"] == CHANGE_USED_UNDECLARED, row
        assert row["source_kind"] == SOURCE_KIND_USAGE, row
        assert not counts_toward_failure(row), row

    _log(
        f"{len(rows)} use-side rows (report-only) across "
        f"{len({r['module'] for r in rows})} modules"
    )
    return rows
