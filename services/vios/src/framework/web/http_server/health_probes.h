/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once

#include <jsoncpp/json/json.h>

#include "error_code.h"

struct mg_connection;

namespace vst_health_probes {

bool isCivetWebServerRunning(const struct mg_connection *conn);

bool isDatabaseHealthy();

nv_vms::VmsErrorCode checkCivetWebServerRunning(const struct mg_connection *conn, Json::Value &out);

nv_vms::VmsErrorCode checkReadinessProbe(const struct mg_connection *conn, Json::Value &out);

} // namespace vst_health_probes
