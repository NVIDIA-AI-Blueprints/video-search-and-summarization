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

#include "ElasticMetadataStore.h"
#include "logger.h"

ElasticMetadataStore::ElasticMetadataStore(MetadataParams& params, bool use_frameid)
    : m_bboxMetadata(m_metadataQueue, m_metadataQueueMutex)
{
    SearchParams inData(params.m_startTime, params.m_endTime, params.m_sensorName);
    if (use_frameid)
    {
        inData.m_useId = true;
        inData.m_search_after = 0;
        m_useId = true;
    }
    m_bboxMetadata.m_searchParams = inData;
}

ElasticMetadataStore::~ElasticMetadataStore()
{
    if (m_elasticTask.valid())
    {
        try
        {
            m_elasticTask.get();
        }
        catch(const std::exception& e)
        {
            LOG(error) << "Caught Exception for m_elasticTask Async task: " <<  e.what() << endl;
        }
    }
}

Json::Value ElasticMetadataStore::getMetadata(const int64_t frameTS)
{
    checkAndRefillMetadata(frameTS);

    Json::Value metadata = Json::nullValue;
    {
        std::lock_guard<std::mutex> guard(m_metadataQueueMutex);
        int64_t elasticTS = 0;
        if (!m_metadataQueue.empty())
        {
            metadata = m_metadataQueue.front();
            elasticTS = metadata["epocTime"].asUInt64() * 1000;
            while(elasticTS < frameTS && !m_metadataQueue.empty())
            {
                m_metadataQueue.pop();
                if (!m_metadataQueue.empty())
                {
                    metadata = m_metadataQueue.front();
                    elasticTS = metadata["epocTime"].asUInt64() * 1000;
                }
                else
                {
                    metadata = Json::nullValue;
                    break;
                }
            }
        }
    }
    return metadata;
}

void ElasticMetadataStore::checkAndRefillMetadata(const int64_t frameTS)
{
    uint32_t currentSize = 0;
    {
        std::lock_guard<std::mutex> guard(m_bboxMetadata.m_hitsLock);
        currentSize = m_bboxMetadata.m_qHits.size();
    }
    if ( (m_bboxMetadata.m_dataSize != 0) && (m_bboxMetadata.m_searching == false)
        && (currentSize < m_bboxMetadata.m_dataSize/2) )
    {
        m_bboxMetadata.m_searching = true;
        m_elasticTask = async::spawn([this, frameTS, currentSize]
        {
            LOG(verbose) << "Starting parallel metadata fetch at current size: "
                        << currentSize << endl;
            if (m_useId == false)
            {
                // Jump search from current frame, when needed
                int64_t frame_ts_ms_signed = frameTS / 1000;
                uint64_t frameTS_millisec = 0;
                if (frame_ts_ms_signed > 0)
                {
                    frameTS_millisec = static_cast<uint64_t>(frame_ts_ms_signed - 1);
                }
                if (frameTS_millisec > m_bboxMetadata.m_searchParams.m_search_after)
                {
                    m_bboxMetadata.m_searchParams.m_search_after = frameTS_millisec;
                }
            }
            elasticSearch::getBboxPosition(m_bboxMetadata);
        });
    }
}

void ElasticMetadataStore::waitForMetadata()
{
    if (m_bboxMetadata.m_qHits.empty() && m_bboxMetadata.m_searching)
    {
        if (m_elasticTask.valid())
        {
            try
            {
                m_elasticTask.get();
            }
            catch(const std::exception& e)
            {
                m_bboxMetadata.m_searching = false;
                LOG(error) << "Caught Exception for m_elasticTask Async task: " <<  e.what() << endl;
            }
        }
    }
}

void ElasticMetadataStore::fetchMetadata()
{
    if (m_useId == false)
    {
        elasticSearch::getBboxPosition(m_bboxMetadata);
    }
    else
    {
        elasticSearch::getBboxPositionStreamer(m_bboxMetadata);
    }
}

bool ElasticMetadataStore::isSearching()
{
    return m_bboxMetadata.m_searching;
}

void ElasticMetadataStore::fetchMetadataAgain(const std::string& newStartTime)
{
    std::queue<Json::Value> empty;
    {
        std::lock_guard<std::mutex> guard(m_bboxMetadata.m_hitsLock);
        std::queue<Json::Value>& non_empty = m_bboxMetadata.m_qHits;
        std::swap( non_empty, empty );
    }
    m_bboxMetadata.m_searchParams.m_start_time = newStartTime;
    m_bboxMetadata.m_searchParams.m_search_after = 0;
    fetchMetadata();
}