<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Sensor Microservice — C++ → Python Migration Design

Status: DRAFT (design + frozen contract). Owner: rbhagwat@nvidia.com. Date: 2026-06-09.

## 1. Goal & rationale

Reimplement the standalone VIOS **sensor management microservice** in Python so that public/OSS
developers (the module is now Apache-2.0) can read, understand, and extend it without the C++/NVIDIA
native toolchain. Functional behavior must not change: the Python service is a drop-in replacement
behind the same external contracts.

## 2. Scope

**In scope:** the `module=sensor` build artifact (`libnvsensormanagement.so` + `launch_vst`) as it runs
as an independent container in `deployment/scaling/` — a pure **control-plane** service: ONVIF/RTSP/MMS
sensor discovery and control, DB persistence, REST API, and notification-event emission.

**Out of scope (stays C++):**
- The monolithic `SENSOR_MODULE` compiled into the stream-processor.
- All Jetson/CSI in-process media paths: `DecoderPool`/GStreamer (`sensor_management.cpp:592,606,739`),
  `vst_recorder::addStream`, `NativeStreamMonitor`. These are compile-time gated
  (`JETSON_PLATFORM`, `LIVE_STREAM_MODULE`, `REPLAY_STREAM_MODULE`, `STREAMBRIDGE_MODULE`,
  `ENABLE_NATIVE_STREAM_MONITOR`) and are **not linked** in a `module=sensor` build.
- The RTSP proxy / recording / WebRTC media plane — the sensor service only *requests* these from
  other microservices via a `camera_proxy` event; it never does the media work itself.

## 3. Key architectural fact (why this is feasible)

For ONVIF/RTSP sensors the C++ sensor service does **not** create an RTSP proxy itself. In
`getAndAddProxyUrl()` (`sensor_management.cpp:473-534`) it emits a `camera_proxy` notification event
to the **RTSP-server microservice**, which does the proxying. So the sensor MS boundary is:
control-plane logic + REST + DB + events. Everything below that line is another container's job and
is reached only through the four contracts frozen in §6.

```
            ┌────────────────────── sensor-ms (TO BE PYTHON) ──────────────────────┐
  REST  ───▶│  FastAPI  ─▶  SensorManagement logic  ─▶  adaptors (ONVIF/RTSP/MMS)   │
            │      │                 │                                              │
            │      ▼                 ▼                                              │
            │  session auth      SQLAlchemy (shared DB)   ─────────────────────────┼─▶ SQLite/Postgres
            │                        │                                              │
            │                        ▼                                              │
            │                 notification publisher ─────────────────────────────┼─▶ Redis/Kafka/MQTT
            └───────────────────────────────────────────────────────────────────────┘
                                     │ camera_proxy / camera_add / camera_remove / camera_streaming
                                     ▼
                          RTSP-server MS / pod / SDR (UNCHANGED C++)
```

## 4. Target stack (decided)

| Concern | Choice |
|---|---|
| Web | FastAPI + Uvicorn, Pydantic v2 models |
| Auth | session-cookie middleware reproducing `UserAuthHandler` (USER_SESSIONS table) |
| DB | SQLAlchemy Core/ORM over the **existing** SQLite + PostgreSQL schema |
| Crypto | `cryptography` (AES-256-CBC) reproducing the `vst_common` EVP scheme — see §6.2 |
| ONVIF control | `onvif-zeep-async` (MIT, async, Home-Assistant-proven) replacing proprietary `nvsoap` |
| WS-Discovery | in-house UDP multicast Probe (avoid LGPL `WSDiscovery` dep) — see §7 |
| Notifications | redis-py / confluent-kafka / paho-mqtt |
| Adaptors | Python ABC + `importlib` plugin loader (replaces `dlopen` C++ vtable) |

## 5. Target repository layout

> Note (2026-06-10 refactor): the sensor module now splits by language —
> `src/modules/sensor_management/cpp/` (the C++ implementation + its Makefile) and
> `src/modules/sensor_management/py/` (the Python service below). The top Makefile builds the C++
> from `cpp/` (`-I .../cpp/`, `$(MAKE) -C .../cpp/`); the docker-compose `sensor-py` build context
> points at `.../py`.

