# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
HTML -> PDF sanity report (Format A), rendered by headless Chromium (Playwright).
Layout: a generic title above a green rule, then per plan -- the plan name, a
Deployment Configuration + Run Summary block, a results matrix, per-group latency
tables, and evidence grouped by category (Download / Picture / WebRTC), 2-up.
"""
from __future__ import annotations

import base64
import html
import logging
import os
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

from sanity_common import SanityContext, UseCaseResult, REPO_ROOT

logger = logging.getLogger("sanity.report")
# PDF is rendered with Playwright's Chromium. By default let Playwright use the browser it
# installed (`playwright install chromium`); set VIOS_SANITY_CHROME to pin a specific binary.
CHROME = os.environ.get("VIOS_SANITY_CHROME", "")

GREEN = "#76b900"
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
# evidence category order + display names (keyed by UseCaseResult.group)
_GROUPS = [("download", "Download"), ("picture", "Picture"), ("webrtc", "WebRTC")]
_GATE_NAMES = {"setup": "Provisioning", "download": "Download", "picture": "Picture",
               "webrtc": "WebRTC + video-wall", "perf": "Latency perf"}


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _data_uri(path: Path) -> str:
    try:
        mime = _MIME.get(Path(path).suffix.lower(), "image/png")
        return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except Exception as e:  # noqa: BLE001
        logger.warning("embed image failed %s: %s", path, e)
        return ""


def _sh(cmd, default="") -> str:
    try:
        out = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=8)
        return (out.stdout or "").strip() or default
    except Exception:  # noqa: BLE001
        return default


def _env_meta(ctx: SanityContext) -> dict:
    gpu = ""
    try:
        gpu = (subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=8).stdout or "").strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        gpu = ""
    return {"branch": _sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], "?"),
            "commit": _sh(["git", "rev-parse", "--short", "HEAD"], "?"),
            "repo": str(REPO_ROOT), "host": _sh(["hostname"], ""), "gpu": gpu,
            "host_ip": ctx.host_ip}


def _hostify(url: str, host_ip: str) -> str:
    return (url or "").replace("localhost", host_ip).replace("127.0.0.1", host_ip)


def _link(url: str) -> str:
    return f'<a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url)}</a>'


def _play(r: UseCaseResult) -> str:
    """A single evidence link: prefer the mp4 (Play video), else the image."""
    vid = next((l for l in r.links if l.lower().endswith((".mp4", ".webm"))), None)
    if vid:
        return f'<a class="play" href="{_esc(vid)}" target="_blank" rel="noopener">&#9654; Play video</a>'
    img = next((l for l in r.links if l.lower().endswith((".jpg", ".jpeg", ".png"))), None)
    if img:
        return f'<a class="play" href="{_esc(img)}" target="_blank" rel="noopener">&#128279; Open image</a>'
    return ""


def _config_rows(meta: dict, pm: dict) -> str:
    host_ip = meta["host_ip"]
    base = _hostify(pm.get("base_url") or "", host_ip).rstrip("/") + "/vst/"
    nvs = _hostify(pm.get("nvstreamer") or "", host_ip)
    n_rtsp = len(pm.get("streams") or [])
    n_file = 1 if pm.get("file_sensor") else 0
    video_set = f"{n_rtsp} RTSP streams + {n_file} file sensor"
    rows = [("Host", _esc(meta["host"])), ("Host IP", _esc(host_ip)), ("GPU", _esc(meta["gpu"])),
            ("Git branch", _esc(meta["branch"])), ("Git commit", _esc(meta["commit"])),
            ("Repo path", _esc(meta["repo"])), ("Target", _esc(pm.get("target", "local"))),
            ("Base URL", _link(base)), ("Consumer", _esc(pm.get("consumer", ""))),
            ("NVStreamer", _link(nvs)), ("Video set", _esc(video_set))]
    return "".join(f'<tr><td>{k}</td><td class="mono">{v}</td></tr>' for k, v in rows)


def _gate_rows(rs: List[UseCaseResult]) -> str:
    def _url(r):
        return "_url[" in r.name or r.name.startswith(("download_url", "picture_url"))
    gates = [
        ("Provisioning", lambda r: r.group == "setup"),
        ("Download", lambda r: r.group == "download" and not _url(r)),
        ("Picture", lambda r: r.group == "picture" and not _url(r)),
        ("URL flows", _url),
        ("WebRTC + video-wall", lambda r: r.group == "webrtc"),
        ("Perf", lambda r: r.group == "perf"),
    ]
    out = []
    for name, pred in gates:
        sel = [r for r in rs if pred(r)]
        if not sel:
            continue
        ok = all(r.status != "FAIL" for r in sel)
        out.append(f'<tr><td class="c {"pass" if ok else "fail"}">{"&#10003;" if ok else "&#10007;"}</td>'
                   f'<td>{_esc(name)}</td></tr>')
    return "".join(out)


def _matrix_rows(rs: List[UseCaseResult]) -> str:
    out = []
    for r in rs:
        cls = r.status.lower()
        out.append(f'<tr><td>{_esc(r.name)}</td><td class="mono muted">{_esc(r.group)}</td>'
                   f'<td class="c {cls}">{_esc(r.status)}</td><td class="c">{r.duration_s:.1f}s</td>'
                   f'<td class="muted">{_esc(r.detail[:82])}</td></tr>')
    return "".join(out)


def _perf_tables(rs: List[UseCaseResult]) -> str:
    blocks = []
    for r in rs:
        if r.group != "perf" or not r.metrics:
            continue
        title = r.name.split("[")[0]
        grp = r.name.split("group=")[-1].split(",")[0].split("]")[0] if "group=" in r.name else ""
        rows = []
        for variant, m in r.metrics.items():
            avg = "-" if m.get("avg_ms") is None else f'{m["avg_ms"]} ms'
            lo = "-" if m.get("min_ms") is None else f'{m.get("min_ms")}'
            hi = "-" if m.get("max_ms") is None else f'{m.get("max_ms")}'
            ov = "ov" if "overlay" in variant else ""
            rows.append(f'<tr class="{ov}"><td>{_esc(variant)}</td><td class="mono">{avg}</td>'
                        f'<td class="mono muted">{lo}</td><td class="mono muted">{hi}</td>'
                        f'<td class="c">{m.get("ok", 0)}/{m.get("n", 0)}</td></tr>')
        blocks.append(f'<div class="pcol"><h4>{_esc(grp or title)} &mdash; avg latency</h4>'
                      f'<table class="perf"><thead><tr><th>Variant</th><th>Avg</th><th>Min</th>'
                      f'<th>Max</th><th>Runs</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    return f'<div class="cols">{"".join(blocks)}</div>' if blocks else ""


def _group_of(r: UseCaseResult) -> str:
    """The evidence bucket for a result: prefer the explicit group, else derive it
    from the use-case name so non-overlay rtsp snapshots (and any result whose
    group is unset) still land in Download / Picture / WebRTC."""
    if r.group in ("download", "picture", "webrtc"):
        return r.group
    n = r.name or ""
    if n.startswith("picture"):          # picture[...] and picture_url[...]
        return "picture"
    if n.startswith("download"):         # download[...] and download_url[...]
        return "download"
    if n.startswith(("webrtc", "video_wall")):
        return "webrtc"
    return r.group


def _wants_evidence(r: UseCaseResult) -> bool:
    """Evidence gallery is OVERLAY-only: only the flagged overlay-matrix cases
    (evidence=True) render. Non-overlay functional cases (download[...]/picture[...]/
    webrtc_play/url flows) are NOT shown -- they belong in the results matrix only."""
    return bool(r.evidence)


def _evidence(rs: List[UseCaseResult]) -> str:
    sections = []
    for gkey, gname in _GROUPS:
        cards = []
        for r in rs:
            if _group_of(r) != gkey or not (r.image or r.links):
                continue
            img = ""
            if r.image and Path(r.image).exists():
                uri = _data_uri(Path(r.image))
                if uri:
                    img = f'<img src="{uri}"/>'
            play = _play(r)
            if not (img or play):
                continue
            cards.append(f'<div class="card"><div class="ct">{_esc(r.name)} '
                         f'<span class="pill {r.status.lower()}">{_esc(r.status)}</span></div>'
                         f'<div class="cd">{_esc(r.detail)}</div>{play}{img}</div>')
        if cards:
            sections.append(f'<h4 class="evh">{_esc(gname)}</h4><div class="cards">{"".join(cards)}</div>')
    return "".join(sections)


def _arch_icon(name: str, a: str) -> str:
    """Return an inline-SVG icon glyph drawn in a local 28x28 box. `a` is the
    accent colour used for the small filled sub-shapes; the wrapping <g> in
    `_arch_box` supplies stroke=a / fill=none. Pure paths + basic shapes only,
    so every glyph renders offline in Chromium (no external images or fonts)."""
    if name == "camera":  # NVStreamer: video-camera body + lens + play triangle
        return ('<rect x="1" y="7" width="17" height="14" rx="2.5"/>'
                '<path d="M18,10.5 L26,6.5 L26,21.5 L18,17.5 Z"/>'
                f'<path d="M6,10.5 L11,14 L6,17.5 Z" fill="{a}" stroke="none"/>')
    if name == "chip":  # VIOS: CPU / server chip with pins on all four sides
        return ('<rect x="6.5" y="6.5" width="15" height="15" rx="1.5"/>'
                '<rect x="10.5" y="10.5" width="7" height="7" rx="1"/>'
                '<path d="M10,6.5 L10,3.5 M14,6.5 L14,3.5 M18,6.5 L18,3.5 '
                'M10,21.5 L10,24.5 M14,21.5 L14,24.5 M18,21.5 L18,24.5 '
                'M6.5,10 L3.5,10 M6.5,14 L3.5,14 M6.5,18 L3.5,18 '
                'M21.5,10 L24.5,10 M21.5,14 L24.5,14 M21.5,18 L24.5,18"/>')
    if name == "gear":  # Overlay Plugin: cog (generator) + a small bbox glyph
        return ('<path d="M15,14 L17.2,14 M13.54,17.54 L15.09,19.09 '
                'M10,19 L10,21.2 M6.46,17.54 L4.91,19.09 M5,14 L2.8,14 '
                'M6.46,10.46 L4.91,8.91 M10,9 L10,6.8 M13.54,10.46 L15.09,8.91"/>'
                '<circle cx="10" cy="14" r="5"/>'
                '<circle cx="10" cy="14" r="1.9"/>'
                '<rect x="18" y="9" width="9" height="10" rx="1" stroke-dasharray="2.2 1.6"/>'
                f'<circle cx="22.5" cy="14" r="1.1" fill="{a}" stroke="none"/>')
    if name == "broker":  # Message Broker: envelope + flowing queue dots
        return ('<rect x="3.5" y="8" width="15.5" height="11.5" rx="1.6"/>'
                '<path d="M3.5,9 L11.25,15 L19,9"/>'
                f'<circle cx="22" cy="13.7" r="1.1" fill="{a}" stroke="none"/>'
                f'<circle cx="25.2" cy="13.7" r="1.1" fill="{a}" stroke="none"/>')
    if name == "database":  # Fake ES: classic stacked-cylinder database drum
        return ('<ellipse cx="14" cy="7" rx="9" ry="3.2"/>'
                '<path d="M5,7 L5,20.5 C5,22.3 9,23.7 14,23.7 '
                'C19,23.7 23,22.3 23,20.5 L23,7"/>'
                '<path d="M5,12 C5,13.8 9,15.2 14,15.2 C19,15.2 23,13.8 23,12"/>'
                '<path d="M5,16.3 C5,18.1 9,19.5 14,19.5 C19,19.5 23,18.1 23,16.3"/>')
    if name == "doc":  # sample-metadata payload: a page with folded corner + lines
        return ('<path d="M6,3 L16,3 L21,8 L21,25 L6,25 Z"/>'
                '<path d="M16,3 L16,8 L21,8"/>'
                '<path d="M9,12.5 L18,12.5 M9,16 L18,16 M9,19.5 L15,19.5"/>')
    return ""


def _arch_box(x: int, y: int, title: str, sub: str, icon: str = "",
              w: int = 190, h: int = 82, fill: str = "#f7f9f2",
              stroke: str = GREEN, accent: str = "") -> str:
    accent = accent or stroke
    cx = x + w // 2
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.6" filter="url(#ds)"/>'
    ]
    if icon:
        parts.append(
            f'<g transform="translate({cx - 14},{y + 11})" fill="none" '
            f'stroke="{accent}" stroke-width="1.7" stroke-linecap="round" '
            f'stroke-linejoin="round">{icon}</g>')
    parts.append(
        f'<text x="{cx}" y="{y + 58}" text-anchor="middle" font-size="13" '
        f'font-weight="700" fill="#1a1a1a">{title}</text>')
    parts.append(
        f'<text x="{cx}" y="{y + 73}" text-anchor="middle" font-size="9" '
        f'fill="#555">{sub}</text>')
    return "".join(parts)


def _arch_diagram() -> str:
    """Inline SVG setup diagram rendered by Chromium (fully self-contained, no
    external assets). Left-to-right pipeline, each node fronted by a hand-drawn
    icon:
      NVStreamer -> VIOS (stream-processor) -> Overlay Plugin -> Fake ES
    with the Redis/Kafka message broker drawn on the *live* overlay-metadata
    path between the Overlay Plugin and VIOS (plugin publishes -> VIOS consumes),
    while replay metadata is written to Fake ES and read back by VIOS on
    replay/download (the dashed Fake ES -> VIOS return arrow beneath the row).
    """
    # per-node accent + soft print-friendly tint
    C_NV = ("#5f9400", "#f2f8e6")   # NVStreamer  (green)
    C_VI = ("#2a6fb0", "#eaf2fb")   # VIOS        (blue)
    C_OV = ("#7a4fd6", "#f2ecfc")   # Overlay     (violet)
    C_ES = ("#0f8a7a", "#e6f6f2")   # Fake ES     (teal)
    C_BR = ("#d99000", "#fff6e6")   # Broker      (amber)
    BLUE = "#2a6fb0"
    return (
        '<div class="arch">'
        '<svg viewBox="0 0 960 324" width="100%" preserveAspectRatio="xMidYMid meet" '
        'font-family="Arial, Helvetica, sans-serif">'
        '<defs>'
        '<marker id="ah" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
        '<path d="M0,0 L7,3 L0,6 Z" fill="#666"/></marker>'
        f'<marker id="ahg" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
        f'<path d="M0,0 L7,3 L0,6 Z" fill="{GREEN}"/></marker>'
        f'<marker id="ahb" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
        f'<path d="M0,0 L7,3 L0,6 Z" fill="{BLUE}"/></marker>'
        '<filter id="ds" x="-20%" y="-20%" width="140%" height="150%">'
        '<feDropShadow dx="0" dy="1.6" stdDeviation="1.6" '
        'flood-color="#000" flood-opacity="0.12"/></filter>'
        '</defs>'
        # --- legend (top-left, empty region left of the broker) ---
        '<g font-size="9">'
        f'<line x1="16" y1="26" x2="42" y2="26" stroke="{GREEN}" stroke-width="1.8" marker-end="url(#ahg)"/>'
        '<text x="48" y="29" fill="#555">broker publish (live)</text>'
        '<line x1="16" y1="44" x2="42" y2="44" stroke="#666" stroke-width="1.8" marker-end="url(#ah)"/>'
        '<text x="48" y="47" fill="#555">broker consume</text>'
        f'<line x1="16" y1="62" x2="42" y2="62" stroke="{BLUE}" stroke-width="1.8" '
        'stroke-dasharray="4 3" marker-end="url(#ahb)"/>'
        '<text x="48" y="65" fill="#555">Fake ES replay meta</text>'
        '</g>'
        # --- pipeline boxes (bottom row, y = 178..260) ---
        + _arch_box(12, 178, "NVStreamer", "RTSP / file source",
                    icon=_arch_icon("camera", C_NV[0]), fill=C_NV[1],
                    stroke=C_NV[0], accent=C_NV[0])
        + _arch_box(258, 178, "VIOS", "sensor + stream-processor",
                    icon=_arch_icon("chip", C_VI[0]), fill=C_VI[1],
                    stroke=C_VI[0], accent=C_VI[0])
        + _arch_box(512, 178, "Overlay Plugin", "DeepStream-sim / metadata_service",
                    icon=_arch_icon("gear", C_OV[0]), fill=C_OV[1],
                    stroke=C_OV[0], accent=C_OV[0])
        + _arch_box(756, 178, "Fake ES", "video_metadata_server",
                    icon=_arch_icon("database", C_ES[0]), fill=C_ES[1],
                    stroke=C_ES[0], accent=C_ES[0])
        # Fake ES annotation: it is the metadata store
        + f'<text x="851" y="170" text-anchor="middle" font-size="8.5" '
          f'font-style="italic" fill="{C_ES[0]}">metadata store</text>'
        # --- horizontal pipeline arrows + labels (box mid-line y = 219) ---
        + '<line x1="202" y1="219" x2="256" y2="219" stroke="#666" stroke-width="1.6" marker-end="url(#ah)"/>'
        + '<line x1="448" y1="219" x2="510" y2="219" stroke="#666" stroke-width="1.6" marker-end="url(#ah)"/>'
        + '<line x1="702" y1="219" x2="754" y2="219" stroke="#666" stroke-width="1.6" marker-end="url(#ah)"/>'
        + '<text x="229" y="213" text-anchor="middle" font-size="8.5" fill="#777">ingest</text>'
        + '<text x="479" y="213" text-anchor="middle" font-size="8.5" fill="#777">frames</text>'
        + '<text x="728" y="213" text-anchor="middle" font-size="8.5" fill="#777">write meta</text>'
        # --- message broker box (top, spanning between VIOS and Overlay Plugin) ---
        + _arch_box(380, 18, "Message Broker", "Redis / Kafka", w=200, h=74,
                    icon=_arch_icon("broker", C_BR[0]), fill=C_BR[1],
                    stroke=C_BR[0], accent=C_BR[0])
        # sample-metadata payload glyph flowing on the live path (right of broker)
        + f'<g transform="translate(660,110) scale(0.6)" fill="none" stroke="{C_BR[0]}" '
          'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
          f'{_arch_icon("doc", C_BR[0])}</g>'
        + f'<text x="671" y="140" text-anchor="middle" font-size="8" fill="{C_BR[0]}">'
          'sample metadata docs</text>'
        # publish: Overlay Plugin top -> broker (green, live path)
        + f'<line x1="560" y1="178" x2="560" y2="93" stroke="{GREEN}" stroke-width="1.8" '
          'marker-end="url(#ahg)"/>'
        + f'<text x="566" y="140" text-anchor="start" font-size="8.5" fill="{GREEN}">publish</text>'
        # consume: broker -> VIOS top (grey)
        + '<line x1="400" y1="93" x2="400" y2="176" stroke="#666" stroke-width="1.8" '
          'marker-end="url(#ah)"/>'
        + '<text x="394" y="140" text-anchor="end" font-size="8.5" fill="#777">consume</text>'
        # replay-consume return path: Fake ES bottom -> curves beneath the row -> VIOS bottom
        + f'<path d="M851,260 C851,298 851,302 730,302 L470,302 C360,302 353,302 353,262" '
          f'fill="none" stroke="{BLUE}" stroke-width="1.8" stroke-dasharray="4 3" '
          'marker-end="url(#ahb)"/>'
        + f'<text x="600" y="316" text-anchor="middle" font-size="8.5" fill="{BLUE}">'
          'consume (replay meta)</text>'
        '</svg>'
        '<div class="arch-cap">Live overlay metadata via broker (Redis / Kafka); '
        'replay metadata via Fake ES.</div>'
        '</div>')


_CSS = """
* { box-sizing: border-box; }
body { font-family: Arial, Helvetica, sans-serif; color: #1a1a1a; font-size: 11.5px; margin: 0; }
.rpthead { border-bottom: 3px solid %(g)s; padding-bottom: 6px; margin-bottom: 4px; }
h1 { font-size: 22px; margin: 0; }
.metaline { color: #666; font-size: 10px; margin-top: 2px; }
.planname { color: %(g)s; font-size: 17px; font-weight: 700; margin: 12px 0 8px; }
/* option-B header: solid black bar with white text, used for each plan section */
.plan-header { background: #000; color: #fff; font-size: 16px; font-weight: 700;
               padding: 7px 12px; margin: 0 0 10px; border-radius: 3px; }
/* ~4-5 blank lines of whitespace separating one plan's section from the next */
.plan-gap { height: 72px; }
/* setup architecture diagram (inline SVG) directly under the report title */
.arch { margin: 8px 0 12px; }
.arch-cap { text-align: center; color: #666; font-size: 9.5px; font-style: italic; margin-top: 3px; }
h3 { color: %(g)s; font-size: 13px; border-bottom: 1px solid #ddd; padding-bottom: 2px; margin: 16px 0 7px; }
h4 { font-size: 11px; color: #444; margin: 8px 0 3px; }
h4.evh { color: %(g)s; font-size: 12px; border-bottom: 1px dotted #cfe0a8; padding-bottom: 2px; margin: 12px 0 6px; }
.cols { display: flex; gap: 16px; } .cols > div { flex: 1; } .pcol { flex: 1; }
table { width: 100%%; border-collapse: collapse; margin: 3px 0; }
th { background: #f0f0f0; text-transform: uppercase; font-size: 9px; color: #555; text-align: left;
     padding: 4px 6px; border-bottom: 1px solid #ddd; }
td { padding: 3px 6px; border-bottom: 1px solid #eee; vertical-align: top; }
tbody tr:nth-child(even) { background: #fafafa; }
tr.ov { background: #fff6e6 !important; }
.mono { font-family: "DejaVu Sans Mono", monospace; } .muted { color: #666; } .c { text-align: center; }
a { color: #2a6fb0; text-decoration: none; word-break: break-all; }
.pass { color: %(g)s; font-weight: 700; } .fail { color: #d33; font-weight: 700; } .skip { color: #999; font-weight: 700; }
.cards { display: flex; flex-wrap: wrap; gap: 11px; }
.card { width: 47%%; border: 1px solid #e2e2e2; border-radius: 6px; padding: 7px 9px; page-break-inside: avoid; }
.ct { font-weight: 700; font-size: 10.5px; }
.cd { color: #555; font-size: 9.5px; margin: 2px 0 4px; }
.pill { font-size: 8.5px; padding: 1px 6px; border-radius: 8px; border: 1px solid currentColor; }
a.play { display: inline-block; background: %(g)s; color: #fff; font-size: 9px; padding: 2px 9px;
         border-radius: 4px; margin-bottom: 4px; }
.card img { width: 100%%; border: 1px solid #ddd; border-radius: 4px; margin-top: 4px; }
.foot { color: #999; font-size: 9px; text-align: center; margin-top: 22px; }
.failhdr { color: #b00020; border-top: 2px solid #b00020; padding-top: 6px; margin-top: 20px; }
.faillinks { margin: 2px 0 6px; font-size: 10px; }
.faillinks a { color: #b00020; margin-right: 4px; }
td code { font-size: 9px; background: #f4f4f4; padding: 1px 3px; border-radius: 3px; word-break: break-all; }
""" % {"g": GREEN}


def _failures_section(results: List[UseCaseResult], failures, plan_meta: dict = None) -> str:
    """A dedicated PDF section pointing to the failure artifacts: the failed-cases JSON
    (every FAIL with its plan, exact request -- api + startTime/endTime/params/streamid --
    and error) and THIS plan's full sensor-MS / streamprocessing-MS container logs (captured
    per-plan while the containers were alive). The per-case list lives in the JSON, not the
    PDF. Empty string when there are no failures."""
    fails = [r for r in results if r.status == "FAIL"]
    if not fails:
        return ""
    link = (failures or {}).get("link", "") if isinstance(failures, dict) else (failures or "")
    # Logs are per-plan (from this plan's meta), not the global failures object.
    logs = (plan_meta or {}).get("logs", {}) if isinstance(plan_meta, dict) else {}
    links = []
    if link:
        links.append(f'<a href="{_esc(link)}"><b>Failed-cases JSON &darr;</b></a>')
    for name, url in (logs or {}).items():
        if url:
            links.append(f'<a href="{_esc(url)}">{_esc(name)} log &darr;</a>')
    linkline = (f'<div class="faillinks">{" &middot; ".join(links)}</div>') if links else ""
    return (f'<h3 class="failhdr">Failures ({len(fails)})</h3>'
            f'{linkline}'
            f'<div class="muted">Per-case details (use case, plan, request api/params, '
            f'error) are in the failed-cases JSON above; the container logs are full (from '
            f'container start).</div>')


def _html(results: List[UseCaseResult], ctx: SanityContext, when: str,
          plan_meta: Optional[Dict[str, dict]], failures=None) -> str:
    meta = _env_meta(ctx)
    plan_meta = plan_meta or {}
    plans = OrderedDict()
    for r in results:
        plans.setdefault(r.plan or "Sanity", []).append(r)

    blocks = []
    for idx, (plan_name, rs) in enumerate(plans.items()):
        pm = plan_meta.get(plan_name, {})
        npass = sum(1 for r in rs if r.status == "PASS")
        nfail = sum(1 for r in rs if r.status == "FAIL")
        nskip = sum(1 for r in rs if r.status == "SKIP")
        perf = _perf_tables(rs)
        # Evidence gallery: include every result in *this* plan that carries an
        # evidence flag OR produced a snapshot/link. Using a union (not an "else"
        # fallback) guarantees each plan's overlay evidence renders in its own
        # section -- a flagged result in one plan can no longer suppress the
        # unflagged snapshots of another plan.
        ev_rs = [r for r in rs if _wants_evidence(r)]
        # ~4-5 blank lines of whitespace before every plan after the first.
        gap = '<div class="plan-gap"></div>' if idx else ''
        blocks.append(
            gap +
            f'<div class="plan-header">{_esc(plan_name)}</div>'
            f'<div class="cols"><div><h3>Deployment Configuration</h3>'
            f'<table>{_config_rows(meta, pm)}</table></div>'
            f'<div><h3>Run Summary</h3><table><tr>'
            f'<td class="pass">{npass} PASS</td><td class="fail">{nfail} FAIL</td>'
            f'<td class="skip">{nskip} SKIP</td></tr></table>'
            f'<h4>Gate checklist</h4><table>{_gate_rows(rs)}</table></div></div>'
            f'<h3>Results Matrix</h3><table><thead><tr><th>Use case</th><th>Group</th>'
            f'<th>Status</th><th>Time</th><th>Detail</th></tr></thead>'
            f'<tbody>{_matrix_rows(rs)}</tbody></table>'
            + (f'<h3>Performance &mdash; Latency</h3>{perf}' if perf else '')
            + f'<h3>Evidence</h3>{_evidence(ev_rs)}'
            + _failures_section(rs, failures, pm))   # per-plan failures section (logs from pm)

    failures_link = (failures or {}).get("link", "") if isinstance(failures, dict) else (failures or "")
    nfail_all = sum(1 for r in results if r.status == "FAIL")
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>'
            f'<div class="rpthead"><h1>VIOS Sanity Report</h1>'
            f'<div class="metaline">Run {_esc(when)} &middot; Host {_esc(meta["host_ip"])} '
            f'&middot; Branch {_esc(meta["branch"])} @ {_esc(meta["commit"])} '
            f'&middot; {len(plans)} plan(s) &middot; {len(results)} use-cases'
            + (f' &middot; <a href="{_esc(failures_link)}"><b>{nfail_all} '
               f'failed &rarr; details</b></a>' if failures_link and nfail_all
               else (f' &middot; <b>{nfail_all} failed</b>' if nfail_all else ''))
            + '</div></div>'
            f'{_arch_diagram()}'
            f'{"".join(blocks)}'
            f'<div class="foot">VIOS + NVStreamer &middot; vios-sanity &middot; automated sanity report</div>'
            f'</body></html>')


def build_pdf(results: List[UseCaseResult], ctx: SanityContext, when: str, out_path: Path,
              plan_meta: Optional[Dict[str, dict]] = None, failures=None) -> Path:
    out_path = Path(out_path)
    doc = _html(results, ctx, when, plan_meta, failures)
    out_path.with_suffix(".html").write_text(doc)   # HTML twin (real new-tab links)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        launch_kw = {"headless": True, "args": ["--no-sandbox"]}
        if CHROME:
            launch_kw["executable_path"] = CHROME
        b = p.chromium.launch(**launch_kw)
        pg = b.new_page()
        pg.set_content(doc, wait_until="networkidle")
        pg.pdf(path=str(out_path), format="A4", print_background=True,
               margin={"top": "14mm", "bottom": "12mm", "left": "12mm", "right": "12mm"})
        b.close()
    logger.info("rendered PDF via Chromium -> %s", out_path)
    return out_path
