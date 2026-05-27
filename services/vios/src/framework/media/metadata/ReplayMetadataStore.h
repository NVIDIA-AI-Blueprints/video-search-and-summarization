/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "MetadataStore.h"

class ReplayMetadataStore : public IMetadataStore
{
public:
    virtual ~ReplayMetadataStore() = default;

    virtual void addMetadata(const Json::Value& metadata) override {};
    virtual Json::Value getMetadata(const int64_t frameTS) override = 0;
    virtual void reFillMetadata(std::queue<Json::Value>& qToFill, std::mutex& qToFillMutex) override {};

    virtual void checkAndRefillMetadata(const int64_t searchAfterTS) =0;
    virtual void waitForMetadata() =0;
    virtual void fetchMetadata() =0;
    virtual void fetchMetadataAgain(const std::string& newStartTime) =0;
    virtual bool isSearching() =0;
};