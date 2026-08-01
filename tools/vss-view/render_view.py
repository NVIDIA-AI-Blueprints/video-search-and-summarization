#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Render a VSS view spec into one self-contained HTML file.

The spec is a small JSON document (see ``view_spec.schema.json``) naming
whitelisted blocks and where their rows come from. An agent produces the spec;
this script produces the markup. Nothing here executes agent-authored code.

Usage:
    render_view.py --spec spec.json --out /tmp/alerts.html
    cat spec.json | render_view.py --out /tmp/alerts.html
    render_view.py --spec spec.json --out out.html --inline-media

Stdlib only — no third-party dependency, no build step.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

TEMPLATE = Path(__file__).with_name("template.html")

BLOCK_TYPES = {"summary", "incident_list", "media_grid", "timeline"}
COLUMNS = {"time", "sensor", "category", "description", "verdict", "media"}

# Thumbnails worth inlining. Anything larger stays a link — a 40 MB data URI
# helps nobody.
MAX_INLINE_BYTES = 2 * 1024 * 1024


class SpecError(ValueError):
    """The spec is not renderable. The message names the offending path."""


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _require(cond: bool, where: str, msg: str) -> None:
    if not cond:
        raise SpecError(f"{where}: {msg}")


def validate(spec: dict) -> None:
    """Check the spec far enough that the renderer cannot be handed junk.

    Deliberately hand-rolled: this must run on a bare host with no ``jsonschema``
    installed, the same constraint the skills' other scripts work under.
    """
    _require(isinstance(spec, dict), "spec", "must be a JSON object")
    _require(isinstance(spec.get("title"), str) and spec["title"].strip(),
             "spec.title", "required, non-empty string")

    blocks = spec.get("blocks")
    _require(isinstance(blocks, list) and blocks, "spec.blocks", "required, non-empty array")

    theme = spec.get("theme", "dark")
    _require(theme in ("dark", "light"), "spec.theme", "must be 'dark' or 'light'")

    for i, block in enumerate(blocks):
        at = f"spec.blocks[{i}]"
        _require(isinstance(block, dict), at, "must be an object")
        btype = block.get("type")
        _require(btype in BLOCK_TYPES,
                 f"{at}.type", f"must be one of {sorted(BLOCK_TYPES)}, got {btype!r}")

        if btype == "summary":
            items = block.get("items")
            _require(isinstance(items, list) and items, f"{at}.items", "required for summary")
            for j, item in enumerate(items):
                _require(isinstance(item, dict) and isinstance(item.get("label"), str),
                         f"{at}.items[{j}].label", "required string")

        cols = block.get("columns")
        if cols is not None:
            _require(isinstance(cols, list) and cols, f"{at}.columns", "must be a non-empty array")
            bad = [c for c in cols if c not in COLUMNS]
            _require(not bad, f"{at}.columns", f"unknown columns {bad}; allowed {sorted(COLUMNS)}")

        src = block.get("source")
        if src is None:
            # Only summary can stand alone on literal values.
            _require(btype == "summary", f"{at}.source", f"required for {btype}")
            continue
        _validate_source(src, f"{at}.source")


def _validate_source(src: dict, at: str) -> None:
    _require(isinstance(src, dict), at, "must be an object")
    mode = src.get("mode")
    _require(mode in ("inline", "poll"), f"{at}.mode", "must be 'inline' or 'poll'")

    if mode == "inline":
        items = src.get("items")
        _require(isinstance(items, list), f"{at}.items", "required array for inline mode")
        for j, item in enumerate(items):
            _require(isinstance(item, dict), f"{at}.items[{j}]", "must be an object")
        return

    url = src.get("url")
    _require(isinstance(url, str) and url, f"{at}.url", "required for poll mode")
    scheme = urlparse(url).scheme.lower()
    _require(scheme in ("http", "https"), f"{at}.url",
             f"must be http(s), got {scheme or 'no scheme'}")

    iv = src.get("interval_ms", 5000)
    _require(isinstance(iv, int) and iv >= 1000, f"{at}.interval_ms",
             "must be an integer >= 1000 (do not hammer the deployment)")

    params = src.get("params")
    if params is not None:
        _require(isinstance(params, dict), f"{at}.params", "must be an object")


