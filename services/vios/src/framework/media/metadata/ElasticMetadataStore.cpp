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

#include "ElasticMetadataStore.h"
#include "logger.h"
#include "utils.h"

#include <algorithm>
#include <chrono>
#include <iterator>
#include <utility>
#include <vector>

namespace
{
    // Prefetch tuning (constants, not config, to keep the change self-contained).
    // Slice window kept small enough that even high-fps streams stay well under
    // the Elasticsearch max_result_window (10k) for a single-shot slice query.
    constexpr int64_t  PREFETCH_SLICE_MS      = 60 * 1000;      // 60s per slice
    constexpr int      PREFETCH_SLICE_MAX_HITS = 10000;         // ES size cap per slice
    // Cap total look-ahead so very long downloads don't load the whole clip into
    // RAM. Beyond this, the incremental refill (checkAndRefillMetadata) handles
    // the tail - the large head start keeps it ahead of consumption.
    constexpr int64_t  PREFETCH_MAX_WINDOW_MS = 10 * 60 * 1000; // 10 min
    constexpr uint16_t PREFETCH_DATASIZE_CAP  = 60000;          // fits atomic<uint16_t>
    // Non-zero seed for m_dataSize when a tail beyond the look-ahead window may
    // still need fetching. Must be >= 2 so the refill threshold (dataSize/2)
    // actually fires when the queue is empty; the incremental fetch overwrites
    // it with the real batch size on the first successful call.
    constexpr uint16_t PREFETCH_TAIL_BOOTSTRAP = 300;

    // Bounded blocking wait in getMetadata (download path only). getMetadata
    // wakes on the m_dataReady signal as soon as a fetch delivers data; this is
    // only the backstop timeout so a slow/unavailable Elasticsearch degrades to
    // occasional flicker, never a hung download.
    constexpr int      GET_WAIT_BUDGET_MS = 300;
}

ElasticMetadataStore::ElasticMetadataStore(MetadataParams& params, bool use_frameid)
    : m_bboxMetadata(metadataQueue(), metadataQueueMutex())
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
    // Both tasks capture `this`; join them before destruction to avoid
    // use-after-free.
    if (m_prefetchTask.valid())
    {
        try
        {
            m_prefetchTask.get();
        }
        catch(const std::exception& e)
        {
            LOG(error) << "Caught Exception for m_prefetchTask Async task: " <<  e.what() << endl;
        }
    }
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

// Pops queue entries older than frameTS and returns the first entry whose
// timestamp is >= frameTS (the "ceiling" match), or nullValue if the queue
// drained. Caller must NOT hold metadataQueueMutex().
Json::Value ElasticMetadataStore::matchQueueFront(const int64_t frameTS)
{
    std::lock_guard<std::mutex> guard(metadataQueueMutex());
    Json::Value metadata = Json::nullValue;
    if (!metadataQueue().empty())
    {
        metadata = metadataQueue().front();
        int64_t elasticTS = metadata["epocTime"].asUInt64() * 1000;
        while(elasticTS < frameTS && !metadataQueue().empty())
        {
            metadataQueue().pop();
            if (!metadataQueue().empty())
            {
                metadata = metadataQueue().front();
                elasticTS = metadata["epocTime"].asUInt64() * 1000;
            }
            else
            {
                metadata = Json::nullValue;
                break;
            }
        }
    }
    return metadata;
}

Json::Value ElasticMetadataStore::getMetadata(const int64_t frameTS)
{
    checkAndRefillMetadata(frameTS);
    Json::Value metadata = matchQueueFront(frameTS);

    // Default (recorded playback / webrtc replay / vod): non-blocking, exactly
    // as before - realtime playback keeps the async prefetch ahead, so an empty
    // queue is rare and simply skips the box for that frame.
    if (metadata != Json::nullValue || !m_blockingGet)
    {
        return metadata;
    }

    // Download path only (m_blockingGet set by startPrefetch): the transcode
    // produces frames far faster than real time, so a momentarily empty queue
    // must NOT drop the overlay. Block until a fetch signals that new metadata
    // is available, then retry. m_dataReady is signaled by the prefetch and the
    // incremental refill when they push/complete, so we wake as soon as data
    // lands - no polling. The deadline is only a backstop so a slow/unavailable
    // Elasticsearch degrades to occasional flicker instead of a hung download.
    const auto deadline = std::chrono::steady_clock::now()
                        + std::chrono::milliseconds(GET_WAIT_BUDGET_MS);
    while (true)
    {
        // The prefetch turns blocking off if Elasticsearch came back empty /
        // unreachable; bail immediately so we never keep stalling per frame.
        if (!m_blockingGet)
        {
            return Json::nullValue;
        }

        const bool moreExpected = m_prefetchInFlight
                                || m_bboxMetadata.m_searching
                                || (!m_fullyPrefetched && m_bboxMetadata.m_dataSize != 0);
        if (!moreExpected)
        {
            return Json::nullValue;  // genuinely exhausted - nothing more will arrive
        }

        const auto now = std::chrono::steady_clock::now();
        if (now >= deadline)
        {
            return Json::nullValue;
        }

        // Make sure a fetch is in flight for the tail case (nothing running but
        // more data expected). checkAndRefillMetadata won't double-spawn, and
        // its lock is independent of m_dataReady's, so there is no deadlock.
        checkAndRefillMetadata(frameTS);

        const auto remainMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                                  deadline - now).count();
        // Wakes immediately if a signal already arrived (persistent flag) or as
        // soon as one does; otherwise returns at the bounded timeout.
        m_dataReady.wait(static_cast<unsigned int>(remainMs));

        metadata = matchQueueFront(frameTS);
        if (metadata != Json::nullValue)
        {
            return metadata;
        }
    }
}

