# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""RTSP DESCRIBE pre-flight — Python equivalent of live555 testRtspUrl (testRTSP.cpp:73).

Used by POST /sensor/add when {"verifyRtsp": true}: probes the supplied RTSP URL before persisting.
Returns ok=True only on a 200 OK DESCRIBE; 401/404/connection-refused/timeout -> ok=False (the add
is then rejected with InvalidParameterError, per swagger). On success it also parses codec /
framerate / resolution from the SDP, matching the C++ which feeds those into the codec-support check
and the camera_proxy metadata.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

_DEFAULT_RTSP_PORT = 554


@dataclass
class RtspProbe:
    ok: bool
    status: int = 0
    codec: str = ""
    framerate: str = ""
    resolution: str = ""
    reason: str = ""


def _strip_userinfo(url: str) -> str:
    p = urlparse(url)
    netloc = p.hostname or ""
    if p.port:
        netloc = f"{netloc}:{p.port}"
    return p._replace(netloc=netloc).geturl()


def _digest_header(user: str, pw: str, method: str, uri: str, challenge: str) -> str:
    def field(name: str) -> str:
        m = re.search(rf'{name}="?([^",]+)"?', challenge)
        return m.group(1) if m else ""

    realm, nonce, qop, opaque = field("realm"), field("nonce"), field("qop"), field("opaque")
    ha1 = hashlib.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    if qop:
        cnonce, nc = "0a4f113b", "00000001"
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
        h = (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
             f'qop={qop}, nc={nc}, cnonce="{cnonce}", response="{resp}"')
    else:
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        h = f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"'
    if opaque:
        h += f', opaque="{opaque}"'
    return h


def _parse_sdp(sdp: str) -> tuple[str, str, str]:
    codec = framerate = resolution = ""
    m = re.search(r"a=rtpmap:\d+\s+([A-Za-z0-9]+)/", sdp)
    if m:
        codec = m.group(1)
    m = re.search(r"a=framerate:([\d.]+)", sdp) or re.search(r"a=x-framerate:\s*([\d.]+)", sdp)
    if m:
        framerate = m.group(1)
    m = re.search(r"a=x-dimensions:\s*(\d+)\s*,\s*(\d+)", sdp)
    if m:
        resolution = f"{m.group(1)}x{m.group(2)}"
    return codec, framerate, resolution


async def rtsp_describe(url: str, username: str = "", password: str = "", timeout: float = 2.0) -> RtspProbe:
    p = urlparse(url)
    host, port = p.hostname, p.port or _DEFAULT_RTSP_PORT
    if not host:
        return RtspProbe(ok=False, reason="malformed url")
    target = _strip_userinfo(url)

    async def _attempt(auth: str | None, cseq: int) -> tuple[int, str, str]:
        req = [f"DESCRIBE {target} RTSP/1.0", f"CSeq: {cseq}",
               "User-Agent: vios-sensor-ms", "Accept: application/sdp"]
        if auth:
            req.append(f"Authorization: {auth}")
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write(("\r\n".join(req) + "\r\n\r\n").encode())
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            status = int(head.split(b" ", 2)[1])
            clen_m = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
            body = await reader.readexactly(int(clen_m.group(1))) if clen_m else b""
            return status, head.decode("latin1"), body.decode("latin1", "ignore")
        finally:
            writer.close()

    try:
        status, head, body = await asyncio.wait_for(_attempt(None, 1), timeout)
        if status == 401 and (username or password):
            ch = re.search(r"WWW-Authenticate:\s*(.+)", head, re.I)
            chal = ch.group(1).strip() if ch else ""
            if chal.lower().startswith("digest"):
                auth = _digest_header(username, password, "DESCRIBE", target, chal)
            else:
                auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
            status, head, body = await asyncio.wait_for(_attempt(auth, 2), timeout)
    except asyncio.TimeoutError:
        return RtspProbe(ok=False, reason="timeout")
    except (ConnectionRefusedError, OSError) as e:
        return RtspProbe(ok=False, reason=f"connect failed: {e}")
    except Exception as e:  # malformed response, etc.
        return RtspProbe(ok=False, reason=f"protocol error: {e}")

    if status != 200:
        return RtspProbe(ok=False, status=status, reason=f"DESCRIBE returned {status}")
    codec, framerate, resolution = _parse_sdp(body)
    return RtspProbe(ok=True, status=200, codec=codec, framerate=framerate, resolution=resolution)
