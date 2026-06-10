# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""WS-Discovery probe for ONVIF cameras (in-house, no LGPL dependency).

Sends a WS-Discovery Probe for `NetworkVideoTransmitter` to the multicast group 239.255.255.250:3702
and collects ProbeMatch responses. This deliberately avoids the LGPLv3 `WSDiscovery` package so the
service stays fully permissively-licensed (see DESIGN.md §7). Message construction and response
parsing are pure functions (unit-tested); discover() does the UDP multicast send/collect loop.
"""
from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

WS_DISCOVERY_ADDR = "239.255.255.250"
WS_DISCOVERY_PORT = 3702
_PROBE_ACTION = "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe"

_NS = {
    "e": "http://www.w3.org/2003/05/soap-envelope",
    "w": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
    "d": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
}


@dataclass
class ProbeMatch:
    address: str = ""            # EndpointReference Address (urn:uuid:...)
    xaddrs: list[str] = field(default_factory=list)   # device service URLs
    types: str = ""
    scopes: str = ""

    @property
    def device_service_url(self) -> str:
        return self.xaddrs[0] if self.xaddrs else ""


def build_probe(message_id: str) -> bytes:
    """Build the WS-Discovery Probe SOAP envelope. message_id must be a fresh uuid (caller-supplied
    for determinism in tests)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
        ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
        ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<e:Header>"
        f"<w:MessageID>uuid:{message_id}</w:MessageID>"
        '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
        f'<w:Action e:mustUnderstand="true">{_PROBE_ACTION}</w:Action>'
        "</e:Header>"
        "<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>"
        "</e:Envelope>"
    ).encode("utf-8")


def parse_probe_match(data: bytes) -> list[ProbeMatch]:
    """Parse a ProbeMatches SOAP response into ProbeMatch entries (tolerant of missing fields)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out: list[ProbeMatch] = []
    for pm in root.iter(f"{{{_NS['d']}}}ProbeMatch"):
        addr_el = pm.find(f"w:EndpointReference/w:Address", _NS)
        xaddrs_el = pm.find("d:XAddrs", _NS)
        types_el = pm.find("d:Types", _NS)
        scopes_el = pm.find("d:Scopes", _NS)
        xaddrs = (xaddrs_el.text or "").split() if xaddrs_el is not None and xaddrs_el.text else []
        out.append(ProbeMatch(
            address=(addr_el.text or "").strip() if addr_el is not None and addr_el.text else "",
            xaddrs=xaddrs,
            types=(types_el.text or "").strip() if types_el is not None and types_el.text else "",
            scopes=(scopes_el.text or "").strip() if scopes_el is not None and scopes_el.text else "",
        ))
    return out


def dedup_matches(matches: list[ProbeMatch]) -> list[ProbeMatch]:
    """Dedupe ProbeMatch entries by device_service_url (falling back to endpoint address)."""
    seen, out = set(), []
    for m in matches:
        key = m.device_service_url or m.address
        if key and key not in seen:
            seen.add(key)
            out.append(m)
    return out


class _ProbeProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.matches: list[ProbeMatch] = []

    def datagram_received(self, data: bytes, addr) -> None:
        self.matches.extend(parse_probe_match(data))


async def discover(message_id: str, timeout: float = 3.0, bind_ip: str = "0.0.0.0") -> list[ProbeMatch]:
    """Multicast a Probe and collect ProbeMatch responses for `timeout` seconds. Deduped by
    device_service_url. message_id is caller-supplied (uuid4 at runtime)."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.bind((bind_ip, 0))
    transport, proto = await loop.create_datagram_endpoint(lambda: _ProbeProtocol(), sock=sock)
    try:
        transport.sendto(build_probe(message_id), (WS_DISCOVERY_ADDR, WS_DISCOVERY_PORT))
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return dedup_matches(proto.matches)
