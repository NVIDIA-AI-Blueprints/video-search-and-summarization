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

#include <atomic>

#include "ReplayMetadataStore.h"
#include "libasync++/async++.h"
#include "elasticSearch.h"
#include "syncobject.h"

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
    virtual void startPrefetch() override;

private:
    // Runs on m_prefetchTask: time-slices [start,end], fetches slices in
    // parallel, merges/sorts, and loads the queue in one shot.
    void prefetchRange();

    // Pops entries older than frameTS and returns the ceiling match (first
    // entry with timestamp >= frameTS), or nullValue if the queue drained.
    Json::Value matchQueueFront(const int64_t frameTS);

    async::task<void>           m_elasticTask;
    async::task<void>           m_prefetchTask;
    BBoxMetaData                m_bboxMetadata;
    bool                        m_useId {false};

    // Set only on the download path (via startPrefetch). Gates the bounded
    // blocking wait in getMetadata so recorded-playback/webrtc/live behavior is
    // byte-for-byte unchanged. Written on the build thread, read on the gst
    // streaming thread, hence atomic.
    std::atomic<bool>           m_blockingGet {false};
    // True while the eager prefetch async task is running.
    std::atomic<bool>           m_prefetchInFlight {false};
    // True once the prefetch has loaded the entire requested range (no tail
    // remains, so incremental refill is unnecessary).
    std::atomic<bool>           m_fullyPrefetched {false};
    // Signaled whenever a fetch pushes new records or completes, so a blocked
    // getMetadata wakes immediately instead of polling. The bounded wait budget
    // is only a safety backstop against a slow/unavailable Elasticsearch.
    SyncObject                  m_dataReady = {};
};