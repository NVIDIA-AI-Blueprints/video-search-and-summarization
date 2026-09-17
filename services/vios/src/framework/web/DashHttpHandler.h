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

#include "CivetServer.h"

class DashHttpHandler final : public CivetHandler
{
public:
    bool handleGet(CivetServer* server, struct mg_connection* connection) override;
    /* A player synchronising its clock against this deployment asks the
     * manifest for the time with HEAD, and civetweb routes that here rather
     * than to handleGet.  Without it the request is refused and the player
     * falls back to a public time service on the internet. */
    bool handleHead(CivetServer* server, struct mg_connection* connection) override;
};
