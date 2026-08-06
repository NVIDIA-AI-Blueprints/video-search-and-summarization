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
Pytest configuration and fixtures for SDRC app tests.
Mocks external dependencies so app and lib modules can be tested without
Kafka, Redis, Kubernetes, or Envoy.
"""
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

# Ensure project root is on path when running tests from repo root or tests/
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _make_mock_cfg():
    """Config/cache mock used by app (configserver or redisconfig)."""
    m = MagicMock()
    m.getworkLoadSpecById.return_value = None
    m.getpods.return_value = []
    m.getworkLoadSpec.return_value = None
    m.getSpecCount.return_value = 0
    m.eraseSpecContent.return_value = None
    m.erasePodSpecContent.return_value = None
    m.getworkLoadSpecs.return_value = "[]"
    m.getAllStreams.return_value = {}
    m.getpodsCount.return_value = 0
    m.addWorkLoadSpec.return_value = None
    m.deleteFromWorkLoadSpec.return_value = None
    m.updateWorkLoadSpec.return_value = None
    m.getCacheInfoForStreamId.return_value = ("pod1", {"external_metadata": {}})
    m.deleteWLObj.return_value = None
    m._loadWorkLoadSpec.return_value = None
    return m


def _make_mock_cluster():
    """K8s/Docker cluster mock."""
    m = MagicMock()
    sts = MagicMock()
    sts.status.replicas = 2
    m.getStatefulSets.return_value = sts
    m.getReadyReplicas.return_value = 2
    m.getWorkloadObjects.return_value = None
    m.getPodIps.return_value = []
    m.get_current_allocation_configs.return_value = {}
    m.get_current_allocation_pod_names.return_value = []
    m.find_unallocated_pod.return_value = {"podName": "pod-0", "podIp": "127.0.0.1", "podPort": 5000}
    m.update_current_allocation_configs.return_value = None
    m.ifPodDown.return_value = False
    m.updateRouteMapping.return_value = None
    m.scaleStatefulsetPods.return_value = None
    m.disaggregate_podInfo.return_value = {"podName": "pod-0", "podIp": "127.0.0.1"}
    m.get_pod_info_by_encoded_name.return_value = {"podName": "pod-0"}
    m.watchPodState.return_value = iter([])
    m.get_podname_keys.return_value = []
    return m


def _make_mock_redis_messaging():
    """Redis messaging mock."""
    m = MagicMock()
    m.getIdPodMapping.return_value = None
    m.getIdPodPodDnsMapping.return_value = None
    m.getCurrentMapping.return_value = {}
    m.clearAllData.return_value = None
    m.clearPodData.return_value = None
    m.getMessageValue.return_value = None
    m.getRedisConnection.return_value = MagicMock()
    m.publishMessage.return_value = None
    m.message_err.return_value = None
    m.message_down.return_value = None
    m.message_up.return_value = None
    return m


def _make_mock_envy():
    """Envoy xDS mock."""
    m = MagicMock()
    m.clusterXDs.return_value = []
    m.routeXDs.return_value = []
    return m


def _make_mock_provisionconfig():
    """Provision config mock (add/delete/applyConfig)."""
    m = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {}
    m.add.return_value = resp
    m.delete.return_value = resp
    m.applyConfig.return_value = resp
    return m


def _make_mock_tracing():
    """OpenTelemetry mock used by app route tests."""
    m = types.ModuleType("lib.tracing")
    span = MagicMock()
    context = MagicMock()
    m.StatusCode = types.SimpleNamespace(ERROR="ERROR")
    m.create_parent_span = MagicMock(return_value=(span, context))
    m.create_child_span = MagicMock(return_value=(span, context))
    m.inject_context = MagicMock(return_value={})
    m.propagate_context = MagicMock()
    m.delete_context_entry = MagicMock()
    return m


@pytest.fixture(scope="function")
def app_test_client():
    """
    Create Flask test client with all external deps mocked so app can be imported.
    Use this fixture in tests that hit HTTP routes; do not import app at module level.
    """
    mock_cfg = _make_mock_cfg()
    mock_cluster = _make_mock_cluster()
    mock_redis = _make_mock_redis_messaging()
    mock_envy = _make_mock_envy()
    mock_pc = _make_mock_provisionconfig()
    mock_tracing = _make_mock_tracing()

    env = {
        "WDM_KFK_ENABLE": "false",
        "WDM_CACHE_METHOD": "file",
        "WDM_RESET_PRELOAD_FILE": "false",
        "WDM_DISABLE_WERKZEUG_LOGGING": "true",
    }

    def _make_module(name, **attrs):
        module = types.ModuleType(name)
        for attr_name, attr_value in attrs.items():
            setattr(module, attr_name, attr_value)
        return module

    parameters_pkg = _make_module(
        "lib.parameters", configserver=MagicMock(return_value=mock_cfg)
    )
    parameters_pkg.__path__ = [os.path.join(_REPO_ROOT, "lib", "parameters")]
    redisconfig_mod = _make_module(
        "lib.parameters.redisconfig",
        clear_stale_redis_workload_spec_lock_keys=MagicMock(),
        redisconfig=MagicMock(return_value=mock_cfg),
    )

    cluster_mod = _make_module(
        "lib.podprovisioner.kubernetes.cluster",
        cluster=MagicMock(return_value=mock_cluster),
    )
    provisionconfig_mod = _make_module(
        "lib.podprovisioner.provisionconfig",
        provisionconfig=MagicMock(return_value=mock_pc),
    )

    kafka_factory = MagicMock(return_value=MagicMock())
    messaging_pkg = _make_module(
        "lib.messaging",
        kafka=kafka_factory,
        redisMessaging=MagicMock(return_value=mock_redis),
    )
    messaging_pkg.__path__ = [os.path.join(_REPO_ROOT, "lib", "messaging")]
    kafka_mod = _make_module("lib.messaging.kafka", kafka=kafka_factory)
    redis_messaging_mod = _make_module(
        "lib.messaging.redisMessaging",
        Consumer=MagicMock(),
        redisMessaging=MagicMock(return_value=mock_redis),
    )

    xds_pkg = _make_module("lib.xDS", envoyxDS=MagicMock(return_value=mock_envy))
    xds_pkg.__path__ = [os.path.join(_REPO_ROOT, "lib", "xDS")]
    envoyxds_mod = _make_module(
        "lib.xDS.envoyxDS", envoyxDS=MagicMock(return_value=mock_envy)
    )
    grpc_xds_mod = _make_module(
        "lib.xDS.grpc_xds_server",
        can_start_grpc_xds_server=MagicMock(return_value=False),
        is_grpc_xds_enabled=MagicMock(return_value=False),
        notify_xds_update=MagicMock(),
        start_grpc_xds_server=MagicMock(),
    )

    module_overrides = {
        "lib.parameters": parameters_pkg,
        "lib.parameters.redisconfig": redisconfig_mod,
        "lib.podprovisioner.kubernetes.cluster": cluster_mod,
        "lib.podprovisioner.provisionconfig": provisionconfig_mod,
        "lib.messaging": messaging_pkg,
        "lib.messaging.kafka": kafka_mod,
        "lib.messaging.redisMessaging": redis_messaging_mod,
        "lib.tracing": mock_tracing,
        "lib.xDS": xds_pkg,
        "lib.xDS.envoyxDS": envoyxds_mod,
        "lib.xDS.grpc_xds_server": grpc_xds_mod,
    }

    previous_app_module = sys.modules.pop("app", None)
    lib_pkg = sys.modules.get("lib")
    missing = object()
    previous_lib_tracing = (
        getattr(lib_pkg, "tracing", missing) if lib_pkg is not None else missing
    )
    if lib_pkg is not None and previous_lib_tracing is not missing:
        delattr(lib_pkg, "tracing")
    patches = [
        patch.dict(os.environ, env),
        patch.dict(sys.modules, module_overrides),
    ]

    for p in patches:
        p.start()

    try:
        import app as app_module
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            yield client, app_module
    finally:
        sys.modules.pop("app", None)
        if previous_app_module is not None:
            sys.modules["app"] = previous_app_module
        for p in reversed(patches):
            p.stop()
        if lib_pkg is not None:
            if previous_lib_tracing is missing:
                try:
                    delattr(lib_pkg, "tracing")
                except AttributeError:
                    pass
            else:
                setattr(lib_pkg, "tracing", previous_lib_tracing)


@pytest.fixture
def client(app_test_client):
    """Flask test client (convenience alias)."""
    c, _ = app_test_client
    return c


@pytest.fixture
def app_module(app_test_client):
    """The imported app module (for accessing app.curr_cluster, app.cfg, etc. in tests)."""
    _, mod = app_test_client
    return mod


@pytest.fixture
def mock_app_config():
    """Minimal app config dict for testing lib modules that require app_config."""
    return {
        "WDM_EVENT_OBJECT_FIELD": "event",
        "WDM_WL_ID_FIELD": "camera_id",
        "WDM_WL_OBJECT_NAME": "testapp",
        "WDM_WL_SPEC": os.path.join(_REPO_ROOT, "tests", "data.yaml"),
        "WDM_WL_CHANGE_FIELD": "change",
        "WDM_WL_CHANGE_ID_ADD": "camera_streaming",
        "WDM_WL_CHANGE_ID_DEL": "camera_remove",
        "WDM_WL_ADD_URL": "/api/v1/stream/add",
        "WDM_WL_DELETE_URL": "/api/v1/stream/remove",
        "WDM_ADD_REMOVE_RETRY_ATTEMPTS": 1,
        "WDM_ADD_REMOVE_REQUEST_TIMEOUT": 5,
        "WDM_ADD_CALL_DELAY": 0,
        "WDM_MAP_ADD_FIELD": None,
        "WDM_REMAP_EVENT_OBJECT": None,
        "WDM_AGENT_EVENT_BUS": "agent-events",
        "WDM_WL_THRESHOLD": 8,
        "WDM_CHECK_STATUS": False,
        "WDM_WL_REDIS_SERVER": "localhost",
        "WDM_WL_REDIS_PORT": 6379,
        "ENVOY_ROUTE_URL_PREFIX": "/",
        "ENVOY_ROUTE_URL_PREFIX_REWRITE": "/hello",
        "ENVOY_REQUEST_TIMEOUT": 30,
    }
