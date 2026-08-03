# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy

import pytest

from lib.lifecycle.http_header import (
    ACTION_ADD,
    ACTION_DELETE,
    ACTION_REPROVISION,
    MODE_HTTP_HEADER,
    MODE_MESSAGE_BUS,
    build_http_lifecycle_event_payload,
    extract_header_value,
    is_http_header_lifecycle_mode,
    is_message_bus_lifecycle_mode,
    match_http_lifecycle_action,
    normalize_lifecycle_ingress_mode,
)


@pytest.fixture
def lifecycle_config():
    return {
        "WDM_LIFECYCLE_INGRESS_MODE": "http",
        "WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER": "streamid",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH": "/sdrc/v1/streams",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD": "POST",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH": "/sdrc/v1/streams",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD": "DELETE",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH": "/sdrc/v1/streams/reprovision",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD": "POST",
        "WDM_EVENT_OBJECT_FIELD": "event",
        "WDM_WL_ID_FIELD": "camera_id",
        "WDM_WL_CHANGE_FIELD": "change",
        "WDM_WL_CHANGE_ID_ADD": "camera_streaming",
        "WDM_WL_CHANGE_ID_DEL": "camera_remove",
        "WDM_WL_CHANGE_ID_REPROVISION": "reprovision",
    }


class TestLifecycleIngressMode:
    def test_defaults_to_message_bus(self):
        assert normalize_lifecycle_ingress_mode(None) == MODE_MESSAGE_BUS
        assert normalize_lifecycle_ingress_mode("") == MODE_MESSAGE_BUS

    def test_accepts_config_aliases(self):
        assert normalize_lifecycle_ingress_mode("message-bus") == MODE_MESSAGE_BUS
        assert normalize_lifecycle_ingress_mode("message_bus") == MODE_MESSAGE_BUS
        assert normalize_lifecycle_ingress_mode("http") == MODE_HTTP_HEADER
        assert normalize_lifecycle_ingress_mode("http-header") == MODE_HTTP_HEADER

    def test_rejects_unknown_modes(self):
        with pytest.raises(ValueError):
            normalize_lifecycle_ingress_mode("grpc")

    def test_mode_helpers_are_exclusive(self, lifecycle_config):
        assert is_http_header_lifecycle_mode(lifecycle_config)
        assert not is_message_bus_lifecycle_mode(lifecycle_config)

        lifecycle_config["WDM_LIFECYCLE_INGRESS_MODE"] = "message-bus"
        assert is_message_bus_lifecycle_mode(lifecycle_config)
        assert not is_http_header_lifecycle_mode(lifecycle_config)


class TestHttpLifecycleClassification:
    def test_classifies_action_from_configured_path_and_method(self, lifecycle_config):
        assert (
            match_http_lifecycle_action("POST", "/sdrc/v1/streams", lifecycle_config)
            == ACTION_ADD
        )
        assert (
            match_http_lifecycle_action("DELETE", "/sdrc/v1/streams", lifecycle_config)
            == ACTION_DELETE
        )
        assert (
            match_http_lifecycle_action(
                "POST", "/sdrc/v1/streams/reprovision", lifecycle_config
            )
            == ACTION_REPROVISION
        )

    def test_matches_backend_path_after_sdrc_prefix_rewrite(self, lifecycle_config):
        assert (
            match_http_lifecycle_action("POST", "/v1/streams", lifecycle_config)
            == ACTION_ADD
        )
        assert (
            match_http_lifecycle_action("DELETE", "/v1/streams", lifecycle_config)
            == ACTION_DELETE
        )

    def test_does_not_infer_action_from_unconfigured_path(self, lifecycle_config):
        assert match_http_lifecycle_action("POST", "/other", lifecycle_config) is None

    def test_body_disambiguates_shared_add_reprovision_binding(self, lifecycle_config):
        lifecycle_config["WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH"] = "/api/v1/stream/add"
        lifecycle_config["WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH"] = "/api/v1/stream/add"

        assert (
            match_http_lifecycle_action(
                "POST", "/api/v1/stream/add", lifecycle_config, has_body=True
            )
            == ACTION_ADD
        )
        assert (
            match_http_lifecycle_action(
                "POST", "/api/v1/stream/add", lifecycle_config, has_body=False
            )
            == ACTION_REPROVISION
        )

    def test_rejects_ambiguous_non_body_aware_duplicate_binding(
        self, lifecycle_config
    ):
        lifecycle_config["WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH"] = "/api/v1/stream"
        lifecycle_config["WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD"] = "POST"
        lifecycle_config["WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH"] = "/api/v1/stream"
        lifecycle_config["WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD"] = "POST"

        with pytest.raises(ValueError, match="only add/reprovision"):
            match_http_lifecycle_action(
                "POST", "/api/v1/stream", lifecycle_config, has_body=True
            )


class TestHttpLifecycleHeadersAndPayload:
    def test_extracts_configured_header_case_insensitively(self):
        headers = {"StreamId": " camera-001 "}
        assert extract_header_value(headers, "streamid") == "camera-001"
        assert extract_header_value(headers, "x-stream-id") is None

    def test_payload_uses_header_identity_and_action_not_body_fields(
        self, lifecycle_config
    ):
        body = {
            "event": {
                "camera_id": "body-camera",
                "camera_url": "rtsp://example.local/camera-001",
                "change": "body-change",
                "metadata": {"site": "warehouse-a"},
            }
        }
        original = copy.deepcopy(body)

        payload = build_http_lifecycle_event_payload(
            lifecycle_config, ACTION_ADD, "camera-001", body
        )

        assert body == original
        assert payload["event"]["camera_id"] == "camera-001"
        assert payload["event"]["change"] == "camera_streaming"
        assert payload["event"]["camera_url"] == "rtsp://example.local/camera-001"
        assert payload["event"]["metadata"] == {"site": "warehouse-a"}

    def test_non_event_body_is_preserved_as_workload_payload(self, lifecycle_config):
        body = {"camera_url": "rtsp://example.local/camera-001"}

        payload = build_http_lifecycle_event_payload(
            lifecycle_config, ACTION_REPROVISION, "camera-001", body
        )

        assert payload["event"]["camera_id"] == "camera-001"
        assert payload["event"]["change"] == "reprovision"
        assert payload["event"]["payload"] == body
