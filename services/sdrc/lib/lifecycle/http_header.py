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

import copy
from urllib.parse import urlsplit

MODE_MESSAGE_BUS = "message-bus"
MODE_HTTP_HEADER = "http-header"

ACTION_ADD = "add"
ACTION_DELETE = "delete"
ACTION_REPROVISION = "reprovision"
BODY_AWARE_ACTIONS = frozenset((ACTION_ADD, ACTION_REPROVISION))

_MODE_ALIASES = {
    MODE_MESSAGE_BUS: MODE_MESSAGE_BUS,
    "message_bus": MODE_MESSAGE_BUS,
    "messagebus": MODE_MESSAGE_BUS,
    "event": MODE_MESSAGE_BUS,
    "event-based": MODE_MESSAGE_BUS,
    "event_based": MODE_MESSAGE_BUS,
    "http": MODE_HTTP_HEADER,
    MODE_HTTP_HEADER: MODE_HTTP_HEADER,
    "http_header": MODE_HTTP_HEADER,
}

_ACTION_CONFIG = {
    ACTION_ADD: (
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_PATH",
        "WDM_HTTP_HEADER_LIFECYCLE_ADD_METHOD",
        "WDM_WL_CHANGE_ID_ADD",
    ),
    ACTION_DELETE: (
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_PATH",
        "WDM_HTTP_HEADER_LIFECYCLE_DELETE_METHOD",
        "WDM_WL_CHANGE_ID_DEL",
    ),
    ACTION_REPROVISION: (
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_PATH",
        "WDM_HTTP_HEADER_LIFECYCLE_REPROVISION_METHOD",
        "WDM_WL_CHANGE_ID_REPROVISION",
    ),
}


def _config_get(config, key, default=None):
    if config is None:
        return default
    if hasattr(config, "get"):
        return config.get(key, default)
    return getattr(config, key, default)


def normalize_lifecycle_ingress_mode(mode):
    if mode is None or str(mode).strip() == "":
        return MODE_MESSAGE_BUS
    normalized = str(mode).strip().lower()
    try:
        return _MODE_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(
            "WDM_LIFECYCLE_INGRESS_MODE must be one of: message-bus, http"
        ) from exc


def is_http_header_lifecycle_mode(config):
    return (
        normalize_lifecycle_ingress_mode(
            _config_get(config, "WDM_LIFECYCLE_INGRESS_MODE")
        )
        == MODE_HTTP_HEADER
    )


def is_message_bus_lifecycle_mode(config):
    return (
        normalize_lifecycle_ingress_mode(
            _config_get(config, "WDM_LIFECYCLE_INGRESS_MODE")
        )
        == MODE_MESSAGE_BUS
    )


def normalize_lifecycle_path(path):
    if path is None:
        return "/"
    split = urlsplit(str(path))
    normalized = split.path or "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    while len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    if normalized == "/sdrc":
        return "/"
    if normalized.startswith("/sdrc/"):
        normalized = normalized[len("/sdrc"):]
    return normalized or "/"


def normalize_lifecycle_method(method):
    return str(method or "").strip().upper()


def build_http_lifecycle_bindings(config):
    bindings = {}
    for action, (path_key, method_key, _change_key) in _ACTION_CONFIG.items():
        path = _config_get(config, path_key)
        method = _config_get(config, method_key)
        if path is None or str(path).strip() == "":
            continue
        if method is None or str(method).strip() == "":
            continue
        key = (normalize_lifecycle_method(method), normalize_lifecycle_path(path))
        bindings.setdefault(key, []).append(action)
    return bindings


def match_http_lifecycle_action(method, path, config, has_body=None):
    bindings = build_http_lifecycle_bindings(config)
    key = (normalize_lifecycle_method(method), normalize_lifecycle_path(path))
    actions = bindings.get(key)
    if not actions:
        return None
    if len(actions) == 1:
        return actions[0]

    action_set = frozenset(actions)
    if action_set == BODY_AWARE_ACTIONS:
        if has_body is False:
            return ACTION_REPROVISION
        return ACTION_ADD

    raise ValueError(
        "Duplicate HTTP lifecycle binding %s %s is used by %s; "
        "only add/reprovision may share a binding"
        % (key[0], key[1], ", ".join(actions))
    )


def extract_header_value(headers, header_name):
    header_name = str(header_name or "").strip()
    if not header_name:
        return None

    if hasattr(headers, "get"):
        value = headers.get(header_name)
        if value is not None:
            value = str(value).strip()
            return value or None

    target = header_name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == target:
            value = str(value).strip()
            return value or None
    return None


def lifecycle_header_name(config):
    configured = _config_get(config, "WDM_HTTP_HEADER_LIFECYCLE_STREAM_ID_HEADER")
    if configured is not None and str(configured).strip() != "":
        return str(configured).strip()

    route_header = _config_get(config, "ENVOY_ROUTE_HEADER")
    if route_header is not None and str(route_header).strip() != "":
        return str(route_header).strip()

    route_header = _config_get(config, "ENVOYROUTEHEADER")
    if route_header is not None and str(route_header).strip() != "":
        return str(route_header).strip()

    return "id"


def _action_change_id(config, action):
    try:
        _path_key, _method_key, change_key = _ACTION_CONFIG[action]
    except KeyError as exc:
        raise ValueError("unknown lifecycle action: %s" % action) from exc
    return _config_get(config, change_key)


def build_http_lifecycle_event_payload(config, action, stream_id, body=None):
    event_field = _config_get(config, "WDM_EVENT_OBJECT_FIELD", "event")
    id_field = _config_get(config, "WDM_WL_ID_FIELD", "camera_id")
    change_field = _config_get(config, "WDM_WL_CHANGE_FIELD", "change")
    change_id = _action_change_id(config, action)

    payload = copy.deepcopy(body) if isinstance(body, dict) else {}
    event_payload = payload.get(event_field)
    if isinstance(event_payload, dict):
        event_payload = copy.deepcopy(event_payload)
    else:
        event_payload = {}
        if payload:
            event_payload["payload"] = payload

    event_payload[id_field] = stream_id
    event_payload[change_field] = change_id
    return {event_field: event_payload}
