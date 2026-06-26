# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Adaptor loader — importlib-based replacement for the C++ dlopen plugin loader.

Reads configs/adaptor_config.json (same schema as the C++ `vst` array, DESIGN.md §6.6): selects the
adaptor by $ADAPTOR env or the first `enabled` entry, then imports its control/discovery classes by
dotted path instead of loading a .so. The `need_rtsp_server` / `need_recording` flags continue to
gate downstream event behavior (e.g. camera_proxy vs camera_streaming).

TODO(P2/P3): register the concrete adaptor module paths once implemented.
"""
from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from typing import Any

from .base import AdaptorInfo, SensorControlAdaptor, SensorDiscoveryAdaptor


@dataclass
class LoadedAdaptor:
    info: AdaptorInfo
    control: SensorControlAdaptor | None
    discovery: SensorDiscoveryAdaptor | None
    need_rtsp_server: bool = False
    need_recording: bool = False
    need_storage_management: bool = False
    need_stream_monitoring: bool = False


# Map adaptor `name` -> dotted import paths for (control_cls, discovery_cls).
# Empty string means that role is absent for the adaptor.
_REGISTRY: dict[str, tuple[str, str]] = {
    "onvif": ("sensor_ms.adaptors.onvif.control:OnvifControl", ""),
    # discovery is the in-house WS-Discovery probe (adaptors.onvif.discovery.discover), driven by
    # SensorMonitoring rather than a discovery-adaptor class; wired in P4.
    # "rtsp_streams": ("sensor_ms.adaptors.rtsp_streams:RtspStreamsControl", ""),
    # Milestone XProtect (mms): SOAP Login + systeminfo.xml discovery + RTSP URLs (control plane only;
    # the gRPC/GraphQL clip path stays in the media/storage service).
    "milestone_soap": ("sensor_ms.adaptors.milestone:MilestoneControl", ""),
    # milestone_onvif uses the ONVIF control adaptor (XProtect's ONVIF bridge).
    "milestone_onvif": ("sensor_ms.adaptors.onvif.control:OnvifControl", ""),
}


def _import_obj(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def load_control_adaptor(name: str) -> SensorControlAdaptor | None:
    """Instantiate the control adaptor registered under `name` (e.g. "onvif"), or None if the
    adaptor has no control class / is unknown. Lets the active adaptor be selected via the ADAPTOR
    env without a full adaptor_config.json."""
    entry = _REGISTRY.get(name)
    if not entry:
        return None
    ctl_spec, _ = entry
    return _import_obj(ctl_spec)() if ctl_spec else None


def load_adaptor(config_path: str) -> LoadedAdaptor:
    with open(config_path) as fh:
        entries = json.load(fh).get("vst", [])

    wanted = os.environ.get("ADAPTOR")
    chosen = None
    for e in entries:
        if wanted and e.get("name") == wanted:
            chosen = e
            break
        if chosen is None and e.get("enabled"):
            chosen = e
    if chosen is None:
        raise RuntimeError(f"no enabled adaptor in {config_path}")

    info = AdaptorInfo(
        m_id=chosen.get("id", ""),
        m_name=chosen.get("name", ""),
        m_type=chosen.get("type", ""),
        m_user=chosen.get("user", ""),
        m_password=chosen.get("password", ""),
        m_port=str(chosen.get("port", "")),
        m_ipaddress=chosen.get("ip", ""),
        m_url=chosen.get("url", ""),
    )
    # Either source is valid for adaptor credentials: a non-empty compose.env override
    # (ADAPTOR_IP/USER/PASSWORD/PORT) takes precedence over the adaptor_config.json entry, so a
    # deployment can configure the bridge/VMS in one place. Mirrors the C++ AdaptorLoader override.
    info.m_ipaddress = os.environ.get("ADAPTOR_IP", "").strip() or info.m_ipaddress
    info.m_user = os.environ.get("ADAPTOR_USER", "").strip() or info.m_user
    info.m_password = os.environ.get("ADAPTOR_PASSWORD") or info.m_password
    info.m_port = os.environ.get("ADAPTOR_PORT", "").strip() or info.m_port

    control = discovery = None
    if info.m_name in _REGISTRY:
        ctl_spec, disc_spec = _REGISTRY[info.m_name]
        if ctl_spec:
            control = _import_obj(ctl_spec)()
            control.info = info
        if disc_spec:
            discovery = _import_obj(disc_spec)()
    # else: adaptor not yet ported (P3) — caller handles None.

    return LoadedAdaptor(
        info=info,
        control=control,
        discovery=discovery,
        need_rtsp_server=bool(chosen.get("need_rtsp_server", False)),
        need_recording=bool(chosen.get("need_recording", False)),
        need_storage_management=bool(chosen.get("need_storage_management", False)),
        need_stream_monitoring=bool(chosen.get("need_stream_monitoring", False)),
    )
