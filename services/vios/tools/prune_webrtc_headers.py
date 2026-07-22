#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prune vendored WebRTC headers to the VIOS include closure.

The WebRTC upgrade branch imported a full source-style header tree under
src/framework/webrtc_streamer/inc/webrtc_headers/src.  VIOS only compiles
against a small subset of those headers because libwebrtc itself is prebuilt.

This script keeps every vendored header directly included by VIOS sources,
every vendored header already recorded in compiler .d files, and the recursive
#include closure of those headers.  Everything else in the vendored header tree
can be removed with --delete.  When --source-header-root and --sync-from-source
are provided, the keep-set is resolved against a WebRTC source checkout and the
matching headers are copied into the VIOS vendored header tree before pruning.

Use --keep-public-roots for a conservative audit that retains entire public
third-party include roots.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, deque
from pathlib import Path


INCLUDE_LINE_RE = re.compile(r"^\s*#\s*include\s*([<\"])([^>\"]+)[>\"]")
DIRECTIVE_RE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)")
DEP_RE = re.compile(
    r"(?:/root|[A-Za-z0-9_./+-]+)?/?src/framework/webrtc_streamer/inc/"
    r"webrtc_headers/src/([^:\s\\]+)"
)
HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".inc"}
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
ENABLED_MACROS = {
    "ABSL_HAVE_INTRINSIC_INT128",
    "WEBRTC_LINUX",
    "WEBRTC_POSIX",
}
DISABLED_MACROS = {
    "RTC_ENABLE_H265",
    "WEBRTC_ABSL_MUTEX",
    "WEBRTC_WIN",
}
PUBLIC_INCLUDE_DIRS = (
    "third_party/abseil-cpp/absl",
    "third_party/boringssl/src/include",
    "third_party/jsoncpp/generated",
    "third_party/jsoncpp/source/include",
    "third_party/libyuv/include",
)


def vios_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def clean_include_name(name: str) -> str:
    name = name.strip()
    for prefix in (
        "webrtc_headers/src/",
        "third_party/webrtc/",
        "external/webrtc/webrtc/",
    ):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


def evaluate_condition(kind: str, expression: str) -> bool | None:
    expression = expression.strip()
    all_known_macros = ENABLED_MACROS | DISABLED_MACROS
    for macro in all_known_macros:
        macro_enabled = macro in ENABLED_MACROS
        if kind == "ifdef" and expression == macro:
            return macro_enabled
        if kind == "ifndef" and expression == macro:
            return not macro_enabled
        if kind in {"if", "elif"}:
            if expression in {macro, f"defined({macro})", f"defined {macro}"}:
                return macro_enabled
            if expression in {
                f"!{macro}",
                f"!defined({macro})",
                f"!defined {macro}",
                f"not defined({macro})",
            }:
                return not macro_enabled
    return None


def iter_active_includes(text: str) -> list[tuple[bool, str]]:
    includes: list[tuple[bool, str]] = []
    stack: list[dict[str, bool]] = [
        {"parent_active": True, "active": True, "branch_taken": False}
    ]

    for line in text.splitlines():
        directive = DIRECTIVE_RE.match(line)
        if directive:
            kind = directive.group(1)
            expression = directive.group(2)

            if kind in {"ifdef", "ifndef", "if"}:
                parent_active = stack[-1]["active"]
                condition = evaluate_condition(kind, expression)
                active = parent_active if condition is None else parent_active and condition
                stack.append(
                    {
                        "parent_active": parent_active,
                        "active": active,
                        "branch_taken": False if condition is None else condition,
                    }
                )
                continue

            if kind == "elif" and len(stack) > 1:
                frame = stack[-1]
                condition = evaluate_condition(kind, expression)
                if frame["branch_taken"]:
                    frame["active"] = False
                elif condition is None:
                    frame["active"] = frame["parent_active"]
                    frame["branch_taken"] = True
                else:
                    frame["active"] = frame["parent_active"] and condition
                    frame["branch_taken"] = condition
                continue

            if kind == "else" and len(stack) > 1:
                frame = stack[-1]
                frame["active"] = frame["parent_active"] and not frame["branch_taken"]
                frame["branch_taken"] = True
                continue

            if kind == "endif" and len(stack) > 1:
                stack.pop()
                continue

        if not stack[-1]["active"]:
            continue

        include = INCLUDE_LINE_RE.match(line)
        if include:
            includes.append((include.group(1) == '"', include.group(2)))

    return includes