```
src/modules/sensor_management/py/
  pyproject.toml
  sensor_ms/
    main.py                 # FastAPI app, lifespan, route registration
    config.py               # config-key loading (§6.4), env overrides
    api/
      sensor_routes.py      # /api/v1/sensor/* (§6.1)
      health_routes.py      # /v1/live /v1/ready /v1/startup
      schemas.py            # Pydantic request/response models (§6.5)
      auth.py               # session-cookie middleware
    core/
      sensor_management.py  # orchestrator (start/stop/scan/add/delete/getInfo)
      sensor_monitoring.py  # discovery-event listener (onSensorFound/Changed/Removed)
      device_manager.py     # in-memory sensor cache + config
      proxy.py              # getAndAddProxyUrl -> emit camera_proxy event
    db/
      models.py             # SQLAlchemy models matching §6.2 DDL exactly
      crypto.py             # AES-256-CBC credential encrypt/decrypt (§6.2)
      repo.py               # insert/select/delete matching C++ SQL
    events/
      publisher.py          # broker-agnostic publish (§6.3)
      redis_pub.py / kafka_pub.py / mqtt_pub.py
    adaptors/
      base.py               # SensorControlAdaptor / SensorDiscoveryAdaptor ABCs (§6.6)
      loader.py             # importlib-based, reads adaptor_config.json
      onvif/                # onvif-zeep-async + discovery probe
      rtsp_streams.py milestone.py remote_device.py native.py streamer.py
  tests/
    contract/               # golden REST/DB/event fixtures captured in Phase 0
```

---

## 6. FROZEN CONTRACT (Phase 0 output)

> This section is the authoritative spec the Python service must satisfy. Field names, table/column
> names, cipher params, event names, and config keys are quoted from the current C++ source.
> **Verification note:** byte-level serialization details (JSON field *order*, timestamp format,
> bool-as-string vs bool) MUST also be confirmed by capturing live payloads from a running C++
> instance during Phase 0 — code reading establishes the shape, capture establishes the bytes.

### 6.1 REST API surface

**Authoritative source:** `doc/api/vst_sensor_management_ms/swagger.yaml` (OpenAPI 3.0.3, Apache-2.0).
The Python service MUST be generated/validated against this spec. The C++ `file:line` refs below are
for behavior tracing only — the swagger is the contract of record.

Server base `/api`, paths `/v1/sensor/...` (i.e. full path `/api/v1/sensor/...`). Main handler
`handleSensorAPI()` (`sensor_management_apis.cpp:273`) parses `/{sensorId}/{action}`.

**Three contract details that code-reading gets wrong — locked from the swagger:**
- **Content-Type:** every JSON response is emitted with HTTP header `Content-Type: text/plain` (body is
  JSON-encoded; clients parse body as JSON and ignore the header). Binary endpoints (jpeg/octet-stream)
  set proper types. FastAPI defaults to `application/json` — must be overridden to match byte-for-byte.
- **Auth:** `bearerAuth` (HTTP bearer token) on mutating endpoints (scan, add, replace, delete,
  POST info/settings/credentials/network/configuration, reboot, debug). GET endpoints are unauthenticated.
  (The C++ `UserAuthHandler`/multi-user `checkUser(user)` path still applies internally, but the documented
  external contract is bearer.)
- **Error envelope:** `{ "error_code": <string>, "error_message": <string> }` — **snake_case**. (camelCase
  `errorCode`/`errorMessage` appear only *inside* the `SensorStatus` object, not in the error envelope.)

**Sensor `type` enum in the public contract** (swagger `SensorInfo.type`): `sensor_rtsp`, `sensor_onvif`,
`sensor_streamer`, `sensor_mms`. (The C++ header `sensor_info.h:36` defines more internal constants —
`sensor_csi`, `sensor_file`, etc. — but only these four are documented as returned by `/sensor/list`.)

**Debug endpoints (gated by `enableDebugApis`)** not in the core flow but part of the surface:
`POST /v1/sensor/debug/plug`, `POST /v1/sensor/debug/unplug`, `GET /v1/sensor/debug/status?ip=`,
`GET /v1/sensor/debug/system/stats` (`SystemStats` schema: cpu/gpu/enc/dec usage, memory, tegrastats).

