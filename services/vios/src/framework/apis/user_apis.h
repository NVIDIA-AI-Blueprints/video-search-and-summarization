/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "sensor_management.h"
#include <jsoncpp/json/json.h>
#include "HttpServerRequestHandler.h"
#include "database.h"

using namespace nv_vms;

class UserRESTApis
{
public:
	UserRESTApis();
    const std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> getHttpApi() { return m_func; };

#ifndef UNIT_TEST
private:
#endif
VmsErrorCode loginUser(const Json::Value& req_info, const Json::Value &in, Json::Value &response, struct mg_connection *conn);
VmsErrorCode addNewUser(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
VmsErrorCode deleteUser(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
VmsErrorCode logoutUser(const Json::Value& req_info, const Json::Value &in, Json::Value &response);
VmsErrorCode addNewUser(string username, string vstUser, string password, bool createUser, Json::Value &response);
VmsErrorCode handleUserCredentials(const Json::Value &data, Json::Value &response);

private:
    std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>>  m_func;
};
