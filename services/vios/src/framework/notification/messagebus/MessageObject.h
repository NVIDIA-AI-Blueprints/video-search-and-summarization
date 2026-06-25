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

#include "syncobject.h"
#include <jsoncpp/json/json.h>

using namespace std;

struct MessageObject
{
    SyncObject m_sync;
    Json::Value m_response;
    string m_responseId;
    std::chrono::system_clock::time_point m_timestamp;
    bool m_isResponseReceived; 

    MessageObject() : m_sync(), 
        m_response(Json::nullValue), 
        m_responseId(""), 
        m_timestamp(std::chrono::system_clock::now()),
        m_isResponseReceived(false) {}
};
