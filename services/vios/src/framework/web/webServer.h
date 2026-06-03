/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "HttpServerRequestHandler.h"
#include "WebsocketServerRequestHandler.h"
#include "websocket_apis.h"

class WebServer
{
    public:
        WebServer();
        ~WebServer();

        void registerRESTAPIs(std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>>& func);
        void registerWSAPIs(std::map<std::string, WebsocketServerRequestHandler::httpFunction, std::less<>>& func);

    private:
        std::shared_ptr<CivetServer> m_civetServer = nullptr;
        std::shared_ptr<WebsocketServerRequestHandler> m_websocket = nullptr;
        unique_ptr<HttpServerRequestHandler> m_httpServerHandler = nullptr;
};