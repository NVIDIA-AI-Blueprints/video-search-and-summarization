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

#include "health_probes.h"

#include <array>

#include "../inc/civetweb/civetweb.h"
#include "database.h"

namespace vst_health_probes {

bool isCivetWebServerRunning(const struct mg_connection *conn)
{
    if (conn == nullptr)
    {
        return false;
    }

    const struct mg_context *ctx = mg_get_context(conn);
    if (ctx == nullptr)
    {
        return false;
    }

    std::array<struct mg_server_port, 1> ports {};
    const int portCount = mg_get_server_ports(ctx, static_cast<int>(ports.size()), ports.data());
    return portCount > 0;
}

bool isDatabaseHealthy()
{
    IDatabaseInterface* db = GET_DB_INSTANCE();
    if (db == nullptr)
    {
        return false;
    }
    return db->isConnected();
}

nv_vms::VmsErrorCode checkCivetWebServerRunning(const struct mg_connection *conn, Json::Value &out)
{
    if (isCivetWebServerRunning(conn))
    {
        return nv_vms::VmsErrorCode::NoError;
    }

    (void)out;
    return nv_vms::VmsErrorCode::VMSInternalError;
}

nv_vms::VmsErrorCode checkReadinessProbe(const struct mg_connection *conn, Json::Value &out)
{
    if (!isCivetWebServerRunning(conn))
    {
        (void)out;
        return nv_vms::VmsErrorCode::VMSInternalError;
    }

    if (!isDatabaseHealthy())
    {
        (void)out;
        return nv_vms::VmsErrorCode::VMSInternalError;
    }

    return nv_vms::VmsErrorCode::NoError;
}

} // namespace vst_health_probes
