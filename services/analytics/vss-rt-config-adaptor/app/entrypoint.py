#!/usr/bin/env python3

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

"""VSS RT Config Adaptor entrypoint for distroless (no shell): starts the Flask app on PORT."""
import logging
import os
import sys

_level = os.environ.get("LOG_LEVEL", "INFO").upper()
_numeric = getattr(logging, _level, logging.INFO)
logging.basicConfig(
    level=_numeric,
    format="%(asctime)s %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)

if __name__ == "__main__":
    import app as app_module

    flask_app = app_module.app
    app_port = flask_app.config["PORT"]
    flask_app.logger.info("application start on port %s", app_port)
    flask_app.run(host="0.0.0.0", port=int(app_port), use_reloader=False)
