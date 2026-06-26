# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ONVIF control adaptor on onvif-zeep-async (MIT) — replaces the proprietary nvsoap path.

Maps ONVIF Media/Device/Imaging/PTZ services to the SensorControlAdaptor interface. The camera-facing
methods require a real ONVIF device; the response->model mapping is factored into pure functions
(profile_to_stream, device_info_to_fields) that ARE unit-tested with mock objects.

OUTSTANDING VALIDATION GATE (DESIGN.md §7, P3): the live camera matrix (discover, GetProfiles,
GetStreamUri, PTZ, digest auth) must be run against real hardware before cutover. Not reproducible in
this environment (no ONVIF camera).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ..base import SensorControlAdaptor

log = logging.getLogger(__name__)

STREAM_TYPE_RTSP = "Rtsp"

# Only correct the camera clock when it drifts beyond this many seconds from host UTC. Mirrors the
# C++ NvSoap::synchronizeDeviceTime threshold (DEVICE_AND_CLIENT_TIME_DIFFERENCE_SEC = 1). NOTE: the
# ONVIF SetSystemDateAndTime/GetSystemDateAndTime payload is SECOND-resolution (Year..Second, no
# millisecond field), so manual sync can only align cameras to ~1s; true millisecond alignment
# requires NTP on the camera (configure_sensor_ntp), where the device disciplines its own clock.
ONVIF_TIME_SYNC_DRIFT_THRESHOLD_SECS = 1


def _ntp_entry(server: str) -> dict[str, Any]:
    """Build an ONVIF NTPInformation manual entry, choosing IPv4 vs DNS by the literal form."""
    parts = server.split(".")
    is_ipv4 = len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    if is_ipv4:
        return {"Type": "IPv4", "IPv4Address": server}
    return {"Type": "DNS", "DNSname": server}


def _extract_utc_datetime(resp: Any) -> datetime | None:
    """Pull a timezone-aware UTC datetime out of an ONVIF GetSystemDateAndTime response.
    Prefers UTCDateTime; returns None if the device doesn't report a usable timestamp."""
    sdt = getattr(resp, "UTCDateTime", None) or getattr(resp, "LocalDateTime", None)
    if sdt is None:
        return None
    date = getattr(sdt, "Date", None)
    time_ = getattr(sdt, "Time", None)
    if date is None or time_ is None:
        return None
    try:
        return datetime(
            int(date.Year), int(date.Month), int(date.Day),
            int(time_.Hour), int(time_.Minute), int(time_.Second),
            tzinfo=timezone.utc,
        )
    except (AttributeError, ValueError, TypeError):
        return None


# --- pure mapping helpers (unit-tested) ---
def profile_to_stream(profile: Any, stream_uri: str, is_main: bool) -> dict[str, Any]:
    """Map an ONVIF media Profile (+ resolved RTSP URI) to our StreamInfo API dict."""
    vec = getattr(profile, "VideoEncoderConfiguration", None)
    codec = (getattr(vec, "Encoding", "") or "") if vec else ""
    res = getattr(vec, "Resolution", None) if vec else None
    resolution = f"{res.Width}x{res.Height}" if res is not None else ""
    rc = getattr(vec, "RateControl", None) if vec else None
    framerate = str(getattr(rc, "FrameRateLimit", "") or "") if rc is not None else ""
    govlength = str(getattr(vec, "GovLength", "") or "") if vec else ""
    bitrate = str(getattr(rc, "BitrateLimit", "") or "") if rc is not None else ""
    token = getattr(profile, "token", "") or getattr(profile, "_token", "")
    return {
        "streamId": token,
        "isMain": is_main,
        "type": STREAM_TYPE_RTSP,
        "storageLocation": "Local",
        "url": stream_uri,            # ONVIF GetStreamUri result (live RTSP)
        "vodUrl": "",
        "name": getattr(profile, "Name", "") or "",
        "metadata": {
            "bitrate": bitrate, "codec": codec, "framerate": framerate,
            "govlength": govlength, "resolution": resolution,
        },
    }


