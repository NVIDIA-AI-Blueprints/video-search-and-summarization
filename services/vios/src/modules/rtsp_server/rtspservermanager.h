/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "rtspserver.h"
#include <jsoncpp/json/json.h>
#include "vstmodule.h"
#include "RtspLoadBalancer.h"

inline constexpr const char* PROXY_SESSION_API = "/api/v1/proxy/session/*";
inline constexpr const char* PROXY_STREAM_API = "/api/v1/proxy/stream/*";
namespace nv_vms
{
    class RtspServerManager : public IVstModule
    {
        public:
            RtspServerManager();
            ~RtspServerManager();
            void postInit() override;
            VmsErrorCode handleSessionAPIrequest(const Json::Value &, const Json::Value &in, Json::Value &out, struct mg_connection *conn);
            VmsErrorCode handleStreamAPIrequest(const Json::Value &, const Json::Value &in, Json::Value &out, struct mg_connection *conn);
            VmsErrorCode removeStream(const std::string &streamId, Json::Value &response);

            /** Return the RTSP base URL (domain prefix or urlPrefix from the first server). */
            std::string getRtspBaseUrl()
            {
                RtspServer* server = m_lb.rtspServer();
                if (!server)
                    return std::string();
                std::string url = server->getRtspServerDomainPrefix();
                if (url.empty())
                    url = server->urlPrefix();
                return url;
            }

        private:
            void handleRESTAPIs();
            void restoreRtspStreamsFromDB();
            VmsErrorCode handleProxyConfiguration(const Json::Value &req_info, const Json::Value &in, Json::Value &response);
            VmsErrorCode handleProxyQos(Json::Value &response);
            VmsErrorCode handleStreamDelete(const std::string &streamId, Json::Value &response);

        private:
            RtspLoadBalancer m_lb;
    };

    inline RtspServerManager* GET_RTSPSERVER()
    {
        return static_cast<RtspServerManager*>(ModuleLoader::getInstance()->getRtspServerMgmtInstance());
    }

    IVstModule* createRtspServerManagerObject();
    void deleteRtspServerManagerObject( IVstModule* object );

} //nv_vms