`GET /v1/sensor/qos` is **deprecated** and always returns null stats — the swagger states "the sensor-ms
has no RTSP server and never collects QoS data," confirming the control-plane-only scope (§2/§3). QoS
moved to `/api/v1/proxy/debug/qos` on the stream-processor.

| Method | Path | Handler (utils) | Notes |
|---|---|---|---|
| GET | `/api/v1/sensor/list` | `getSensorInfoList` (apis:562) | array of sensor objects (§6.5) |
| POST | `/api/v1/sensor/add` | `addSensor` (utils:565) | body §6.5; returns `{sensorId}` |
| DELETE | `/api/v1/sensor/{id}` | `deleteSensor` (utils:807) | returns `true` |
| GET | `/api/v1/sensor/{id}/info` | `getSensorInfo` (utils:886) | sensor object |
| POST | `/api/v1/sensor/{id}/info` | `setSensorInfo` (utils:927) | name/position/tags; name uniqueness |
| GET | `/api/v1/sensor/{id}/status` | `getSensorStatus` (utils:1060) | `{name,errorCode,errorMessage,state}` |
| GET | `/api/v1/sensor/{id}/settings` | `getSensorSettings` (utils:100) | per-stream Image/Encode |
| POST | `/api/v1/sensor/{id}/settings` | `setSensorSettings` (utils:289) | range-validated |
| GET | `/api/v1/sensor/{id}/network` | `getSensorNetworkInfo` (utils:1084) | ipv4/ipv6 |
| POST | `/api/v1/sensor/{id}/network` | `setSensorNetworkInfo` (utils:1109) | returns `{rebootNeeded}` |
| POST | `/api/v1/sensor/{id}/credentials` | `setSensorCredentials` (utils:1169) | `{username,password}` |
| POST | `/api/v1/sensor/{id}/reboot` | `rebootSensor` (utils:1236) | |
| POST | `/api/v1/sensor/{id}/replace` | `replaceSensorId` (utils:510) | `{sensorId}` (alias `deviceid`) |
| GET | `/api/v1/sensor/{id}/streams` | `getSensorStreamList` | |
| GET | `/api/v1/sensor/{id}/timelines` | `getRecordingTimelines` (utils:1565) | `startTime`/`endTime` query |
| GET | `/api/v1/sensor/streams` | `getSensorStreamListFromDB` | |
| GET | `/api/v1/sensor/status` | (apis:54) | map keyed by sensorId |
| POST | `/api/v1/sensor/scan` | `scanCameras(true)` | |
| GET/POST | `/api/v1/sensor/configuration` | `handleSensorConfiguration` (apis:106) | GET full config; POST `{deviceDiscoveryInterfaces,ntpServers}` |
| GET | `/api/v1/sensor/version` | `getVersion` (apis:210) | `{type,version}` |
| GET | `/api/v1/sensor/help` | `getSensorHelp` (apis:230) | array of paths |
| GET | `/api/v1/sensor/qos` | `getSensorQosInfo` (apis:540) | `{stats,numActiveRtspConnections,rtspServerTxBitrate}` |
| GET | `/v1/live`, `/v1/ready`, `/v1/startup` | health probes | |

Error object: `{ "error_code": <string>, "error_message": <string> }` (snake_case — see §6.1).
VmsErrorCode→HTTP mapping per the table in §6.5.7. Remote/edge DataChannel handlers
(`remote_sensor_control_apis.cpp`) mirror
add/remove/settings/status/credentials/info/netsettings over a WebRTC envelope
(`requestId`,`sensorId`,`requestMethod`,`data`) — in scope only if edge sync is enabled.

### 6.2 Database contract

DB selector: `GET_DB_INSTANCE` → PostgreSQL if `use_centralize_db` else SQLite
(`database/include/database.h:24`). Schema version table `DB_DETAILS`, `VST_DB_VERSION="0"`.
Schema evolves via `ALTER TABLE ADD COLUMN` if-missing (sqlite_helper.cpp:590) — Python must tolerate
extra/missing columns the same way.

