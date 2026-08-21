/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

    virtual void addMetadata(const Json::Value& metadata) override
    {
        // No-op: replay stores pull metadata from the backend themselves
        // (fetchMetadata/checkAndRefillMetadata); nothing is ever pushed in.
        (void)metadata;
    };
    virtual Json::Value getMetadata(const int64_t frameTS) override = 0;
    virtual void reFillMetadata(std::queue<Json::Value>& qToFill, std::mutex& qToFillMutex) override
    {
        // No-op: the replay queue is refilled from the backend by
        // checkAndRefillMetadata(), not from a caller-supplied queue.
        (void)qToFill;
        (void)qToFillMutex;
    };

    virtual void checkAndRefillMetadata(const int64_t searchAfterTS) =0;
    virtual void waitForMetadata() =0;
    virtual void fetchMetadata() =0;
    virtual void fetchMetadataAgain(const std::string& newStartTime) =0;
    virtual bool isSearching() =0;

    // Optional eager prefetch used by the download path to fully populate the
    // metadata queue before/while the pipeline starts, so faster-than-real-time
    // transcoding never outruns the async fetch (bbox flicker). Default no-op
    // preserves existing behavior for stores that do not implement it.
    virtual void startPrefetch()
    {
        // Intentionally blank: prefetch is an opt-in optimization, so stores
        // that do not implement it keep their existing lazy-fetch behavior.
    }
};