# --------------------------------------------------------------------------
# Media inlining
# --------------------------------------------------------------------------

def _fetch_data_uri(url: str, timeout: float) -> str | None:
    if not urlparse(url).scheme.lower() in ("http", "https"):
        return None
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310 — scheme checked above
            if resp.status != 200:
                return None
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            raw = resp.read(MAX_INLINE_BYTES + 1)
    except Exception as exc:  # network/DNS/timeout — degrade to the plain link
        print(f"  ! inline skipped ({exc.__class__.__name__}): {url}", file=sys.stderr)
        return None

    if len(raw) > MAX_INLINE_BYTES:
        print(f"  ! inline skipped (>{MAX_INLINE_BYTES // 1024}KB): {url}", file=sys.stderr)
        return None
    if not ctype.startswith("image/"):
        guessed = mimetypes.guess_type(url)[0] or ""
        if not guessed.startswith("image/"):
            return None
        ctype = guessed
    return f"data:{ctype};base64," + base64.b64encode(raw).decode("ascii")


def _dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _put(obj, path: str, value) -> None:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def inline_media(spec: dict, timeout: float) -> int:
    """Rewrite inline-source thumbnails to base64 data URIs.

    Only ``inline`` sources are touched: a poll source fetches fresh rows in the
    browser, so anything baked in here would be overwritten on the first tick.
    """
    count = 0
    for block in spec.get("blocks", []):
        src = block.get("source") or {}
        if src.get("mode") != "inline":
            continue
        fields = block.get("fields") or {}
        default = "info.imagePath" if block.get("type") == "incident_list" else "thumbnail"
        path = fields.get("thumbnail", default)
        cache: dict[str, str] = {}
        for item in src.get("items", []):
            url = _dig(item, path)
            if not isinstance(url, str) or url.startswith("data:"):
                continue
            if url not in cache:
                data_uri = _fetch_data_uri(url, timeout)
                if not data_uri:
                    continue
                cache[url] = data_uri
            _put(item, path, cache[url])
            count += 1
    return count


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------

def _embed(spec: dict) -> str:
    """Serialize the spec for a <script type="application/json"> block.

    ``</script>`` inside the payload would end the element early, so every angle
    bracket and ampersand goes out as a \\u escape. JSON parses these back
    identically.
    """
    return (json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def render(spec: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    title = spec.get("title", "VSS view")
    return (template
            .replace("__VSS_THEME__", spec.get("theme", "dark"))
            .replace("__VSS_TITLE__", (title.replace("&", "&amp;")
                                            .replace("<", "&lt;")
                                            .replace(">", "&gt;")))
            .replace("__VSS_SPEC__", _embed(spec)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render a VSS view spec into a self-contained HTML file.")
    ap.add_argument("--spec", default="-",
                    help="Path to the spec JSON, or '-' for stdin (default).")
    ap.add_argument("--out", required=True, help="Output .html path.")
    ap.add_argument("--inline-media", action="store_true",
                    help="Fetch inline-source thumbnails and embed them as data URIs, "
                         "so the file survives the deployment going away.")
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="Per-image fetch timeout for --inline-media (seconds).")
    ap.add_argument("--theme", choices=["dark", "light"],
                    help="Override the spec's theme.")
    args = ap.parse_args(argv)

    try:
        raw = sys.stdin.read() if args.spec == "-" else Path(args.spec).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read spec: {exc}", file=sys.stderr)
        return 2

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: spec is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if args.theme:
        spec["theme"] = args.theme

    try:
        validate(spec)
    except SpecError as exc:
        print(f"error: invalid spec — {exc}", file=sys.stderr)
        return 2

    spec.setdefault("generated_at",
                    datetime.now(timezone.utc).replace(microsecond=0).isoformat())

    if args.inline_media:
        n = inline_media(spec, args.timeout)
        print(f"inlined {n} image(s)", file=sys.stderr)

    out = Path(args.out)
    try:
        if out.parent != Path(""):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(spec), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 2

    live = sum(1 for b in spec.get("blocks", [])
               if (b.get("source") or {}).get("mode") == "poll")
    kind = f"{live} live block(s)" if live else "static snapshot"
    print(f"{out.resolve()}  ({kind})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