**`SENSOR_DETAILS`** (sqlite_helper.cpp:338) — key columns:
`DEVICE_ID, SENSOR_ID (UNIQUE), SENSOR_HW_ID, USERNAME, PASSWORD (encrypted), NAME, IPADDRESS,
HARDWARE, MANUFACTURER, SERIAL_NUMBER, FIRMWARE_VERSION, HARDWARE_ID, LOCATION, TAGS, URL, TYPE,
POSITION, USERS, IS_REMOTE, REMOTE_DEVICE_ID, REMOTE_DEVICE_NAME, REMOTE_DEVICE_LOCATION,
HTTP_STATUS (int), SENSOR_STATUS (int), CREATED_DATE_TIME, MODIFIED_DATE_TIME`.

**`SENSOR_STREAMS`** (sqlite_helper.cpp:429) — `SENSOR_ID (FK), STREAM_ID (UNIQUE), STREAM_LIVE_URL,
STREAM_REPLAY_URL, STREAM_PROXY_URL, STREAM_RESOLUTION, STREAM_FRAMERATE, STREAM_ENCODING,
STREAM_STATUS (int), STREAM_TYPE (int), STREAM_ENCODING_PROFILE, STREAM_ENCODING_INTERVAl [sic],
STREAM_DURATION, STREAM_ISMAINSTREAM, STREAM_ISALWAYSRECORDING, STREAM_STORAGE_LOCATION (int default 0),
BITRATE, NUM_OF_FRAMES, AUDIO_CONTAINER, AUDIO_ENCODING, AUDIO_SAMPLE_RATE, AUDIO_BPS, AUDIO_CHANNELS,
STREAM_NAME, IS_BFRAMES_PRESENT (int default 0), CREATED/MODIFIED_DATE_TIME`.
Note the misspelled column `STREAM_ENCODING_INTERVAl` — reproduce verbatim.

Other tables the service reads/writes or coexists with: `VIDEO_RECORD_DETAILS`, `RECORDING_STATUS`,
`VIDEO_RECORD_SCHEDULE_DETAILS`, `USER_DETAILS`, `USER_SESSIONS`, `TEMP_VIDEO_FILES`, `DB_DETAILS`.
Delete cascades: `deleteSensorDetails` removes from SENSOR_STREAMS, RECORDING_STATUS, then SENSOR_DETAILS.
Writes use INSERT OR REPLACE (upsert); preserve CREATED_DATE_TIME, always bump MODIFIED_DATE_TIME.

**Credential encryption** (`vst_common.cpp:853-1019`, `utils.cpp:2447`):
- Cipher: **AES-256-CBC** (`EVP_aes_256_cbc`), PKCS#7 padding (`EVP_CIPHER_CTX_set_padding(ctx,1)`).
- Key: contents of the cert file `vst_data_path/<CA_CERTIFICATE_FILE_NAME>`, else
  `<SELF_SIGNED_CERTIFICATE_FILE_NAME>`, else hardcoded fallback
  `"WnZr4u7x!A%D*G-KaPdSgVkYp3s5v8y/"` (32 bytes). Key length set to `key.size()`.
- IV: the **SENSOR_ID** string, padded with `\0` or truncated to **16 bytes**.
- Encoding: AES output is `base64_encode`d before storage; decrypt = base64_decode then AES.
- No salt, no KDF. Python `cryptography` must reproduce this exactly to read existing rows.

### 6.3 Notification events

One broker active at a time (`NotificationFactory` singleton); selected by `use_message_broker`
(`"redis"|"kafka"|"mqtt"|""`). Nothing is sent if empty or `enable_notification=false`. All four
events publish to the **same** topic `message_broker_topic` (default `"vst.event"`); Kafka partition
key = `message_broker_payload_key` (default `"sensor.id"`).

Event name comes from `SensorStatusEvent` (`vst_common.cpp:290`):

| Enum | int | `change` string | Trigger |
|---|---|---|---|
| SensorStatusOffline | 0 | `camera_remove` | sensor delete |
| SensorStatusOnline | 1 | `camera_add` | sensor online (discovery/add) |
| SensorStatusStreaming | 2 | `camera_streaming` | CSI stream active |
| SensorStatusProxy | 3 | `camera_proxy` | RTSP proxy requested (main stream) |