class Resolver:
    def __init__(self, header_root: Path) -> None:
        self.header_root = header_root
        self.include_roots = [
            header_root,
            header_root / "third_party/abseil-cpp",
            header_root / "third_party/boringssl/src/include",
            header_root / "third_party/jsoncpp/source/include",
            header_root / "third_party/jsoncpp/generated",
            header_root / "third_party/libyuv/include",
        ]

    def resolve(self, include_name: str, including_file: Path | None, quoted: bool) -> Path | None:
        include_name = clean_include_name(include_name)
        candidates: list[Path] = []

        if quoted and including_file and is_under(including_file, self.header_root):
            candidates.append(including_file.parent / include_name)

        for include_root in self.include_roots:
            candidates.append(include_root / include_name)

        for candidate in candidates:
            if not is_under(candidate, self.header_root):
                continue
            if candidate.is_file():
                return candidate.resolve()
        return None


def iter_source_files(vios_root: Path, header_root: Path) -> list[Path]:
    source_roots = [vios_root / "src", vios_root / "test"]
    files: list[Path] = []
    for source_root in source_roots:
        if not source_root.is_dir():
            continue
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            if is_under(path, header_root):
                continue
            files.append(path.resolve())
    return files


def iter_header_files(header_root: Path) -> list[Path]:
    return [
        path.resolve()
        for path in header_root.rglob("*")
        if path.is_file() and path.suffix in HEADER_SUFFIXES
    ]


def direct_roots_from_sources(
    source_files: list[Path],
    resolver: Resolver,
) -> tuple[set[Path], Counter[str]]:
    roots: set[Path] = set()
    unresolved: Counter[str] = Counter()

    for source_file in source_files:
        for quoted, include_name in iter_active_includes(read_text(source_file)):
            resolved = resolver.resolve(include_name, None, quoted)
            if resolved:
                roots.add(resolved)
            elif (
                include_name.startswith("webrtc_headers/src/")
                or include_name.startswith("third_party/webrtc/")
                or include_name.startswith("external/webrtc/webrtc/")
            ):
                unresolved[include_name] += 1

    return roots, unresolved


def roots_from_depfiles(vios_root: Path, header_root: Path) -> set[Path]:
    roots: set[Path] = set()
    for depfile in vios_root.rglob("*.d"):
        for match in DEP_RE.finditer(read_text(depfile)):
            candidate = (header_root / match.group(1)).resolve()
            if candidate.is_file() and is_under(candidate, header_root):
                roots.add(candidate)
    return roots


def public_include_roots(header_root: Path) -> set[Path]:
    roots: set[Path] = set()
    for dirname in PUBLIC_INCLUDE_DIRS:
        directory = header_root / dirname
        if not directory.is_dir():
            continue
        roots.update(
            path.resolve()
            for path in directory.rglob("*")
            if path.is_file() and path.suffix in HEADER_SUFFIXES
        )
    return roots


def include_closure(
    roots: set[Path],
    resolver: Resolver,
) -> tuple[set[Path], Counter[str]]:
    keep = set(roots)
    queue = deque(sorted(roots))
    unresolved: Counter[str] = Counter()

    while queue:
        header = queue.popleft()
        for quoted, include_name in iter_active_includes(read_text(header)):
            resolved = resolver.resolve(include_name, header, quoted)
            if not resolved:
                normalized = clean_include_name(include_name)
                if quoted and "/" not in include_name:
                    unresolved[f"{rel(header, resolver.header_root)} -> {include_name}"] += 1
                elif normalized.startswith(
                    (
                        "api/",
                        "audio/",
                        "base/",
                        "build/",
                        "call/",
                        "common_audio/",
                        "common_video/",
                        "logging/",
                        "json/",
                        "libyuv/",
                        "media/",
                        "modules/",
                        "net/",
                        "p2p/",
                        "pc/",
                        "rtc_base/",
                        "stats/",
                        "system_wrappers/",
                        "test/",
                        "third_party/",
                        "video/",
                    )
                ):
                    unresolved[include_name] += 1
                continue

            if resolved not in keep:
                keep.add(resolved)
                queue.append(resolved)

    return keep, unresolved


def delete_unneeded(unneeded: list[Path], header_root: Path) -> int:
    deleted = 0
    for path in unneeded:
        try:
            path.unlink()
            deleted += 1
        except FileNotFoundError:
            continue

    for dirpath, dirnames, _ in os.walk(header_root, topdown=False):
        path = Path(dirpath)
        if path == header_root:
            continue
        try:
            path.rmdir()
        except OSError:
            pass
        dirnames[:] = []

    return deleted


