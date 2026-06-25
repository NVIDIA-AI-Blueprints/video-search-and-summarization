/*
 * SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include <fstream>
#include <map>
#include <memory>

#include "logger.h"
#include <jsoncpp/json/json.h>
#include "error_code.h"
#include "rtspvideocapturer.h"
#include "event_loop.h"

struct HlsData : public EventLoopData
{
    std::map<std::string, std::string, std::less<>> m_opts;
};

struct HlsOutData : public EventLoopOutData
{
    Json::Value m_response;
    nv_vms::VmsErrorCode m_error;
};

class HLSManager
{
    public:
        HLSManager();
        ~HLSManager();
        void start();
        VmsErrorCode startStream(std::map<std::string, std::string, std::less<>> opts, Json::Value &response);
        VmsErrorCode stopStream(const string& peerid, Json::Value &response);
        VmsErrorCode start(std::map<std::string, std::string, std::less<>> opts, Json::Value &response);
        VmsErrorCode stop(const string peerid, Json::Value &response);
        VmsErrorCode postToEventLoop(const string& task_name, std::map<std::string, std::string, std::less<>>& opts,
                                    Json::Value& response, bool is_async = true, uint32_t timeout = 10);
        bool checkIfPeerPresent(const string& peerid);
    private:
        std::map<std::string, RTSPVideoCapturer*>     m_streamMap;
        EventLoop m_eventLoop;
        std::mutex m_streamLock;
};

