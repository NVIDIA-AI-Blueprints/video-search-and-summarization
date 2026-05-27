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
#include "postgresql_helper.h"
#include "sqlite_helper.h"
#include "database.h"
#include "database_manager.h"

#define GET_DB_INSTANCE DatabaseConnectionFactory::getInstance
inline constexpr int ONE_MIN = 1000;
inline constexpr int FILE_INIT_DURATION = 1;

class DatabaseConnectionFactory
{
public:
    static IDatabaseInterface *getInstance()
    {
        if (GET_CONFIG().use_centralize_db)
        {
            return GET_POSTGRESQL_INSTANCE();
        }
        return GET_SQLITE_INSTANCE();
    }
};
