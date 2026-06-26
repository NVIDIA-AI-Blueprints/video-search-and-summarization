# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Milestone XProtect VMS control adaptor (``milestone_soap``).

Control-plane port of the C++ ``milestone_vms.so`` (src/adaptors/milestone/milestone_vms.cpp):

  - ``connect()``  -> SOAP ``Login`` to the XProtect Management Server's ServerCommandService,
                      yielding a refreshable session token.
  - ``discover()`` -> GET the recording server's ``/rcserver/systeminfo.xml`` and turn each Milestone
                      camera into a sensor with constructed RTSP ``live``/``vod`` URLs.

Implemented with ``httpx`` (HTTP/SOAP transport) + ``lxml`` (XML), both already dependencies.

WHY NO gRPC / protobuf / SOAP toolkit: in the C++ stack those belong to the *media-plane*
``vms_media.so`` (gRPC ``DirectStreaming`` clip download + GraphQL + GStreamer), which is used by the
storage service, NOT the sensor control plane. So the control adaptor needs none of them.

Parity scope (matches what the C++ SOAP adaptor actually does at runtime):
  - connect + bulk discovery + RTSP URL construction + token refresh are real.
  - per-camera control (PTZ / image / encode / network settings, time-sync, timelines) is no-op /
    unsupported in the C++ SOAP adaptor; ONVIF-backed Milestone (``milestone_onvif``) uses the ONVIF
    control adaptor for those instead.
  - the C++ RecorderStatusService online/offline event thread is present but DEAD CODE (never wired);
    online/offline is instead handled by the generic discovery/monitoring loop.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from lxml import etree

from .base import SensorControlAdaptor

log = logging.getLogger(__name__)

SERVERCOMMAND_NS = "http://videoos.net/2/XProtectCSServerCommand"
LOGIN_ACTION = "http://videoos.net/2/XProtectCSServerCommand/IServerCommandService/Login"
MILESTONE_RTSP_PORT = 554
SERVERCOMMAND_PORT = 443
SYSTEMINFO_PORT = 80
_TIMEOUT = 10.0


# --- pure helpers (unit-tested without a server) ---
def build_login_envelope(instance_id: str, current_token: str = "") -> str:
    """SOAP 1.1 envelope for XProtect ServerCommandService ``Login(instanceId, currentToken)``."""
    return (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
        f'<Login xmlns="{SERVERCOMMAND_NS}">'
        f"<instanceId>{instance_id}</instanceId>"
        f"<currentToken>{current_token}</currentToken>"
        "</Login></s:Body></s:Envelope>"
    )


def parse_login_token(xml_bytes: bytes) -> str:
    """Extract the session token from a SOAP ``LoginResponse`` (namespace-agnostic ``Token`` element)."""
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return ""
    for el in root.iter():
        if etree.QName(el).localname == "Token" and (el.text or "").strip():
            return el.text.strip()
    return ""


def _embed_creds(url: str, user: str, password: str) -> str:
    """Embed user[:password]@ into an rtsp URL (C++ embeds adaptor creds in the stream URLs)."""
    if not user or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    cred = quote(user, safe="") + ((":" + quote(password, safe="")) if password else "")
    return f"{scheme}://{cred}@{rest}"


def parse_systeminfo(xml_bytes: bytes, host: str, user: str = "", password: str = "") -> list[dict[str, Any]]:
    """Parse ``rcserver/systeminfo.xml`` into camera dicts, mirroring C++ ``parseCameraInfo``:
    display name = ``<camera name>_<last-guid-segment>``, id = ``<guid>``, RTSP
    ``rtsp://host:554/live|vod/<guid>`` (adaptor creds embedded), codec defaults to h264."""
    out: list[dict[str, Any]] = []
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return out
    base = f"rtsp://{host}:{MILESTONE_RTSP_PORT}"
    for cam in root.iter():
        if etree.QName(cam).localname != "camera":
            continue
        name = cam.get("name", "") or ""
        guid = ""
        for child in cam.iter():
            if etree.QName(child).localname == "guid" and (child.text or "").strip():
                guid = child.text.strip()
                break
        if not guid:
            continue
        disp = f"{name}_{guid.rsplit('-', 1)[-1]}" if name else guid
        out.append({
            "sensor_id": guid,
            "name": disp,
            "live_url": _embed_creds(f"{base}/live/{guid}", user, password),
            "replay_url": _embed_creds(f"{base}/vod/{guid}", user, password),
            "codec": "h264",
        })
    return out


class MilestoneControl(SensorControlAdaptor):
    """XProtect SOAP control adaptor. Server address/creds come from adaptor_config (self.info)."""

    def __init__(self) -> None:
        super().__init__()
        self._token = ""

    def _server(self) -> tuple[str, str, str]:
        ip = self.info.m_ipaddress or (urlparse(self.info.m_url or "").hostname or "")
        return ip, self.info.m_user or "", self.info.m_password or ""

    async def connect(self) -> int:
        return await self.refresh_token()

    async def refresh_token(self) -> int:
        """SOAP Login -> session token. Cert verify is disabled (parity with C++ login path)."""
        ip, user, password = self._server()
        if not ip:
            log.error("Milestone: no server IP configured")
            return -1
        url = f"https://{ip}:{SERVERCOMMAND_PORT}/ManagementServer/ServerCommandService.svc"
        body = build_login_envelope(str(uuid.uuid4()), self._token)
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": LOGIN_ACTION}
        auth = httpx.BasicAuth(user, password) if user else None
        try:
            async with httpx.AsyncClient(verify=False, timeout=_TIMEOUT) as client:
                resp = await client.post(url, content=body, headers=headers, auth=auth)
            token = parse_login_token(resp.content)
            if token:
                self._token = token
                return 0
            log.error("Milestone login: no token (HTTP %s)", resp.status_code)
            return -1
        except Exception as e:
            log.error("Milestone login failed for %s: %s", ip, e)
            return -1

    async def discover(self) -> list[dict[str, Any]]:
        """Login (if needed) then fetch + parse systeminfo.xml into camera dicts."""
        ip, user, password = self._server()
        if not ip:
            return []
        if not self._token:
            await self.refresh_token()
        url = f"http://{ip}:{SYSTEMINFO_PORT}/rcserver/systeminfo.xml"
        auth = httpx.BasicAuth(user, password) if user else None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, auth=auth)
            if resp.status_code != 200:
                log.warning("Milestone systeminfo.xml HTTP %s for %s", resp.status_code, ip)
                return []
            return parse_systeminfo(resp.content, ip, user, password)
        except Exception as e:
            log.error("Milestone discovery failed for %s: %s", ip, e)
            return []

    async def get_sensor_stream_info(self, sensor: dict[str, Any]) -> int:
        return 0  # C++ single-sensor overload is a no-op; discovery is the bulk path