def sync_from_source(
    keep: set[Path], source_header_root: Path, target_header_root: Path
) -> tuple[int, int]:
    copied = 0
    unchanged = 0

    for source in sorted(keep):
        try:
            relative = source.relative_to(source_header_root)
        except ValueError:
            continue

        target = target_header_root / relative
        data = source.read_bytes()
        if target.is_file() and target.read_bytes() == data:
            unchanged += 1
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copied += 1

    return copied, unchanged


def print_counter(title: str, counter: Counter[str], limit: int) -> None:
    if not counter:
        return
    print(title)
    for name, count in counter.most_common(limit):
        print(f"  {count:4d} {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true", help="delete unneeded headers")
    parser.add_argument(
        "--no-depfiles",
        action="store_true",
        help="ignore existing compiler .d dependency files",
    )
    parser.add_argument(
        "--show-unneeded",
        type=int,
        default=0,
        metavar="N",
        help="print the first N headers that would be deleted",
    )
    parser.add_argument(
        "--keep-public-roots",
        action="store_true",
        help="keep all headers in public third-party include roots",
    )
    parser.add_argument(
        "--source-header-root",
        type=Path,
        help="resolve required headers from this WebRTC source root",
    )
    parser.add_argument(
        "--sync-from-source",
        action="store_true",
        help="copy required headers from --source-header-root before pruning",
    )
    args = parser.parse_args()

    vios_root = vios_root_from_script()
    target_header_root = (
        vios_root / "src/framework/webrtc_streamer/inc/webrtc_headers/src"
    ).resolve()
    source_header_root = (
        args.source_header_root.resolve()
        if args.source_header_root
        else target_header_root
    )
    if not target_header_root.is_dir():
        print(f"missing WebRTC header root: {target_header_root}", file=sys.stderr)
        return 2
    if not source_header_root.is_dir():
        print(f"missing source WebRTC header root: {source_header_root}", file=sys.stderr)
        return 2
    if args.sync_from_source and source_header_root == target_header_root:
        print("--sync-from-source requires a distinct --source-header-root", file=sys.stderr)
        return 2

    resolver = Resolver(source_header_root)
    source_all_headers = set(iter_header_files(source_header_root))
    source_files = iter_source_files(vios_root, target_header_root)
    source_roots, source_unresolved = direct_roots_from_sources(source_files, resolver)
    dep_roots = (
        set() if args.no_depfiles else roots_from_depfiles(vios_root, source_header_root)
    )
    public_roots = (
        public_include_roots(source_header_root) if args.keep_public_roots else set()
    )
    keep, closure_unresolved = include_closure(source_roots | dep_roots | public_roots, resolver)
    keep &= source_all_headers

    target_keep = {
        (target_header_root / path.relative_to(source_header_root)).resolve()
        for path in keep
        if is_under(path, source_header_root)
    }
    target_headers_before_sync = set(iter_header_files(target_header_root))
    missing_before_sync = sorted(path for path in target_keep if not path.is_file())

    copied = 0
    unchanged = 0
    if args.sync_from_source:
        copied, unchanged = sync_from_source(keep, source_header_root, target_header_root)

    target_all_headers = set(iter_header_files(target_header_root))
    unneeded = sorted(target_all_headers - target_keep)

    print(f"VIOS WebRTC header root: {target_header_root}")
    print(f"Source WebRTC header root: {source_header_root}")
    print(f"VIOS source files scanned: {len(source_files)}")
    print(f"Source header files scanned: {len(source_all_headers)}")
    print(f"VIOS header files before sync: {len(target_headers_before_sync)}")
    print(f"Required headers missing before sync: {len(missing_before_sync)}")
    print(f"Direct roots from source: {len(source_roots)}")
    print(f"Roots from compiler deps: {len(dep_roots)}")
    print(f"Public include-root headers kept: {len(public_roots)}")
    print(f"Headers kept by include closure: {len(keep)}")
    print(f"Headers copied/updated from source: {copied}")
    print(f"Required headers already current: {unchanged}")
    print(f"VIOS header files before prune: {len(target_all_headers)}")
    print(f"Headers removable: {len(unneeded)}")
    print_counter("Unresolved direct WebRTC-like includes:", source_unresolved, 20)
    print_counter("Unresolved includes inside kept headers:", closure_unresolved, 30)

    if args.show_unneeded:
        print(f"First {min(args.show_unneeded, len(unneeded))} removable headers:")
        for path in unneeded[: args.show_unneeded]:
            print(f"  {rel(path, target_header_root)}")

    if args.delete:
        deleted = delete_unneeded(unneeded, target_header_root)
        print(f"Deleted headers: {deleted}")
    else:
        print("Dry run only; pass --delete to prune files.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
