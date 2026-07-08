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

// Owns per-camera Basler producers, analogous to NativeStreamMonitor. Lazily
// dlopens libbasler_producer.so so this translation unit carries no pylon link dependency.
class BaslerStreamMonitor
{
public:
    static BaslerStreamMonitor* getInstance();

    // Start grabbing from serial for streamId. Idempotent; returns false if pylon unavailable.
    bool addStream(const std::string& streamId, const std::string& serial);
    void removeStream(const std::string& streamId);

    // Returns the producer for streamId, or nullptr if not started.
    std::shared_ptr<IMediaDataProducer> getProducer(const std::string& streamId);

    // Returns "h264" when the stream is active, "" otherwise.
    std::string getVideoCodec(const std::string& streamId);

    // SPS/PPS headers for the RTSP SDP sprop-parameter-sets.
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

    // Separate lock from m_mutex so the grab thread can report headers without blocking addStream.
    std::map<std::string, std::vector<std::vector<uint8_t>>> m_videoHeaders;
    std::mutex m_headersMutex;
};

} // namespace nv_vms
