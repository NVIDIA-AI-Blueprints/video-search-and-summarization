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

#include "ReplayMetadataStore.h"
#include "libasync++/async++.h"
#include "elasticSearch.h"

class ElasticMetadataStore : public ReplayMetadataStore
{
public:
    virtual ~ElasticMetadataStore();
    ElasticMetadataStore(MetadataParams& metadataParams, bool use_frameid);

    virtual Json::Value getMetadata(const int64_t frameTS) override;
    virtual void checkAndRefillMetadata(const int64_t searchAfterTS) override;
    virtual void waitForMetadata() override;
    virtual void fetchMetadata() override;
    virtual bool isSearching() override;
    virtual void fetchMetadataAgain(const std::string& newStartTime) override;

private:
    async::task<void>           m_elasticTask;
    BBoxMetaData                m_bboxMetadata;
    bool                        m_useId {false};
};