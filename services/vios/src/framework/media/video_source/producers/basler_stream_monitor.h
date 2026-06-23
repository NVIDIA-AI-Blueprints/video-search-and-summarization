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

#include "media_producer.h"  // IMediaDataProducer

#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <vector>

namespace nv_vms {

// Registry that owns the per-camera Basler producers (each an IMediaDataProducer),
// analogous to NativeStreamMonitor. It is a plain owner/lifecycle manager -- it
// does not implement IMediaDataProducer itself; the per-camera producer does.
// It lazily dlopens libbasler_producer.so (which links pylon) on first use, so
// this translation unit -- compiled into libnvvideo_source.so, shipped in every
// image -- carries no pylon link/load dependency. Where pylon is absent the
// dlopen fails gracefully and basler streams simply do not start.
class BaslerStreamMonitor
{
public:
    static BaslerStreamMonitor* getInstance();

    // Open the camera identified by serial and start grabbing for streamId.
    // Idempotent per streamId. Returns false if pylon / libbasler_producer.so is
    // unavailable or the producer failed to start.
    bool addStream(const std::string& streamId, const std::string& serial);
    void removeStream(const std::string& streamId);

    // The producer for streamId (e.g. for the recorder to register a consumer),
    // or nullptr if none.
    std::shared_ptr<IMediaDataProducer> getProducer(const std::string& streamId);

    // Video codec of the stream, for the RTSP SDP (mirrors the registry-keyed
    // accessor WebrtcStreamProducer/NativeStreamMonitor expose). The Basler
    // producer always encodes H.264.
    std::string getVideoCodec(const std::string& streamId);

    // SPS/PPS for the RTSP SDP (sprop-parameter-sets). The producer reports its
    // cached headers via setVideoHeaders once captured; NvMediaSource reads them
    // at DESCRIBE via getVideoHeaders (mirrors WebrtcStreamProducer::getVideoHeaders).
    void setVideoHeaders(const std::string& streamId, std::vector<std::vector<uint8_t>> headers);
    std::queue<std::vector<uint8_t>> getVideoHeaders(const std::string& streamId);

private:
    BaslerStreamMonitor() = default;

    using CreateFn  = IMediaDataProducer* (*)(const char*, const char*);
    using DestroyFn = void (*)(IMediaDataProducer*);

    bool ensureLibraryLoaded();  // dlopen libbasler_producer.so once

    void*     m_libHandle{nullptr};
    CreateFn  m_createFn{nullptr};
    DestroyFn m_destroyFn{nullptr};
    std::map<std::string, std::shared_ptr<IMediaDataProducer>> m_producers;
    std::mutex m_mutex;

    // RTSP SDP headers (SPS/PPS) per stream. Separate lock from m_mutex so the
    // producer's grab thread can report headers without contending with addStream.
    std::map<std::string, std::vector<std::vector<uint8_t>>> m_videoHeaders;
    std::mutex m_headersMutex;
};

} // namespace nv_vms