void ElasticMetadataStore::checkAndRefillMetadata(const int64_t frameTS)
{
    // While the eager prefetch owns the queue, or once it has loaded the whole
    // range, the incremental (search_after) refill must stay out of the way -
    // it would race the prefetch on m_search_after / the queue and reorder data.
    if (m_prefetchInFlight || m_fullyPrefetched)
    {
        return;
    }
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
            // Wake any getMetadata blocked in the bounded wait: new records were
            // pushed (or the fetch finished with none, so it can re-evaluate).
            m_dataReady.signal();
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

void ElasticMetadataStore::startPrefetch()
{
    // If Elasticsearch is not configured there is no metadata to fetch (e.g. a
    // local standalone setup that only exercises the debug timestamp overlay).
    // Do NOT enable the blocking wait - getMetadata must stay non-blocking so
    // every frame is emitted immediately, never stalling the download.
    if (GET_CONFIG().video_metadata_server.empty())
    {
        LOG(info) << "startPrefetch: video metadata server not configured; "
                     "overlay metadata disabled, getMetadata stays non-blocking" << endl;
        return;  // m_blockingGet stays false
    }

    // Enable the bounded blocking wait in getMetadata for this (download) store.
    m_blockingGet = true;

    // Frame-id (streamer) mode is not used by the download path; keep the
    // existing behavior for it.
    if (m_useId)
    {
        fetchMetadata();
        return;
    }
    if (m_prefetchInFlight)
    {
        return;
    }

    // Publish "work in flight" synchronously so a getMetadata() call that races
    // ahead of the async task waits for the prefetch instead of returning null.
    m_prefetchInFlight = true;
    m_bboxMetadata.m_searching = true;
    m_prefetchTask = async::spawn([this] { prefetchRange(); });
}

void ElasticMetadataStore::prefetchRange()
{
    try
    {
        const std::string sensor   = m_bboxMetadata.m_searchParams.m_sensor_id;
        const std::string startIso = m_bboxMetadata.m_searchParams.m_start_time;
        const std::string endIso   = m_bboxMetadata.m_searchParams.m_end_time;

        const int64_t startMs = static_cast<int64_t>(getEpocTimeInMS(startIso));
        const int64_t endMs   = static_cast<int64_t>(getEpocTimeInMS(endIso));

        if (startMs <= 0 || endMs <= 0 || endMs <= startMs)
        {
            // Range is not usable for slicing (e.g. empty end time). Fall back to
            // the proven sequential fetch; incremental refill handles the rest.
            LOG(warning) << "prefetchRange: unusable range [" << startIso << " .. "
                         << endIso << "], using sequential fetch" << endl;
            elasticSearch::getBboxPosition(m_bboxMetadata);
        }
        else
        {
            const int64_t windowEndMs = std::min(endMs, startMs + PREFETCH_MAX_WINDOW_MS);
            const bool coversWholeRange = (windowEndMs >= endMs);

            // Time slices. Intermediate slices are half-open ([s, e-1ms]) so
            // adjacent slices never return the same record twice; the final
            // slice keeps the inclusive upper bound (== the original lte
            // end_time semantics) so a record landing exactly on windowEndMs is
            // not dropped.
            std::vector<std::pair<std::string, std::string>> slices;
            for (int64_t s = startMs; s < windowEndMs; s += PREFETCH_SLICE_MS)
            {
                const int64_t e = std::min(windowEndMs, s + PREFETCH_SLICE_MS);
                const bool isLast = (e >= windowEndMs);
                const int64_t endBoundMs = isLast ? e : (e - 1);
                slices.emplace_back(convertEpocToISO8601_2(s * 1000),
                                    convertEpocToISO8601_2(endBoundMs * 1000));
            }

            // Fetch all slices in parallel, then wait for them (same idiom as
            // streamrecorder's parallel duration retrieval).
            std::vector<async::task<std::pair<bool, std::vector<Json::Value>>>> tasks;
            tasks.reserve(slices.size());
            for (const auto& sl : slices)
            {
                const std::string sStart = sl.first;
                const std::string sEnd   = sl.second;
                tasks.push_back(async::spawn([sensor, sStart, sEnd]
                {
                    return elasticSearch::fetchRangeHits(sensor, sStart, sEnd,
                                                         PREFETCH_SLICE_MAX_HITS);
                }));
            }

            std::vector<Json::Value> all;
            bool anyReachable = false;
            for (auto& t : tasks)
            {
                std::pair<bool, std::vector<Json::Value>> part = t.get();
                anyReachable = anyReachable || part.first;
                all.insert(all.end(),
                           std::make_move_iterator(part.second.begin()),
                           std::make_move_iterator(part.second.end()));
            }

            // Parallel slices arrive out of order; the consumer needs ascending
            // timestamps.
            std::sort(all.begin(), all.end(),
                      [](const Json::Value& a, const Json::Value& b)
                      {
                          return a["epocTime"].asUInt64() < b["epocTime"].asUInt64();
                      });

            {
                std::lock_guard<std::mutex> guard(metadataQueueMutex());
                for (auto& h : all)
                {
                    metadataQueue().push(h);
                }
            }

            if (!all.empty())
            {
                // NOTE: for 3D sensors epocTime derives from info[sensorId]
                // rather than the @timestamp sort value; the incremental tail
                // handoff below seeds search_after from epocTime, so a 3D-sensor
                // clip longer than the look-ahead window may skip/re-fetch a few
                // records at the seam. Acceptable for that niche path.
                const Json::UInt64 lastTs = all.back()["epocTime"].asUInt64();
                if (lastTs > m_bboxMetadata.m_searchParams.m_search_after)
                {
                    m_bboxMetadata.m_searchParams.m_search_after = lastTs;
                }
            }
            m_fullyPrefetched = coversWholeRange;
            if (all.empty())
            {
                if (!anyReachable)
                {
                    // Elasticsearch could not be reached. Turn off the per-frame
                    // blocking wait so frames (e.g. the debug timestamp overlay)
                    // are emitted immediately instead of each stalling for the
                    // wait budget. Seed the tail so incremental refill can still
                    // recover non-blockingly if ES comes back.
                    m_blockingGet = false;
                    m_bboxMetadata.m_dataSize = coversWholeRange ? 0 : PREFETCH_TAIL_BOOTSTRAP;
                }
                else if (coversWholeRange)
                {
                    // ES answered: the entire clip genuinely has no metadata.
                    // Nothing will ever arrive, so stop blocking.
                    m_blockingGet = false;
                    m_bboxMetadata.m_dataSize = 0;
                }
                else
                {
                    // ES answered but this head window was empty (e.g. the camera
                    // was idle early on); later parts of the clip may still have
                    // metadata. Keep blocking and let the incremental refill fetch
                    // the tail so those frames are not dropped.
                    m_bboxMetadata.m_dataSize = PREFETCH_TAIL_BOOTSTRAP;
                }
            }
            else
            {
                // m_dataSize drives both the incremental refill threshold and
                // getMetadata's "more expected" check. When the whole range is
                // loaded, m_fullyPrefetched short-circuits the refill regardless.
                m_bboxMetadata.m_dataSize = static_cast<uint16_t>(
                    std::min<size_t>(all.size(), PREFETCH_DATASIZE_CAP));
            }

            LOG(info) << "prefetchRange: loaded " << all.size() << " records over "
                      << slices.size() << " slice(s), fullRange=" << coversWholeRange
                      << ", camera=" << sensor << endl;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "prefetchRange: exception: " << e.what()
                   << "; disabling blocking wait, incremental refill may recover" << endl;
        // A fetch failed. Stop the per-frame blocking wait so the download is
        // never stalled waiting on a broken Elasticsearch. Leave the incremental
        // refill armed (non-blocking) in case it recovers later.
        m_blockingGet = false;
        if (m_bboxMetadata.m_dataSize == 0)
        {
            m_bboxMetadata.m_dataSize = PREFETCH_TAIL_BOOTSTRAP;
        }
    }

    m_bboxMetadata.m_searching = false;
    m_prefetchInFlight = false;
    // Wake getMetadata: records are loaded (or the range is genuinely empty and
    // the flags above now reflect that), so it can match or terminate.
    m_dataReady.signal();
}