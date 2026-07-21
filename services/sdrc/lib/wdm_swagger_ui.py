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

"""Shared Swagger UI registration and OpenAPI server URL for WDM Flask apps."""
import json
import os

from flask import Blueprint, render_template, request, send_from_directory


def openapi_public_server_root():
    """Try-it-out base URL: WDM_PUBLIC_BASE_URL, or X-Forwarded-* / SCRIPT_NAME, else request.url_root."""
    explicit = (os.environ.get("WDM_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    xf_h = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
    xf_p = (request.headers.get("X-Forwarded-Proto") or request.scheme or "http").split(",")[0].strip()
    if xf_h:
        base = f"{xf_p}://{xf_h}".rstrip("/")
        xf_pre = (request.headers.get("X-Forwarded-Prefix") or "").split(",")[0].strip().rstrip("/")
        if not xf_pre:
            xf_pre = (request.environ.get("SCRIPT_NAME") or "").strip().rstrip("/")
        if xf_pre:
            base = base + xf_pre
        return base.rstrip("/")
    return (request.url_root or "").rstrip("/")


def register_wdm_swagger_ui(
    flask_app,
    swagger_url_prefix: str,
    openapi_relative_url: str,
    app_name: str,
    *,
    blueprint_name: str = "swagger_ui_wdm",
) -> None:
    """Swagger UI with relative assets (works behind path prefixes)."""
    import flask_swagger_ui as _fsw

    _dist = os.path.join(os.path.dirname(_fsw.__file__), "dist")
    bp = Blueprint(blueprint_name, __name__, static_folder=_dist)
    swagger_config = {
        "app_name": app_name,
        "dom_id": "#swagger-ui",
        "url": openapi_relative_url,
        "layout": "StandaloneLayout",
        "deepLinking": True,
    }

    @bp.route("/", methods=["GET"], strict_slashes=False)
    @bp.route("/<path:path>", methods=["GET"])
    def _swagger_ui_dispatch(path=None):
        if path and path != "index.html":
            return send_from_directory(_dist, path)
        cfg = dict(swagger_config)
        if not cfg.get("oauth2RedirectUrl"):
            cfg["oauth2RedirectUrl"] = "oauth2-redirect.html"
        return render_template(
            "swagger_ui_index.html",
            app_name=cfg.pop("app_name"),
            config_json=json.dumps(cfg),
            oauth_config_json=None,
        )

    flask_app.register_blueprint(bp, url_prefix=swagger_url_prefix)