**VERIFIED against a live deployment (2026-06-09)** — captured from the `vst_events` Redis stream.
Transport: `XADD <message_broker_topic> * <message_broker_payload_key> <json>`; observed stream key
`vst_events` (REDIS_MSG_KEY), entry field name `sensor.id` (payload key). Serialization is **jsoncpp:
keys sorted ALPHABETICALLY, compact (no spaces)** — reproduce with
`json.dumps(payload, sort_keys=True, separators=(",", ":"))`. `created_at` is ISO8601 UTC second
precision (`2026-06-09T07:49:54Z`).

Exact captured payloads (note alphabetical key order, which differs from the C++ source insertion order):
```
camera_add   : {"alert_type":"camera_status_change","created_at":"...","event":{"camera_id":"...","camera_name":"...","camera_url":"","change":"camera_add","tags":""},"source":"vst"}
camera_proxy : {"alert_type":"camera_status_change","created_at":"...","event":{"camera_id":"...","camera_name":"...","camera_url":"rtsp://USER:PASS@host:554/path","change":"camera_proxy","metadata":{"codec":"h264","framerate":"","resolution":""},"tags":""},"source":"vst"}
camera_remove: {"alert_type":"camera_status_change","created_at":"...","event":{"camera_id":"...","camera_name":"...","camera_url":"","change":"camera_remove","tags":""},"source":"vst"}
```
- `camera_url`: **empty** for `camera_add` AND `camera_remove`; URL **with credentials** for `camera_proxy`.
- `metadata` (`{codec,framerate,resolution}`, alphabetical) present only on `camera_proxy`/`camera_streaming`;
  **absent** on `camera_add` and `camera_remove`.
- `camera_streaming` (CSI-only) not reproduced live (needs a CSI sensor); document from code, same shape as proxy.
- Golden strings are committed in `vios-sensor-py/tests/test_events.py` and enforced byte-for-byte.

### 6.4 Config keys (≈47, read by sensor MS)

Loaded from `configs/vst_config.json` with env overrides (`config.cpp`). Groups:
- **Broker/notify:** `enable_notification`, `use_message_broker`, `enable_notification_consumer`,
  `use_message_broker_consumer`, `message_broker_topic` (`vst.event`), `message_broker_topic_consumer`,
  `message_broker_payload_key` (`sensor.id`), `message_broker_metadata_topic`, `redis_server_env_var`,
  `kafka_server_address`, `mqtt_broker_address`.
- **Discovery:** `sensor_discovery_timeout` (10), `sensor_discovery_freq_secs` (15),
  `onvif_request_timeout_secs` (10), `onvif_sensor_time_sync_interval_secs` (60),
  `onvif_sensor_time_sync_compensation_ms` (20), `sensor_discovery_interfaces` ([]),
  `max_sensors_supported` (8), `enable_camera_auto_discovery` (computed).
- **RTSP/network:** `rtsp_server_port`, `rtsp_preferred_network_iface`, `rtsp_streaming_over_tcp`,
  `server_domain_name`, `use_reverse_proxy`, `reverse_proxy_server_address`.
- **Remote VST:** `remote_vst_address` (edge sync; optional).
- **IPC:** `enable_ipc_path`, `ipc_socket_path` (Jetson media path — out of scope).
- **DB:** `centralize_db_name`, `centralize_db_username`, `centralize_remote_db_password`,
  `centralize_remote_db_hostaddr`, `centralize_remote_db_port`, `use_centralize_db`,
  `use_centralize_local_db`, `max_centralize_db_conn`.
- **NTP:** `ntpServers`, `use_sensor_ntp_time`.
- **Codec defaults:** `default_bitrate` (8000), `default_framerate` (30), `default_resolution`
  (1920x1080), `default_gov_length` (60), `default_profile` (Main), `default_quality`,
  `default_encoding_interval`, `video_codecs`, `audio_codecs`.
- **Identity:** `device_name`, `device_location`.

### 6.5 Data model (Pydantic targets)

Sensor types (`sensor_info.h:36`): `sensor_onvif`, `sensor_mms_onvif`, `sensor_rtsp`,
`sensor_nvstream`, `sensor_udp`, `sensor_webrtc`, `sensor_generic`, `sensor_edge`, `sensor_csi`,
`sensor_file`.

