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

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests


SDRC_ROOT = Path(__file__).resolve().parents[1]
if str(SDRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SDRC_ROOT))


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = ""

    def json(self):
        return {}


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class _FakeFlaskConfig(dict):
    def from_object(self, obj):
        for name in dir(obj):
            if name.isupper():
                self[name] = getattr(obj, name)


class _FakeFlask:
    def __init__(self, *args, **kwargs):
        self.config = _FakeFlaskConfig()
        self.logger = MagicMock()
        self.wsgi_app = self

    def before_request(self, func):
        return func

    def after_request(self, func):
        return func

    def route(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator


class _FakeLazySettings:
    def __init__(self, module_name):
        self.module_name = module_name

    def Config(self):
        return importlib.import_module(self.module_name).Config


class _FakeKafkaBus:
    def __init__(self, *args, **kwargs):
        self.consumer = MagicMock()
        self.handlers = {}

    def handle(self, topic):
        def decorator(func):
            self.handlers[topic] = func
            return func
        return decorator


@pytest.fixture
def app_module(monkeypatch, request):
    kafka_enabled = bool(getattr(request, "param", False))
    fake_bus = _FakeKafkaBus()
    mock_cfg = MagicMock()
    mock_cfg.getworkLoadSpecById.return_value = None
    mock_cfg.getpods.return_value = []
    mock_cfg.getworkLoadSpec.return_value = None
    mock_cfg.getSpecCount.return_value = 0
    mock_cfg.eraseSpecContent.return_value = None
    mock_cfg.erasePodSpecContent.return_value = None
    mock_cfg.getworkLoadSpecs.return_value = "[]"
    mock_cfg.getAllStreams.return_value = {}
    mock_cfg.getpodsCount.return_value = 0
    mock_cfg.addWorkLoadSpec.return_value = None
    mock_cfg.deleteFromWorkLoadSpec.return_value = None
    mock_cfg.updateWorkLoadSpec.return_value = None
    mock_cfg.getCacheInfoForStreamId.return_value = ("pod-0", {"external_metadata": {}})

    mock_cluster = MagicMock()
    sts = MagicMock()
    sts.status.replicas = 1
    mock_cluster.getStatefulSets.return_value = sts
    mock_cluster.getReadyReplicas.return_value = 1
    mock_cluster.getWorkloadObjects.return_value = None
    mock_cluster.getPodIps.return_value = []
    mock_cluster.get_current_allocation_configs.return_value = {}
    mock_cluster.get_current_allocation_pod_names.return_value = []
    mock_cluster.find_unallocated_pod.return_value = {
        "podName": "pod-0",
        "podIp": "127.0.0.1",
        "podPort": 5000,
    }
    mock_cluster.update_current_allocation_configs.return_value = 1
    mock_cluster.ifPodDown.return_value = False
    mock_cluster.updateRouteMapping.return_value = None
    mock_cluster.scaleStatefulsetPods.return_value = None
    mock_cluster.get_pod_info_by_encoded_name.return_value = {"podName": "pod-0"}
    mock_cluster.watchPodState.return_value = iter([])
    mock_cluster.get_podname_keys.return_value = []

    mock_redis = MagicMock()
    mock_redis.getIdPodMapping.return_value = None
    mock_redis.getIdPodPodDnsMapping.return_value = None
    mock_redis.getCurrentMapping.return_value = {}
    mock_redis.clearAllData.return_value = None
    mock_redis.clearPodData.return_value = None
    mock_redis.getMessageValue.side_effect = lambda msg: msg.get("event")
    mock_redis.getRedisConnection.return_value = MagicMock()
    mock_redis.publishMessage.return_value = None
    mock_redis.message_err.return_value = None

    mock_envy = MagicMock()
    mock_envy.clusterXDs.return_value = []
    mock_envy.routeXDs.return_value = []

    mock_pc = MagicMock()
    mock_pc.applyConfig.return_value = _Resp(200)

    mock_kfk = MagicMock()

    mock_tracing = _module("lib.tracing")
    span = MagicMock()
    context = MagicMock()
    mock_tracing.StatusCode = types.SimpleNamespace(ERROR="ERROR")
    mock_tracing.create_parent_span = MagicMock(return_value=(span, context))
    mock_tracing.create_child_span = MagicMock(return_value=(span, context))
    mock_tracing.inject_context = MagicMock(return_value={})
    mock_tracing.propagate_context = MagicMock()
    mock_tracing.delete_context_entry = MagicMock()

    monkeypatch.setenv("WDM_KFK_ENABLE", "true" if kafka_enabled else "false")
    monkeypatch.setenv("WDM_CACHE_METHOD", "file")
    monkeypatch.setenv("WDM_CLUSTER_TYPE", "docker")
    monkeypatch.setenv("WDM_RESET_PRELOAD_FILE", "false")
    monkeypatch.setenv("WDM_DISABLE_WERKZEUG_LOGGING", "true")
    monkeypatch.setenv("WDM_INITIALIZE_FROM_VST", "false")
    monkeypatch.setenv("WDM_HANDLE_CONFIG_EVENTS", "true")
    monkeypatch.setenv("WDM_TRUST_PROXY_HEADERS", "false")

    request = types.SimpleNamespace(
        headers={},
        json=None,
        method="GET",
        path="/",
        query_string=b"",
        get_data=lambda: b"",
    )
    monkeypatch.setitem(
        sys.modules,
        "flask",
        _module(
            "flask",
            Flask=_FakeFlask,
            Response=MagicMock(),
            g=types.SimpleNamespace(),
            has_request_context=MagicMock(return_value=False),
            render_template=MagicMock(return_value=""),
            request=request,
            stream_with_context=lambda value: value,
            jsonify=lambda *args, **kwargs: args[0] if args else kwargs,
        ),
    )
    monkeypatch.setitem(sys.modules, "flask_kafka", _module("flask_kafka", FlaskKafka=MagicMock(return_value=fake_bus)))
    monkeypatch.setitem(sys.modules, "simple_settings", _module("simple_settings", LazySettings=_FakeLazySettings))
    monkeypatch.setitem(sys.modules, "yaml", _module("yaml"))
    monkeypatch.setitem(
        sys.modules,
        "prometheus_client",
        _module("prometheus_client", Gauge=MagicMock(), generate_latest=MagicMock(return_value=b"")),
    )
    monkeypatch.setitem(
        sys.modules,
        "lib.wdm_swagger_ui",
        _module(
            "lib.wdm_swagger_ui",
            openapi_public_server_root=MagicMock(return_value="/"),
            register_wdm_swagger_ui=MagicMock(),
        ),
    )

    monkeypatch.setitem(sys.modules, "lib.parameters", _module("lib.parameters", configserver=MagicMock(return_value=mock_cfg)))
    monkeypatch.setitem(
        sys.modules,
        "lib.parameters.redisconfig",
        _module(
            "lib.parameters.redisconfig",
            clear_stale_redis_workload_spec_lock_keys=MagicMock(),
            redisconfig=MagicMock(return_value=mock_cfg),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "lib.podprovisioner.kubernetes.cluster",
        _module("lib.podprovisioner.kubernetes.cluster", cluster=MagicMock(return_value=mock_cluster)),
    )
    monkeypatch.setitem(
        sys.modules,
        "lib.podprovisioner.provisionconfig",
        _module("lib.podprovisioner.provisionconfig", provisionconfig=MagicMock(return_value=mock_pc)),
    )
    monkeypatch.setitem(sys.modules, "lib.messaging", _module("lib.messaging", kafka=MagicMock(return_value=mock_kfk)))
    monkeypatch.setitem(
        sys.modules,
        "lib.messaging.redisMessaging",
        _module("lib.messaging.redisMessaging", Consumer=MagicMock(), redisMessaging=MagicMock(return_value=mock_redis)),
    )
    monkeypatch.setitem(sys.modules, "lib.tracing", mock_tracing)
    monkeypatch.setitem(sys.modules, "lib.xDS", _module("lib.xDS", envoyxDS=MagicMock(return_value=mock_envy)))
    monkeypatch.setitem(sys.modules, "lib.xDS.envoyxDS", _module("lib.xDS.envoyxDS", envoyxDS=MagicMock(return_value=mock_envy)))
    monkeypatch.setitem(
        sys.modules,
        "lib.xDS.grpc_xds_server",
        _module(
            "lib.xDS.grpc_xds_server",
            can_start_grpc_xds_server=MagicMock(return_value=False),
            is_grpc_xds_enabled=MagicMock(return_value=False),
            notify_xds_update=MagicMock(),
            start_grpc_xds_server=MagicMock(),
        ),
    )

    sys.modules.pop("app", None)
    sys.modules.pop("config", None)
    app_mod = importlib.import_module("app")
    app_mod.app.config["TESTING"] = True
    app_mod._test_fake_bus = fake_bus
    app_mod._test_mock_kfk = mock_kfk
    yield app_mod
    sys.modules.pop("app", None)


def _config_event(name="dock-a", remove_config=False):
    event = {"camera_id": "cam-1", "change": "config", "name": name}
    if remove_config:
        event["remove_config"] = True
    return {"event": event}


def test_should_handle_config_effective_flag_matrix(app_module):
    app_module.app.config["WDM_ENABLE_REGEX_MAPPING"] = False
    app_module.app.config["WDM_HANDLE_CONFIG_EVENTS"] = None
    assert app_module.should_handle_config_events() is False

    app_module.app.config["WDM_ENABLE_REGEX_MAPPING"] = True
    assert app_module.should_handle_config_events() is True

    app_module.app.config["WDM_HANDLE_CONFIG_EVENTS"] = False
    assert app_module.should_handle_config_events() is False

    app_module.app.config["WDM_HANDLE_CONFIG_EVENTS"] = True
    app_module.app.config["WDM_ENABLE_REGEX_MAPPING"] = False
    assert app_module.should_handle_config_events() is True


def test_pod_configure_skips_when_disabled(app_module):
    app_module.app.config["WDM_HANDLE_CONFIG_EVENTS"] = False
    payload = _config_event()

    result = app_module.podConfigureRedis("test-workload", payload["event"], payload)

    assert result == app_module.CONFIGURE_NOOP
    app_module.pc.applyConfig.assert_not_called()
    app_module.curr_cluster.update_current_allocation_configs.assert_not_called()


def test_pod_configure_missing_name_failed_no_alloc(app_module):
    payload = {"event": {"camera_id": "cam-1", "change": "config"}}

    result = app_module.podConfigureRedis("test-workload", payload["event"], payload)

    assert result == app_module.CONFIGURE_FAILED
    app_module.pc.applyConfig.assert_not_called()
    app_module.curr_cluster.update_current_allocation_configs.assert_not_called()
    app_module.redisMsging.message_err.assert_called_once()


def test_pod_configure_none_response_failed_no_alloc(app_module):
    app_module.pc.applyConfig.return_value = None
    payload = _config_event()

    result = app_module.podConfigureRedis("test-workload", payload["event"], payload)

    assert result == app_module.CONFIGURE_FAILED
    app_module.curr_cluster.update_current_allocation_configs.assert_not_called()
    app_module.redisMsging.message_err.assert_called_once()


def test_pod_configure_http_error_failed_no_alloc(app_module):
    app_module.pc.applyConfig.return_value = _Resp(404)
    payload = _config_event()

    result = app_module.podConfigureRedis("test-workload", payload["event"], payload)

    assert result == app_module.CONFIGURE_FAILED
    app_module.curr_cluster.update_current_allocation_configs.assert_not_called()


def test_pod_configure_http_200_persists(app_module):
    payload = _config_event("dock-b")

    result = app_module.podConfigureRedis("test-workload", payload["event"], payload)

    assert result == app_module.CONFIGURE_OK
    app_module.curr_cluster.update_current_allocation_configs.assert_called_once()
    saved = app_module.curr_cluster.update_current_allocation_configs.call_args.args[0]
    assert saved["encoded_matching_name"] == "dock-b"


def test_pod_configure_remove_config_delete_shape(app_module):
    app_module.curr_cluster.get_current_allocation_configs.return_value = {"dock-c": {}}
    payload = _config_event("dock-c", remove_config=True)

    result = app_module.podConfigureRedis("test-workload", payload["event"], payload)

    assert result == app_module.CONFIGURE_OK
    app_module.curr_cluster.delete_allocation_config.assert_called_once_with(
        {"encoded_matching_name": "dock-c"}
    )


def test_pod_configure_remove_config_missing_allocation_noops(app_module):
    payload = _config_event("dock-missing", remove_config=True)

    result = app_module.podConfigureRedis("test-workload", payload["event"], payload)

    assert result == app_module.CONFIGURE_NOOP
    app_module.curr_cluster.delete_allocation_config.assert_not_called()
    app_module.pc.applyConfig.assert_not_called()
    app_module.curr_cluster.update_current_allocation_configs.assert_not_called()


def test_pod_configure_defer_on_failure(app_module):
    app_module.app.config["WDM_CONFIG_DEFER_ON_FAILURE"] = True
    app_module.pc.applyConfig.return_value = None
    payload = _config_event()

    result = app_module.podConfigureRedis("test-workload", payload["event"], payload)

    assert result == app_module.CONFIGURE_DEFERRED


@pytest.mark.parametrize("app_module", [True], indirect=True)
def test_kafka_terminal_config_failure_commits(app_module, monkeypatch):
    wl_d = {
        app_module.change_field: app_module.change_id_pod_configure,
        app_module.app.config["WDM_WL_ID_FIELD"]: "cam-1",
        app_module.app.config["WDM_POD_ALLOCATION_ENCODED_NAME_KEY"]: "dock-a",
    }
    original_json = {app_module.app.config["WDM_EVENT_OBJECT_FIELD"]: wl_d}
    app_module._test_mock_kfk.getMessageValue.return_value = (wl_d, original_json)
    monkeypatch.setattr(app_module, "podConfigureRedis", MagicMock(return_value=app_module.CONFIGURE_FAILED))

    handler = app_module._test_fake_bus.handlers[app_module.topic]
    handler(MagicMock())

    app_module._test_fake_bus.consumer.commit.assert_called_once()


@pytest.mark.parametrize("app_module", [True], indirect=True)
def test_kafka_unclassified_exception_commits_as_terminal(app_module, monkeypatch):
    """Poison/unknown failures must commit so Kafka partitions are not blocked."""
    from lib.bus_outcomes import reset_retry_attempts_for_tests

    reset_retry_attempts_for_tests()
    wl_d = {
        app_module.change_field: app_module.change_id_add,
        app_module.app.config["WDM_WL_ID_FIELD"]: "cam-1",
    }
    original_json = {app_module.app.config["WDM_EVENT_OBJECT_FIELD"]: wl_d}
    app_module._test_mock_kfk.getMessageValue.return_value = (wl_d, original_json)
    monkeypatch.setattr(app_module, "provisionStreamRedis", MagicMock(side_effect=RuntimeError("boom")))

    handler = app_module._test_fake_bus.handlers[app_module.topic]
    handler(MagicMock())

    app_module._test_fake_bus.consumer.commit.assert_called_once()


@pytest.mark.parametrize("app_module", [True], indirect=True)
def test_kafka_malformed_keyerror_commits(app_module, monkeypatch):
    from lib.bus_outcomes import reset_retry_attempts_for_tests

    reset_retry_attempts_for_tests()
    wl_d = {
        app_module.change_field: app_module.change_id_add,
        # missing camera_id on purpose
    }
    original_json = {app_module.app.config["WDM_EVENT_OBJECT_FIELD"]: {}}
    app_module._test_mock_kfk.getMessageValue.return_value = (wl_d, original_json)

    handler = app_module._test_fake_bus.handlers[app_module.topic]
    handler(MagicMock())

    app_module._test_fake_bus.consumer.commit.assert_called_once()


@pytest.mark.parametrize("app_module", [True], indirect=True)
def test_kafka_max_replica_does_not_commit_until_retry_limit(app_module, monkeypatch):
    from lib.bus_outcomes import reset_retry_attempts_for_tests

    reset_retry_attempts_for_tests()
    app_module.app.config["WDM_EVENT_RETRY_LIMIT"] = 3
    app_module.evic_q_on_no_capacity = False
    wl_d = {
        app_module.change_field: app_module.change_id_add,
        app_module.app.config["WDM_WL_ID_FIELD"]: "cam-1",
    }
    original_json = {app_module.app.config["WDM_EVENT_OBJECT_FIELD"]: wl_d}
    app_module._test_mock_kfk.getMessageValue.return_value = (wl_d, original_json)
    monkeypatch.setattr(
        app_module,
        "provisionStreamRedis",
        MagicMock(side_effect=app_module.MaxReplicaException(1)),
    )

    handler = app_module._test_fake_bus.handlers[app_module.topic]
    msg = MagicMock()
    msg.topic.return_value = "t"
    msg.partition.return_value = 0
    msg.offset.return_value = 42
    monkeypatch.setattr(app_module.time, "sleep", MagicMock())

    handler(msg)
    app_module._test_fake_bus.consumer.commit.assert_not_called()
    assert app_module._test_fake_bus.consumer.seek.call_count == 1

    handler(msg)
    app_module._test_fake_bus.consumer.commit.assert_not_called()
    assert app_module._test_fake_bus.consumer.seek.call_count == 2

    handler(msg)
    app_module._test_fake_bus.consumer.commit.assert_called_once()
    assert app_module._test_fake_bus.consumer.seek.call_count == 2


@pytest.mark.parametrize("app_module", [True], indirect=True)
def test_kafka_seek_failure_parks_offset_on_next_commit(app_module, monkeypatch):
    """If seek fails, flask-kafka's later commit must still park at this offset."""
    from lib.bus_outcomes import reset_retry_attempts_for_tests

    reset_retry_attempts_for_tests()
    app_module.app.config["WDM_EVENT_RETRY_LIMIT"] = 5
    app_module.evic_q_on_no_capacity = False
    wl_d = {
        app_module.change_field: app_module.change_id_add,
        app_module.app.config["WDM_WL_ID_FIELD"]: "cam-1",
    }
    original_json = {app_module.app.config["WDM_EVENT_OBJECT_FIELD"]: wl_d}
    app_module._test_mock_kfk.getMessageValue.return_value = (wl_d, original_json)
    monkeypatch.setattr(
        app_module,
        "provisionStreamRedis",
        MagicMock(side_effect=app_module.MaxReplicaException(1)),
    )
    monkeypatch.setattr(app_module.time, "sleep", MagicMock())

    consumer = app_module._test_fake_bus.consumer
    real_commit = MagicMock()
    consumer.commit = real_commit
    consumer.seek.side_effect = RuntimeError("seek broken")

    handler = app_module._test_fake_bus.handlers[app_module.topic]
    msg = MagicMock()
    msg.topic.return_value = "t"
    msg.partition.return_value = 0
    msg.offset.return_value = 42

    handler(msg)
    # Handler itself must not advance; flask-kafka would call commit next.
    real_commit.assert_not_called()
    assert consumer.commit is not real_commit

    # Simulate flask-kafka post-handler commit.
    consumer.commit()
    real_commit.assert_called_once()
    parked = real_commit.call_args[0][0]
    assert len(parked) == 1
    tp, meta = next(iter(parked.items()))
    assert tp.topic == "t"
    assert tp.partition == 0
    assert meta.offset == 42
    # One-shot wrapper must restore the original commit.
    assert consumer.commit is real_commit


def test_kafka_park_offset_on_next_commit_unit():
    from lib.bus_outcomes import kafka_park_offset_on_next_commit

    consumer = MagicMock()
    original = MagicMock()
    consumer.commit = original
    msg = MagicMock()
    msg.topic = "topic-a"
    msg.partition = 1
    msg.offset = 7

    assert kafka_park_offset_on_next_commit(consumer, msg) is True
    consumer.commit()
    original.assert_called_once()
    parked = original.call_args[0][0]
    tp, meta = next(iter(parked.items()))
    assert (tp.topic, tp.partition, meta.offset) == ("topic-a", 1, 7)
    assert consumer.commit is original


def test_classify_exception_and_decide_commit(monkeypatch):
    import sys
    import types

    from lib.bus_outcomes import (
        EVENT_RETRYABLE,
        EVENT_TERMINAL,
        classify_exception,
        decide_commit,
        reset_retry_attempts_for_tests,
    )
    import requests

    # Stub redis / kubernetes if not installed in the test environment.
    if "redis" not in sys.modules:
        fake_redis = types.ModuleType("redis")

        class RedisError(Exception):
            pass

        class RedisConnectionError(RedisError):
            pass

        fake_redis.RedisError = RedisError
        fake_redis.ConnectionError = RedisConnectionError
        monkeypatch.setitem(sys.modules, "redis", fake_redis)

    if "kubernetes.client.rest" not in sys.modules:
        fake_k8s = types.ModuleType("kubernetes")
        fake_client = types.ModuleType("kubernetes.client")
        fake_rest = types.ModuleType("kubernetes.client.rest")

        class ApiException(Exception):
            def __init__(self, status=0, reason=""):
                super().__init__(reason)
                self.status = status
                self.reason = reason

        fake_rest.ApiException = ApiException
        monkeypatch.setitem(sys.modules, "kubernetes", fake_k8s)
        monkeypatch.setitem(sys.modules, "kubernetes.client", fake_client)
        monkeypatch.setitem(sys.modules, "kubernetes.client.rest", fake_rest)

    import redis
    from kubernetes.client.rest import ApiException

    reset_retry_attempts_for_tests()
    assert classify_exception(KeyError("camera_id")) == EVENT_TERMINAL
    assert classify_exception(RuntimeError("boom")) == EVENT_TERMINAL
    assert classify_exception(requests.ConnectionError("down")) == EVENT_RETRYABLE
    assert classify_exception(redis.ConnectionError("redis down")) == EVENT_RETRYABLE
    assert classify_exception(TimeoutError("slow")) == EVENT_RETRYABLE
    assert classify_exception(ApiException(status=503, reason="Service Unavailable")) == EVENT_RETRYABLE

    should_commit, final, attempt = decide_commit(EVENT_RETRYABLE, "k1", 2)
    assert should_commit is False
    assert final == EVENT_RETRYABLE
    assert attempt == 1

    should_commit, final, attempt = decide_commit(EVENT_RETRYABLE, "k1", 2)
    assert should_commit is True
    assert final == EVENT_TERMINAL
    assert attempt == 2


def test_remove_all_streams_missing_trace_context_continues(app_module, monkeypatch):
    app_module.cfg.getpods.return_value = ["pod-0", "pod-1"]
    app_module.cfg.getworkLoadSpecs.side_effect = [
        json.dumps([{"event": {"camera_id": "cam-1"}}]),
        json.dumps([{"event": {"camera_id": "cam-2"}}]),
    ]
    calls = []

    def fake_deprovision(k8swlob_name, data, original_json, parent_context, wait_add_threads_timeout=None):
        calls.append((k8swlob_name, data["camera_id"], parent_context))

    monkeypatch.setattr(app_module, "deprovisionStreamRedis", fake_deprovision)
    app_module.id_ctx_mapping = {}

    app_module.removeAllStreams()

    assert calls == [
        (app_module.app.config["WDM_WL_OBJECT_NAME"], "cam-1", None),
        (app_module.app.config["WDM_WL_OBJECT_NAME"], "cam-2", None),
    ]


def test_apply_config_retries_transport_errors(monkeypatch):
    from lib.podprovisioner.provisionconfig import provisionconfig

    cfg = {
        "WDM_CONFIG_PORT": "9002",
        "WDM_CONFIG_URL": "/config",
        "WDM_CONFIG_RETRY_ATTEMPTS": 3,
        "WDM_CONFIG_RETRY_DELAY": 0.25,
        "WDM_ADD_REMOVE_REQUEST_TIMEOUT": 2,
    }
    calls = []
    sleeps = []

    def fake_post(**kwargs):
        calls.append(kwargs)
        raise requests.ConnectionError("refused")

    monkeypatch.setattr("lib.podprovisioner.provisionconfig.requests.post", fake_post)
    monkeypatch.setattr("lib.podprovisioner.provisionconfig.time.sleep", lambda delay: sleeps.append(delay))

    pc = provisionconfig(cfg, MagicMock(), MagicMock())
    assert pc.applyConfig({"podIp": "127.0.0.1"}, {"event": {}}) is None
    assert len(calls) == 3
    assert sleeps == [0.25, 0.25]


def test_apply_config_returns_http_response_without_retry(monkeypatch):
    from lib.podprovisioner.provisionconfig import provisionconfig

    cfg = {
        "WDM_CONFIG_PORT": "9002",
        "WDM_CONFIG_URL": "/config",
        "WDM_CONFIG_RETRY_ATTEMPTS": 3,
        "WDM_CONFIG_RETRY_DELAY": 0.25,
        "WDM_ADD_REMOVE_REQUEST_TIMEOUT": 2,
    }
    calls = []

    def fake_post(**kwargs):
        calls.append(kwargs)
        return _Resp(404)

    monkeypatch.setattr("lib.podprovisioner.provisionconfig.requests.post", fake_post)

    pc = provisionconfig(cfg, MagicMock(), MagicMock())
    response = pc.applyConfig({"podIp": "127.0.0.1"}, {"event": {}})
    assert response.status_code == 404
    assert len(calls) == 1
