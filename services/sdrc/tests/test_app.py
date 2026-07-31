# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Pytest tests for app.py Flask routes and helpers.
Use the client and app_module fixtures from conftest; do not import app at module level.
"""
import pytest


class TestHealthz:
    def test_healthz_returns_ok(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert "OK" in r.data.decode()


class TestReset:
    def test_reset_returns_ok(self, client):
        r = client.get("/reset")
        assert r.status_code == 200
        assert r.data.decode().strip() == "ok"


class TestConfigEndpoint:
    def test_get_config_returns_json(self, client, app_module):
        r = client.get("/get_config")
        assert r.status_code == 200
        assert r.content_type == "application/json"
        assert isinstance(r.get_json(), dict)


class TestReplicas:
    def test_replicas_returns_json(self, client):
        r = client.get("/replicas")
        assert r.status_code == 200
        data = r.get_json()
        assert "wl_object" in data
        assert "replicas" in data
        assert "wlobreplicas" in data


class TestGetWl:
    def test_getwl_no_id_returns_empty_list(self, client):
        r = client.get("/getwl")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_getwl_with_id_returns_spec_or_empty(self, client, app_module):
        app_module.cfg.getworkLoadSpecById.return_value = None
        r = client.get("/getwl?id=cam1")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_getwl_with_id_returns_spec_when_found(self, client, app_module):
        spec = [{"event": {"camera_id": "cam1", "camera_url": "rtsp://x"}}]
        app_module.cfg.getworkLoadSpecById.return_value = spec
        r = client.get("/getwl?id=cam1")
        assert r.status_code == 200
        assert r.get_json() == spec


class TestGetPodDns:
    def test_getpoddns_no_id_returns_empty_list(self, client):
        r = client.get("/getpoddns")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_getpoddns_with_id_returns_mapping(self, client, app_module):
        app_module.redisMsging.getIdPodMapping.return_value = "pod-0"
        app_module.redisMsging.getIdPodPodDnsMapping.return_value = "pod-0.dns"
        r = client.get("/getpoddns?id=cam1")
        assert r.status_code == 200
        data = r.get_json()
        assert data["id"] == "cam1"
        assert data["podname"] == "pod-0"
        assert data["poddns"] == "pod-0.dns"


class TestXDSRoutes:
    def test_clusters_post_returns_json(self, client):
        r = client.post("/v3/discovery:clusters")
        assert r.status_code == 200
        assert r.content_type == "application/json"

    def test_routes_post_returns_json(self, client):
        r = client.post("/v3/discovery:routes")
        assert r.status_code == 200
        assert r.content_type == "application/json"


class TestCurrentStreams:
    def test_current_distributed_streams_cache_returns_list(self, client, app_module):
        app_module.cfg.getAllStreams.return_value = {}
        r = client.get("/current_distributed_streams_cache")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_current_streamid_address_mapping_returns_dict(self, client):
        r = client.get("/current_streamid_address_mapping")
        assert r.status_code == 200
        assert isinstance(r.get_json(), dict)


class TestCacheMetadataUpdate:
    def test_cache_metadata_update_requires_json(self, client):
        r = client.post("/cache_metadata_update", data="not json")
        assert r.status_code == 400
        assert "JSON" in r.data.decode()

    def test_cache_metadata_update_requires_stream_id_and_metadata(self, client):
        r = client.post(
            "/cache_metadata_update",
            json={},
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert "stream_id" in r.data.decode() or "additional_metadata" in r.data.decode()

    def test_cache_metadata_update_stream_not_found_returns_400(self, client, app_module):
        app_module.cfg.getCacheInfoForStreamId.return_value = (None, None)
        r = client.post(
            "/cache_metadata_update",
            json={"stream_id": "s1", "additional_metadata": {"k": "v"}},
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert "not found" in r.data.decode()

    def test_cache_metadata_update_success(self, client, app_module):
        app_module.cfg.getCacheInfoForStreamId.return_value = (
            "pod-0",
            {"external_metadata": {}},
        )
        r = client.post(
            "/cache_metadata_update",
            json={"stream_id": "s1", "additional_metadata": {"key": "value"}},
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        assert "updated" in r.data.decode().lower()


class TestApplyMetadataPayload:
    def test_apply_metadata_payload_requires_json(self, client):
        r = client.post("/apply_metadata_payload", data="x")
        assert r.status_code == 400

    def test_apply_metadata_payload_requires_stream_id(self, client, app_module):
        app_module.change_field = "change"
        app_module.change_id_add = "camera_streaming"
        r = client.post(
            "/apply_metadata_payload",
            json={"event": {"camera_url": "rtsp://x"}},  # no camera_id
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert "stream_id" in r.data.decode()


class TestHttpHeaderLifecycle:
    def _enable_http_lifecycle(self, app_module):
        app_module.app.config.update(
            WDM_LIFECYCLE_INGRESS_MODE="http",
            WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER="streamid",
            WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH="/sdrc/v1/streams",
            WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD="POST",
            WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH="/sdrc/v1/streams",
            WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD="DELETE",
            WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH="/sdrc/v1/streams/reprovision",
            WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD="POST",
        )

    def test_configured_lifecycle_endpoint_is_inactive_in_message_bus_mode(
        self, client, app_module
    ):
        self._enable_http_lifecycle(app_module)
        app_module.app.config["WDM_LIFECYCLE_INGRESS_MODE"] = "message-bus"

        r = client.post(
            "/sdrc/v1/streams",
            json={"event": {"camera_id": "payload-id", "change": "ignored"}},
            headers={"streamid": "camera-001"},
        )

        assert r.status_code == 409
        assert r.get_json()["mode"] == "message-bus"

    def test_http_header_add_invokes_existing_provision_logic(
        self, client, app_module, monkeypatch
    ):
        self._enable_http_lifecycle(app_module)
        calls = []

        def fake_provision(k8swlob_name, data, original_json, parent_context=None):
            calls.append((k8swlob_name, data, original_json, parent_context))

        monkeypatch.setattr(app_module, "provisionStreamRedis", fake_provision)

        r = client.post(
            "/sdrc/v1/streams",
            json={
                "event": {
                    "camera_id": "payload-id",
                    "camera_url": "rtsp://example.local/camera-001",
                    "change": "ignored",
                }
            },
            headers={"streamid": "camera-001"},
        )

        assert r.status_code == 200
        assert r.get_json()["action"] == "add"
        assert calls
        _, data, original_json, _ = calls[0]
        assert data["camera_id"] == "camera-001"
        assert data["change"] == "camera_streaming"
        assert original_json["event"]["camera_id"] == "camera-001"

    def test_http_header_lifecycle_requires_configured_header(self, client, app_module):
        self._enable_http_lifecycle(app_module)

        r = client.post("/sdrc/v1/streams", json={"event": {}})

        assert r.status_code == 400
        assert "streamid" in r.get_json()["error"]

    def test_http_header_delete_requires_existing_stream(self, client, app_module):
        self._enable_http_lifecycle(app_module)
        app_module.cfg.getworkLoadSpecById.return_value = None
        app_module.redisMsging.getIdPodMapping.return_value = None

        r = client.delete("/sdrc/v1/streams", headers={"streamid": "missing-camera"})

        assert r.status_code == 404
        assert r.get_json()["stream_id"] == "missing-camera"

    def test_http_header_delete_invokes_existing_deprovision_logic(
        self, client, app_module, monkeypatch
    ):
        self._enable_http_lifecycle(app_module)
        app_module.cfg.getworkLoadSpecById.return_value = [
            {"event": {"camera_id": "camera-001", "camera_url": "rtsp://x"}}
        ]
        app_module.redisMsging.getIdPodMapping.return_value = "pod-0"
        calls = []

        def fake_deprovision(k8swlob_name, data, original_json, parent_context):
            calls.append((k8swlob_name, data, original_json, parent_context))

        monkeypatch.setattr(app_module, "deprovisionStreamRedis", fake_deprovision)

        r = client.delete("/sdrc/v1/streams", headers={"streamid": "camera-001"})

        assert r.status_code == 200
        assert r.get_json()["action"] == "delete"
        assert calls
        _, data, original_json, _ = calls[0]
        assert data["camera_id"] == "camera-001"
        assert data["change"] == "camera_remove"
        assert original_json["event"]["camera_id"] == "camera-001"

    def test_http_header_reprovision_uses_cached_stream_state(
        self, client, app_module, monkeypatch
    ):
        self._enable_http_lifecycle(app_module)
        app_module.cfg.getworkLoadSpecById.return_value = [
            {
                "event": {
                    "camera_id": "camera-001",
                    "camera_url": "rtsp://cached",
                    "camera_name": "Dock Camera 1",
                }
            }
        ]
        app_module.redisMsging.getIdPodMapping.return_value = "pod-0"
        calls = []

        def fake_reprovision(k8swlob_name, data, original_json, parent_context):
            calls.append((k8swlob_name, data, original_json, parent_context))

        monkeypatch.setattr(app_module, "reprovisionStreamRedis", fake_reprovision)

        r = client.post(
            "/sdrc/v1/streams/reprovision",
            headers={"streamid": "camera-001"},
        )

        assert r.status_code == 200
        assert r.get_json()["action"] == "reprovision"
        assert calls
        _, data, original_json, _ = calls[0]
        assert data["camera_id"] == "camera-001"
        assert data["camera_url"] == "rtsp://cached"
        assert data["change"] == "reprovision"
        assert original_json["event"]["camera_name"] == "Dock Camera 1"


class TestGetWlReplicaData:
    def test_get_wl_replica_data_returns_json(self, client):
        r = client.get("/get_wl_replica_data")
        assert r.status_code == 200
        data = r.get_json()
        assert "wl_object" in data
        assert "standby_pods_configured" in data


class TestMetrics:
    def test_metrics_returns_prometheus_text(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.content_type


class TestMergeDicts:
    """Tests for _merge_dicts helper (pure function in app.py)."""

    def test_merge_dicts_prefer_dict1(self, app_module):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 20, "c": 3}
        out = app_module._merge_dicts(d1, d2, prefer_dict1=True)
        assert out == {"a": 1, "b": 2, "c": 3}

    def test_merge_dicts_prefer_dict2(self, app_module):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 20, "c": 3}
        out = app_module._merge_dicts(d1, d2, prefer_dict1=False)
        assert out["b"] == 20
        assert out["a"] == 1
        assert out["c"] == 3

    def test_merge_dicts_empty_dict2(self, app_module):
        d1 = {"a": 1}
        d2 = {}
        out = app_module._merge_dicts(d1, d2, prefer_dict1=True)
        assert out == {"a": 1}


class TestMaxReplicaException:
    def test_max_replica_exception_message(self, app_module):
        e = app_module.MaxReplicaException(5)
        assert "5" in str(e)