`/sensor/list` & `/sensor/{id}/info` object keys: `sensorId, name, sensorIp, hardware, manufacturer,
firmwareVersion, serialNumber, hardwareId, location, tags, isRemoteSensor, remoteDeviceId,
remoteDeviceName, remoteDeviceLocation, position{origin{latitude,longitude},
geoLocation{latitude,longitude}, coordinates{x,y}, direction, depth, fieldOfView}, state
("online"|"offline"|"removed"), isTimelinePresent, type`.

Stream object keys (device_manager.cpp:1732): `name, streamId, isMain, storageLocation, url,
vodUrl, ipc_url (Jetson), type, metadata{resolution, codec, bitrate, framerate, govlength}`.

`/add` body: `sensorUrl` (rtsp), `sensorIp`, `username`, `password`, `name`, `location`, `tags`,
`hardware`, `manufacturer`, `serialNumber`, `firmwareVersion`, `hardwareId`, `encoding`, `framerate`,
`width`, `height`, `container`, `verifyRtsp`, `isRemoteSensor`, `remoteDeviceId`. Limits:
MAX_FRAMERATE 60, MIN/MAX_BITRATE 64/20480, MIN/MAX_GOVLENGTH 2/255, MIN/MAX_QUALITY 0/6,
MIN/MAX_ENCODING_INTERVAL 1/240.

Enums to port with string serializations: `SensorStatusEvent`, `StreamType`
(Http/Hls/Rtsp/FileDownload/Udp/Webrtc/Native/NotSupported/Unknown), `StreamStatus` (-1..5),
`StreamDirection` (-1..2), `StreamStorageType` (Local/Cloud/Unknown), `PTZAction` (PanTilt/Zoom/Unknown),
`AuthenticationMethods` (bitmask: NONE/USERNAME_TOKEN/DIGEST).

**§6.5.7 VmsErrorCode → HTTP** (utils.cpp:1163): NoError 200; CameraUnauthorized 401;
InvalidParameter/MethodNotAllowed/VMSNotSupported 400; CameraNotFound 404;
Communication/VMSInternal 500; plus ResourceConflict 409, PayloadTooLarge 413,
UnsupportedMediaType 415, UnprocessableEntity 422, TooManyRequests 429, ServiceUnavailable 503.

### 6.6 Adaptor interface (→ Python ABC)

`ISensorControlInterface` (sensor_control_adaptor.h:78) methods to mirror: `connect`,
`getSensorStreamInfo` (list + single), `synchronizeSensorTime`, `getSensorStatus` (single + batch),
`rebootSensor`, `isServerOnline`, `get/setSensorImageSettings`, `get/setNetworkInfo`,
`get/setSensorEncodeSettings`, `getStreamSettings`, `setPTZ`, `getPTZ`, `validateCredentials`,
`addSensor`, `deleteSensor`, `setSensorInfo`, `getRecordingTimelines`, `set/getCacheSensorList`,
`setAdaptorInfo`. Default returns (-1 / NoError / true / 0) must be preserved.

`ISensorDiscoveryInterface` (sensor_discovery_adaptor.h:43): `start`, `stop`, `searchSensor`,
register/deregister listener, `publishOnSensorFound/Changed/Removed`, cache list accessors.
`ISensorDiscoveryEvent`: `onSensorFound(SensorInfo)`, `onSensorChanged(SensorInfo)`,
`onSensorRemoved(sensor_id)`, `notifyEvent`, `refreshSensorList`.

`AdaptorInfo`: `m_id, m_name, m_type (vst|mms|streamer|event), m_user, m_password, m_port,
m_ipaddress, m_url`.

Adaptor registry `configs/adaptor_config.json` (`vst` array): each entry has
`enabled, id, name, type, ip, user, password, port, need_stream_monitoring, need_rtsp_server,
need_recording, need_storage_management, control_adaptor_lib_path, discovery_adaptor_lib_path,
media_adaptor_lib_path`. Selection: `$ADAPTOR` env or first `enabled`. Python loader replaces
`.so` paths with `importlib` module references; the `need_*` flags continue to gate which events the
service emits (e.g. `need_rtsp_server` decides camera_proxy vs camera_streaming).