def prefix_to_netmask(prefix: int) -> str:
    """IPv4 prefix length -> dotted-quad netmask (C++ getNetmaskFromPrefixLen). 24 -> 255.255.255.0."""
    try:
        p = max(0, min(32, int(prefix)))
    except (TypeError, ValueError):
        return ""
    mask = (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF if p else 0
    return ".".join(str((mask >> (8 * i)) & 0xFF) for i in (3, 2, 1, 0))


def netmask_to_prefix(mask: str) -> int:
    """Dotted-quad netmask -> IPv4 prefix length (C++ getPrefixLength). 255.255.255.0 -> 24."""
    try:
        parts = [int(o) for o in (mask or "").split(".")]
        if len(parts) != 4:
            return 0
        bits = "".join(f"{o:08b}" for o in parts)
        return bits.count("1")
    except ValueError:
        return 0


def network_interface_to_info(iface: Any) -> dict[str, Any]:
    """Map an ONVIF NetworkInterface to the swagger NetworkInfo dict (C++ getSensorNetworkInfo).
    Defensive against missing optional fields (cameras vary widely in what they populate)."""

    def _first_addr(cfg: Any) -> tuple[str, int]:
        manual = getattr(cfg, "Manual", None) or []
        if manual:
            return getattr(manual[0], "Address", "") or "", getattr(manual[0], "PrefixLength", 0) or 0
        from_dhcp = getattr(cfg, "FromDHCP", None)
        if from_dhcp is not None:
            return getattr(from_dhcp, "Address", "") or "", getattr(from_dhcp, "PrefixLength", 0) or 0
        return "", 0

    info: dict[str, Any] = {
        "isIpv4Enabled": False, "dhcpV4": "false", "ipAddressV4": "", "subnetMaskV4": "",
        "isIpv6Enabled": False, "dhcpV6": "false", "ipAddressV6": "", "subnetMaskV6": "",
    }
    v4 = getattr(iface, "IPv4", None)
    if v4 is not None:
        info["isIpv4Enabled"] = bool(getattr(v4, "Enabled", False))
        cfg = getattr(v4, "Config", None)
        if cfg is not None:
            info["dhcpV4"] = "true" if getattr(cfg, "DHCP", False) else "false"
            addr, prefix = _first_addr(cfg)
            info["ipAddressV4"] = addr
            info["subnetMaskV4"] = prefix_to_netmask(prefix)
    v6 = getattr(iface, "IPv6", None)
    if v6 is not None:
        info["isIpv6Enabled"] = bool(getattr(v6, "Enabled", False))
        cfg = getattr(v6, "Config", None)
        if cfg is not None:
            dhcp6 = getattr(cfg, "DHCP", "Off")
            info["dhcpV6"] = "true" if str(dhcp6).lower() not in ("off", "false", "") else "false"
            addr, prefix = _first_addr(cfg)
            info["ipAddressV6"] = addr
            info["subnetMaskV6"] = str(prefix or "")
    return info


def _range_setting(value: Any, lo: Any, hi: Any) -> dict[str, str] | None:
    """Build a {Min,Max,Value} RangeSetting, or None when the range is absent (C++ SET_SLIDER_IF_VALID)."""
    if lo is None or hi is None:
        return None
    return {"Min": str(lo), "Max": str(hi), "Value": str(value if value is not None else "")}


def encoder_options_to_encode(config: Any, options: Any) -> dict[str, Any]:
    """Map an ONVIF VideoEncoderConfiguration + its Options to the swagger Encode block.
    Only H264/H265 are surfaced (C++ parity). Returns {} when nothing usable is present."""
    encoding = (getattr(config, "Encoding", "") or "").upper()
    encode: dict[str, Any] = {}
    if encoding in ("H264", "H265"):
        encode["Encoding"] = {"AllowedValues": [encoding], "Value": encoding}

    opt = getattr(options, "H265", None) or getattr(options, "H264", None)
    rc = getattr(config, "RateControl", None)
    element: dict[str, Any] = {}
    quality = getattr(config, "Quality", None)
    q_range = getattr(opt, "QualityRange", None) if opt is not None else None
    rs = _range_setting(quality, getattr(q_range, "Min", None) if q_range else None,
                        getattr(q_range, "Max", None) if q_range else None)
    if rs:
        element["Quality"] = rs
    if rc is not None:
        element_bitrate = getattr(rc, "BitrateLimit", None)
        if element_bitrate is not None:
            element["Bitrate"] = {"Min": "", "Max": "", "Value": str(element_bitrate)}
        fr = getattr(rc, "FrameRateLimit", None)
        fr_range = getattr(opt, "FrameRateRange", None) if opt is not None else None
        if fr_range is not None:
            element["FrameRate"] = {
                "AllowedValues": [str(getattr(fr_range, "Min", "")), str(getattr(fr_range, "Max", ""))],
                "Value": str(fr if fr is not None else ""),
            }
    gov = getattr(opt, "GovLengthRange", None) if opt is not None else None
    rs = _range_setting(getattr(config, "GovLength", None),
                        getattr(gov, "Min", None) if gov else None,
                        getattr(gov, "Max", None) if gov else None)
    if rs:
        element["GovLength"] = rs
    res = getattr(config, "Resolution", None)
    res_avail = getattr(opt, "ResolutionsAvailable", None) if opt is not None else None
    if res is not None and res_avail:
        element["Resolution"] = {
            "AllowedValues": [{"Width": getattr(r, "Width", ""), "Height": getattr(r, "Height", "")}
                              for r in res_avail],
            "Value": {"Width": getattr(res, "Width", ""), "Height": getattr(res, "Height", "")},
        }
    if element and encoding in ("H264", "H265"):
        encode["Options"] = [{encoding: element}]
    return encode


def _to_int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _apply_encode_settings(vec: Any, encode: dict[str, Any]) -> None:
    """Mutate an ONVIF VideoEncoderConfiguration from the flat Encode body (C++ setSensorSettings
    shape). Only the fields present are applied, coerced to the numeric types zeep expects. GovLength
    and the profile live under the codec-specific element (H264/MPEG4) in ONVIF Media1, not on the
    configuration directly."""
    if encode.get("Encoding"):
        vec.Encoding = encode["Encoding"]
    quality = _to_float(encode.get("Quality"))
    if quality is not None:
        vec.Quality = quality

    rc = getattr(vec, "RateControl", None)
    if rc is not None:
        bitrate = _to_int(encode.get("Bitrate"))
        if bitrate is not None:
            rc.BitrateLimit = bitrate
        framerate = _to_int(encode.get("FrameRate"))
        if framerate is not None:
            rc.FrameRateLimit = framerate
        interval = _to_int(encode.get("EncodingInterval"))
        if interval is not None:
            rc.EncodingInterval = interval

    res = encode.get("Resolution")
    vres = getattr(vec, "Resolution", None)
    if isinstance(res, dict) and vres is not None:
        w, h = _to_int(res.get("Width")), _to_int(res.get("Height"))
        if w is not None and h is not None:
            vres.Width, vres.Height = w, h

    gov = _to_int(encode.get("GovLength"))
    codec = getattr(vec, "H264", None) or getattr(vec, "MPEG4", None)
    if gov is not None and codec is not None:
        codec.GovLength = gov
    if encode.get("Profiles") and getattr(vec, "H264", None) is not None:
        vec.H264.H264Profile = encode["Profiles"]


def _clamp_to_range(value: float, rng: Any) -> float:
    """Clamp value to [rng.Min, rng.Max] when the range object is present, else return it unchanged."""
    if rng is None:
        return value
    lo, hi = getattr(rng, "Min", None), getattr(rng, "Max", None)
    if lo is not None:
        value = max(value, float(lo))
    if hi is not None:
        value = min(value, float(hi))
    return value


def _nearest_resolution(target: str, available: Any) -> tuple[int, int] | None:
    """Pick the supported resolution whose pixel count is closest to 'WxH'. Returns None when there
    are no options (caller then leaves the resolution unchanged). Mirrors C++ nearest-resolution."""
    if not available:
        return None
    try:
        tw, th = (int(x) for x in str(target).lower().split("x", 1))
    except (ValueError, AttributeError):
        return None
    target_px = tw * th
    best = min(available, key=lambda r: abs(
        int(getattr(r, "Width", 0)) * int(getattr(r, "Height", 0)) - target_px))
    return int(getattr(best, "Width", 0)), int(getattr(best, "Height", 0))


def default_encode_from_options(defaults: dict[str, Any], opt: Any) -> dict[str, Any]:
    """Build the flat Encode body that applies the configured defaults (bitrate/framerate/gov/
    resolution) to a camera, clamped to that camera's encoder options. Mirrors C++
    setStreamDefaultSettings. `opt` is the H264/H265 options block (or None). A default of 0/empty is
    skipped (C++ parity: it only overrides when configured)."""
    encode: dict[str, Any] = {}
    bitrate = defaults.get("bitrate")
    if bitrate:
        # ONVIF Media1 options carry no standard bitrate range, so set the configured value directly;
        # the camera clamps/rejects out-of-range internally.
        encode["Bitrate"] = str(int(bitrate))
    framerate = defaults.get("framerate")
    if framerate:
        encode["FrameRate"] = str(int(_clamp_to_range(
            float(framerate), getattr(opt, "FrameRateRange", None) if opt is not None else None)))
    gov = defaults.get("gov_length")
    if gov:
        encode["GovLength"] = str(int(_clamp_to_range(
            float(gov), getattr(opt, "GovLengthRange", None) if opt is not None else None)))
    res = defaults.get("resolution")
    if res:
        nearest = _nearest_resolution(
            res, getattr(opt, "ResolutionsAvailable", None) if opt is not None else None)
        if nearest:
            encode["Resolution"] = {"Width": nearest[0], "Height": nearest[1]}
    return encode


def device_info_to_fields(dev_info: Any) -> dict[str, str]:
    """Map ONVIF GetDeviceInformation -> sensor fields. hardware = Model (matches the C++ and the
    /hardware/ discovery scope, e.g. "DS-2CD2T43G0-I5"); HardwareId is a separate device id."""
    return {
        "hardware": getattr(dev_info, "Model", "") or getattr(dev_info, "HardwareId", "") or "",
        "manufacturer": getattr(dev_info, "Manufacturer", "") or "",
        "serialNumber": getattr(dev_info, "SerialNumber", "") or "",
        "firmwareVersion": getattr(dev_info, "FirmwareVersion", "") or "",
    }


class OnvifControl(SensorControlAdaptor):
    """ONVIF control via onvif-zeep-async. Opens an ONVIFCamera per call and always closes it,
    so a failed/timed-out connection never leaks the underlying aiohttp session."""

    @staticmethod
    async def _connect(host: str, port: int, user: str, pw: str):
        from onvif import ONVIFCamera  # lazy: only when an ONVIF device is actually used

        cam = ONVIFCamera(host, port, user, pw)
        try:
            await cam.update_xaddrs()
        except BaseException:
            # update_xaddrs failed after the aiohttp session was created -- a connect timeout/error
            # (Exception) OR a cancellation (CancelledError, e.g. the HTTP request was cancelled by a
            # client/ingress timeout while connecting to a slow camera). Catch BaseException so the
            # session is ALWAYS closed; with `except Exception` a cancellation would leak it
            # ("Unclosed client session"). Close, then re-raise.
            await OnvifControl._close(cam)
            raise
        return cam

    @staticmethod
    async def _close(cam) -> None:
        """Close every aiohttp resource the camera holds. We do NOT simply call cam.close() because
        the library's close() runs its sub-closes (snapshot client/connector, then each service) in
        sequence with no per-step guard: if one sub-close raises OR the close is interrupted by
        cancellation, the remaining sessions leak ("Unclosed client session").

        Instead we collect every closer, then close them one by one:
          - each close is `asyncio.shield`-ed so it runs to completion even if this cleanup is being
            cancelled (e.g. the request timed out mid-operation);
          - a CancelledError raised while awaiting one close does NOT abort the loop -- we remember it
            and keep closing the rest, then re-raise it at the end so the task still cancels. With a
            plain `except Exception` the cancellation would escape after the first close and leak the
            remaining sessions (the classic "one session still leaks" symptom on the media path,
            which holds two services: devicemgmt + media)."""
        if cam is None:
            return

        closers = []
        # Per-service resources, closed INDIVIDUALLY -- not via service.close(). The library's
        # ONVIFService.close() runs transport.aclose() -> _session.close() -> _connector.close() with
        # no internal guard, so if transport.aclose() raises (the camera drops the connection after a
        # write such as SetVideoEncoderConfiguration), the aiohttp _session/_connector are never
        # closed and leak ("Unclosed client session" -- seen only on the write path). Closing each
        # resource separately means one failure can't strand the others.
        for service in list(getattr(cam, "services", {}).values()):
            transport = getattr(service, "transport", None)
            closers.append(getattr(transport, "aclose", None))
            session = getattr(service, "_session", None)
            closers.append(getattr(session, "close", None) if session is not None else None)
            connector = getattr(service, "_connector", None)
            closers.append(getattr(connector, "close", None) if connector is not None else None)
            # Also call the service's own close() (idempotent after the above) so any resource the
            # library tracks that we don't reach by name is still released.
            closers.append(getattr(service, "close", None))
        # The camera's snapshot client + connector (created in ONVIFCamera.__init__).
        for attr in ("_snapshot_client", "_snapshot_connector"):
            obj = getattr(cam, attr, None)
            closers.append(getattr(obj, "close", None) if obj is not None else None)

        cancelled: BaseException | None = None
        closers = [c for c in closers if c is not None]
        for closer in closers:
            try:
                await asyncio.shield(closer())
            except asyncio.CancelledError as exc:
                cancelled = exc  # keep cleaning up; re-raise after everything is closed
            except Exception:  # best-effort cleanup; never mask the original error
                pass
        if cancelled is not None:
            raise cancelled

    async def connect(self) -> int:
        # Connection is per-sensor (cameras are independent); nothing global to do.
        return 0

    async def validate_credentials(self, sensor: dict[str, Any], username: str, password: str) -> bool:
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)), username, password)
            dev = await cam.create_devicemgmt_service()
            await dev.GetDeviceInformation()
            return True
        except Exception as e:
            log.info("ONVIF credential validation failed for %s: %s", sensor.get("ip"), e)
            return False
        finally:
            await self._close(cam)

    async def get_sensor_stream_info(self, sensor: dict[str, Any]) -> int:
        """Populate sensor['streams'] from ONVIF profiles + stream URIs. Returns 0 on success."""
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            sensor.update(device_info_to_fields(await dev.GetDeviceInformation()))
            media = await cam.create_media_service()
            profiles = await media.GetProfiles()
            streams = []
            for i, prof in enumerate(profiles):
                token = getattr(prof, "token", "")
                uri_resp = await media.GetStreamUri(
                    {"StreamSetup": {"Stream": "RTP-Unicast",
                                     "Transport": {"Protocol": "RTSP"}}, "ProfileToken": token}
                )
                uri = getattr(uri_resp, "Uri", "") or ""
                streams.append(profile_to_stream(prof, uri, is_main=(i == 0)))
            sensor["streams"] = streams
            return 0
        except Exception as e:
            log.error("ONVIF get_sensor_stream_info failed for %s: %s", sensor.get("ip"), e)
            return -1
        finally:
            await self._close(cam)

    # The remaining camera-facing operations follow the same pattern (create service -> call ->
    # map). They require a real device for validation (P3 gate); the response<->model mapping is
    # factored into the pure helpers above (network_interface_to_info, encoder_options_to_encode)
    # which ARE unit-tested.
    async def reboot_sensor(self, sensor: dict[str, Any]) -> int:
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            await dev.SystemReboot()
            return 0
        except Exception as e:
            log.error("ONVIF reboot failed for %s: %s", sensor.get("ip"), e)
            return -1
        finally:
            await self._close(cam)

    async def synchronize_sensor_time(self, sensor: dict[str, Any]) -> int:
        """Correct the camera clock to host UTC via ONVIF SetSystemDateAndTime (Manual mode), but only
        when it has drifted beyond ONVIF_TIME_SYNC_DRIFT_THRESHOLD_SECS. Reads GetSystemDateAndTime
        first (measuring round-trip) so steady-state passes are no-ops and the write is latency-
        compensated, mirroring the C++ resync-on-drift behaviour.

        ONVIF time is SECOND-resolution, so this aligns a camera to within ~1s of host UTC; it cannot
        achieve millisecond accuracy (use configure_sensor_ntp for that).

        Returns 0 when a correction was applied, 1 when the clock was already in sync (no action),
        -1 on error. create_type is synchronous in onvif-zeep-async; only the call is awaited."""
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            # Measure the GetSystemDateAndTime round-trip so we can centre the manual set on the
            # instant the camera applies it (now + rtt/2), shaving the request latency off the error.
            t0 = time.monotonic()
            try:
                cam_resp = await dev.GetSystemDateAndTime()
            except Exception:
                cam_resp = None
            rtt = time.monotonic() - t0
            now = datetime.now(timezone.utc)
            cam_time = _extract_utc_datetime(cam_resp) if cam_resp is not None else None
            if cam_time is not None:
                drift = (now - cam_time).total_seconds()
                if abs(drift) <= ONVIF_TIME_SYNC_DRIFT_THRESHOLD_SECS:
                    log.info("ONVIF %s clock in sync: drift=%+.0fs (cam=%s, host=%s)",
                             sensor.get("ip"), drift, cam_time.isoformat(), now.isoformat())
                    return 1
                log.info("ONVIF %s clock drift=%+.0fs > %ds (cam=%s, host=%s, rtt=%.0fms); correcting",
                         sensor.get("ip"), drift, ONVIF_TIME_SYNC_DRIFT_THRESHOLD_SECS,
                         cam_time.isoformat(), now.isoformat(), rtt * 1000)
            else:
                log.info("ONVIF %s clock unreadable; setting host UTC (host=%s)",
                         sensor.get("ip"), now.isoformat())
            target = datetime.now(timezone.utc) + timedelta(seconds=rtt / 2)
            req = dev.create_type("SetSystemDateAndTime")
            req.DateTimeType = "Manual"
            req.DaylightSavings = False
            req.TimeZone = {"TZ": "UTC0"}
            req.UTCDateTime = {
                "Date": {"Year": target.year, "Month": target.month, "Day": target.day},
                "Time": {"Hour": target.hour, "Minute": target.minute, "Second": target.second},
            }
            await dev.SetSystemDateAndTime(req)
            return 0
        except Exception as e:
            log.error("ONVIF time-sync failed for %s: %s", sensor.get("ip"), e)
            return -1
        finally:
            await self._close(cam)

    async def synchronize_sensors_time_batch(self, sensors: list[dict[str, Any]],
                                             compensation_ms: int = 20) -> int:
        """Set ALL given cameras to the SAME upcoming whole-second boundary, firing every
        SetSystemDateAndTime simultaneously so the cameras are locked to EACH OTHER (not just to the
        host) within LAN latency -- this is what makes overlaid frame timestamps line up. Mirrors C++
        OnvifDiscovery::synchronizeDateAndTime (curl_multi): connect to every camera up-front, wait
        until compensation_ms before the next whole second, then gather the writes so they land on the
        cameras right at the boundary. Returns the number of cameras successfully set."""
        if not sensors:
            return 0

        # Phase 1: connect to every camera concurrently. _connect does network I/O (update_xaddrs) of
        # variable latency, so it is done up-front and is NOT part of the synchronized fire window.
        async def _open(sensor: dict[str, Any]):
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            return sensor, cam, dev

        opened = await asyncio.gather(*(_open(s) for s in sensors), return_exceptions=True)
        ready = [r for r in opened if not isinstance(r, BaseException)]
        unreachable = len(opened) - len(ready)
        if not ready:
            log.warning("batch time-sync: no camera reachable (%d attempted)", len(sensors))
            return 0

        try:
            # Phase 2: align to (next whole second - compensation). If already inside the compensation
            # window, target the following second so every fire is launched pre-boundary.
            comp_s = max(0.0, compensation_ms / 1000.0)
            now = time.time()
            boundary = float(int(now)) + 1.0
            sleep_s = boundary - comp_s - now
            if sleep_s < 0:
                boundary += 1.0
                sleep_s += 1.0
            await asyncio.sleep(sleep_s)

            # Phase 3: build the identical request (whole second .000 UTC) and fire all at once.
            target = datetime.fromtimestamp(boundary, tz=timezone.utc)

            async def _fire(dev) -> None:
                req = dev.create_type("SetSystemDateAndTime")
                req.DateTimeType = "Manual"
                req.DaylightSavings = False
                req.TimeZone = {"TZ": "UTC0"}
                req.UTCDateTime = {
                    "Date": {"Year": target.year, "Month": target.month, "Day": target.day},
                    "Time": {"Hour": target.hour, "Minute": target.minute, "Second": target.second},
                }
                await dev.SetSystemDateAndTime(req)

            results = await asyncio.gather(*(_fire(dev) for _, _, dev in ready),
                                           return_exceptions=True)
            ok = sum(1 for r in results if not isinstance(r, BaseException))
            for r in results:
                if isinstance(r, BaseException):
                    log.warning("batch time-sync: a camera write failed: %s", r)
            log.info("ONVIF batch time-sync: set %d/%d camera(s) to %s (compensation=%dms, "
                     "%d unreachable)", ok, len(sensors), target.isoformat(),
                     compensation_ms, unreachable)
            return ok
        finally:
            await asyncio.gather(*(self._close(cam) for _, cam, _ in ready),
                                 return_exceptions=True)

    async def configure_sensor_ntp(self, sensor: dict[str, Any], ntp_servers: list[str]) -> int:
        """Switch the camera to NTP time discipline (the only path to millisecond-level alignment):
        SetNTP with the given servers, then SetSystemDateAndTime DateTimeType=NTP so the device keeps
        its own clock continuously aligned. Mirrors the (disabled) C++ setNTP fallback in
        NvSoap::synchronizeDeviceTime. Idempotent. Returns 0 on success, 1 if no servers given, -1 on
        error."""
        servers = [s for s in (ntp_servers or []) if s]
        if not servers:
            return 1
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            await dev.SetNTP({"FromDHCP": False, "NTPManual": [_ntp_entry(s) for s in servers]})
            req = dev.create_type("SetSystemDateAndTime")
            req.DateTimeType = "NTP"
            req.DaylightSavings = False
            req.TimeZone = {"TZ": "UTC0"}
            await dev.SetSystemDateAndTime(req)
            log.info("ONVIF %s set to NTP time (servers=%s)", sensor.get("ip"), servers)
            return 0
        except Exception as e:
            log.error("ONVIF NTP config failed for %s: %s", sensor.get("ip"), e)
            return -1
        finally:
            await self._close(cam)

    async def get_network_info(self, sensor: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            ifaces = await dev.GetNetworkInterfaces()
            if not ifaces:
                return -1, {}
            return 0, network_interface_to_info(ifaces[0])
        except Exception as e:
            log.error("ONVIF get_network_info failed for %s: %s", sensor.get("ip"), e)
            return -1, {}
        finally:
            await self._close(cam)

    async def set_network_info(self, sensor: dict[str, Any], net: dict[str, Any]) -> tuple[int, bool]:
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            ifaces = await dev.GetNetworkInterfaces()
            if not ifaces:
                return -1, False
            token = getattr(ifaces[0], "token", "") or getattr(ifaces[0], "_token", "")
            prefix = netmask_to_prefix(net.get("subnetMaskV4", ""))
            req = {
                "InterfaceToken": token,
                "NetworkInterface": {
                    "Enabled": bool(net.get("isIpv4Enabled", True)),
                    "IPv4": {
                        "Enabled": bool(net.get("isIpv4Enabled", True)),
                        "DHCP": str(net.get("dhcpV4", "false")).lower() == "true",
                        "Manual": [{"Address": net.get("ipAddressV4", ""), "PrefixLength": prefix}],
                    },
                },
            }
            resp = await dev.SetNetworkInterfaces(req)
            return 0, bool(getattr(resp, "RebootNeeded", False))
        except Exception as e:
            log.error("ONVIF set_network_info failed for %s: %s", sensor.get("ip"), e)
            return -1, False
        finally:
            await self._close(cam)

    async def get_settings(self, sensor: dict[str, Any], type_: str = "") -> tuple[int, dict[str, Any]]:
        """Per-profile {Image, Encode} options keyed by profile token. type_ filters the blocks."""
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            media = await cam.create_media_service()
            profiles = await media.GetProfiles()
            out: dict[str, Any] = {}
            for prof in profiles:
                token = getattr(prof, "token", "") or getattr(prof, "_token", "")
                values: dict[str, Any] = {}
                if type_ in ("", "Encode"):
                    vec = getattr(prof, "VideoEncoderConfiguration", None)
                    if vec is not None:
                        opts = await media.GetVideoEncoderConfigurationOptions(
                            {"ConfigurationToken": getattr(vec, "token", "")})
                        encode = encoder_options_to_encode(vec, opts)
                        if encode:
                            values["Encode"] = encode
                if values:
                    out[token] = values
            return 0, out
        except Exception as e:
            log.error("ONVIF get_settings failed for %s: %s", sensor.get("ip"), e)
            return -1, {}
        finally:
            await self._close(cam)

    async def set_settings(self, sensor: dict[str, Any], settings: dict[str, Any]) -> int:
        """Apply Encode settings (encoding/bitrate/framerate/quality/govlength/resolution) to the main
        profile's video encoder configuration. Body shape matches the C++ setSensorSettings flat
        Encode object: {"Encode": {"Encoding","Bitrate","FrameRate","Quality","GovLength",
        "EncodingInterval","Resolution":{"Width","Height"}}}. Image-setting apply is camera-specific
        and validated against hardware (P3)."""
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            media = await cam.create_media_service()
            profiles = await media.GetProfiles()
            if not profiles:
                return -1
            vec = getattr(profiles[0], "VideoEncoderConfiguration", None)
            encode = settings.get("Encode") or {}
            if vec is None or not encode:
                return 0

            _apply_encode_settings(vec, encode)
            # create_type is synchronous in onvif-zeep-async; only the service call is awaited.
            req = media.create_type("SetVideoEncoderConfiguration")
            req.Configuration = vec
            req.ForcePersistence = True
            await media.SetVideoEncoderConfiguration(req)
            return 0
        except Exception as e:
            log.error("ONVIF set_settings failed for %s: %s", sensor.get("ip"), e)
            return -1
        finally:
            await self._close(cam)

    async def apply_default_encode_settings(self, sensor: dict[str, Any],
                                            defaults: dict[str, Any]) -> int:
        """Push the configured default encoder settings (bitrate/framerate/gov/resolution) to the
        camera's MAIN video encoder, clamped to the camera's supported options. Mirrors C++
        setStreamDefaultSettings, applied once when an ONVIF camera is credentialed. Returns 0 on
        success (incl. nothing-to-apply), -1 on error."""
        cam = None
        try:
            cam = await self._connect(sensor["ip"], int(sensor.get("port", 80)),
                                      sensor.get("user", ""), sensor.get("password", ""))
            media = await cam.create_media_service()
            profiles = await media.GetProfiles()
            if not profiles:
                return -1
            vec = getattr(profiles[0], "VideoEncoderConfiguration", None)
            if vec is None:
                return 0
            opts = await media.GetVideoEncoderConfigurationOptions(
                {"ConfigurationToken": getattr(vec, "token", "")})
            opt = getattr(opts, "H265", None) or getattr(opts, "H264", None)
            encode = default_encode_from_options(defaults, opt)
            if not encode:
                return 0
            _apply_encode_settings(vec, encode)
            req = media.create_type("SetVideoEncoderConfiguration")
            req.Configuration = vec
            req.ForcePersistence = True
            await media.SetVideoEncoderConfiguration(req)
            log.info("ONVIF %s applied default encode settings: %s", sensor.get("ip"), encode)
            return 0
        except Exception as e:
            log.error("ONVIF apply default encode settings failed for %s: %s", sensor.get("ip"), e)
            return -1
        finally:
            await self._close(cam)