---

## 7. ONVIF strategy

Replace all `nvsoap` usage (`onvif_client.cpp` is `getNvSoap()->GetProfiles/GetMediaUri/
GetCapabilities/GetServices/GetServiceCapabilities/GetDeviceInformation/...`) with **`onvif-zeep-async`**
(MIT). Map: `GetProfiles`→media profiles, `GetMediaUri`→`live_url`, `GetDeviceInformation`→
hardware/manufacturer/firmware/serial, PTZ via the PTZ service, imaging via the Imaging service.
Digest/WS-UsernameToken auth and SHA-256 hashing handled by zeep + `cryptography`.

**WS-Discovery:** the C++ path is multicast `Probe` to `239.255.255.250:3702`. Implement in-house
(~150 LOC, asyncio UDP) to (a) avoid the LGPLv3 `WSDiscovery` dependency and (b) match current
discovery semantics (`sensor_discovery_interfaces`, timeout/frequency keys). Fall back to MIT
`onvif-python`'s discovery only if the in-house probe underperforms.

Validate Phase 3 against **real cameras** (digest auth, GetProfiles, GetMediaUri, PTZ ranges), not mocks.

---

## 8. Module port map

| C++ | Python | Difficulty |
|---|---|---|
| `SensorManagement` | `core/sensor_management.py` | medium (orchestration, locks→asyncio) |
| `SensorMonitoring` | `core/sensor_monitoring.py` | low |
| `SensorControl` (adaptor wrapper) | folded into adaptor base/loader | low |
| `DeviceManager` cache | `core/device_manager.py` | low |
| `SensorManagementApis` | `api/sensor_routes.py` | medium (exact JSON parity) |
| `sensor_management_utils` | split across api/core | medium |
| DB helpers + crypto | `db/` | medium (crypto parity critical) |
| NotificationFactory + publishers | `events/` | low |
| onvif adaptor (nvsoap) | `adaptors/onvif/` | **high (only real risk)** |
| milestone / rtsp_streams / remote_device / native / streamer | `adaptors/*.py` | low (pure logic/HTTP) |
| vms_media (gRPC) | optional `adaptors/vms_media.py` (grpcio + regen proto) | medium; defer |

## 9. Phases & verification gates

- **P0 Freeze contract** (this doc + captured golden fixtures): OpenAPI dump, DB DDL, EVP params,
  recorded event payloads. Gate: fixtures committed under `tests/contract/`.
- **P1 Skeleton + DB/crypto parity.** Gate: Python reads a sensor a C++ instance wrote (incl. password
  decrypt); `/sensor/list` JSON matches golden fixture.
- **P2 Control logic + easy adaptors + event publishers.** Gate: add/delete/scan/list/status flows pass
  against golden REST + event fixtures; emitted events diff-clean.
- **P3 ONVIF + discovery.** Gate: real-camera matrix (discover, profiles, media URI, PTZ, credentials).
- **P4 Differential validation.** C++ vs Python side-by-side on same DB + bus + cameras; zero diff;
  full BDD via `/vios-sqa` green against the Python service.
- **P5 Cutover.** Swap the sensor container image in `deployment/scaling/`; keep C++ image for instant
  rollback. RTSP-server/pod/Envoy/SDR untouched (contracts preserved).

## 10. Risks

1. **ONVIF camera compatibility** — top risk; mitigated by onvif-zeep-async's HA pedigree + real-camera
   testing in P3.
2. **Crypto/serialization byte-parity** — credentials and event JSON must match exactly; mitigated by
   golden fixtures + differential run (P4).
3. **Concurrency model shift** — C++ mutexes → asyncio; document task affinity; avoid blocking the loop
   on SOAP/HTTP (use async clients).
4. **Hidden monolith coupling** — if any consumer depends on a behavior only present in the C++ build
   beyond the four contracts, P4 differential run surfaces it before cutover.
5. **LGPL** — moot under in-house WS-Discovery; if `WSDiscovery` lib is later desired, requires NVIDIA
   OSS-compliance sign-off.
