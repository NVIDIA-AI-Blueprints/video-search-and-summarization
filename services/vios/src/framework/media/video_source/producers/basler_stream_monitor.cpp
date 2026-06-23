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

#include "basler_stream_monitor.h"

#include "logger.h"

#include <dlfcn.h>

namespace nv_vms {

BaslerStreamMonitor* BaslerStreamMonitor::getInstance()
{
    static BaslerStreamMonitor instance;
    return &instance;
}

bool BaslerStreamMonitor::ensureLibraryLoaded()
{
    if (m_createFn && m_destroyFn) {
        return true;
    }
    if (!m_libHandle) {
        // RTLD_GLOBAL so the producer's deferred pylon symbols bind against the
        // LD_PRELOADed pylon libs already in the process symbol space.
        m_libHandle = dlopen("libbasler_producer.so", RTLD_NOW | RTLD_GLOBAL);
        if (!m_libHandle) {
            const char* err = dlerror();
            LOG(error) << "BaslerStreamMonitor: cannot load libbasler_producer.so ("
                       << (err ? err : "unknown") << "); basler streaming disabled" << std::endl;
            return false;
        }
    }
    m_createFn  = reinterpret_cast<CreateFn>(dlsym(m_libHandle, "createBaslerProducer"));
    m_destroyFn = reinterpret_cast<DestroyFn>(dlsym(m_libHandle, "destroyBaslerProducer"));
    if (!m_createFn || !m_destroyFn) {
        LOG(error) << "BaslerStreamMonitor: missing factory symbols in libbasler_producer.so"
                   << std::endl;
        return false;
    }
    return true;
}

bool BaslerStreamMonitor::addStream(const std::string& streamId, const std::string& serial)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_producers.count(streamId) != 0) {
        LOG(info) << "BaslerStreamMonitor: stream " << streamId << " already started" << std::endl;
        return true;
    }
    if (!ensureLibraryLoaded()) {
        return false;
    }

    // Own the producer via shared_ptr with a deleter that calls back into the
    // library that allocated it. This also satisfies the producer's
    // enable_shared_from_this base used by the consumer fan-out.
    DestroyFn destroyFn = m_destroyFn;
    std::shared_ptr<IMediaDataProducer> producer(
        m_createFn(streamId.c_str(), serial.c_str()),
        [destroyFn](IMediaDataProducer* p) { if (p && destroyFn) destroyFn(p); });

    if (!producer) {
        LOG(error) << "BaslerStreamMonitor: failed to create producer for serial " << serial
                   << std::endl;
        return false;
    }
    if (!producer->start()) {
        LOG(error) << "BaslerStreamMonitor: failed to start producer for serial " << serial
                   << std::endl;
        return false;  // producer destroyed by its deleter on scope exit
    }
    m_producers[streamId] = std::move(producer);
    LOG(info) << "BaslerStreamMonitor: started basler stream " << streamId << " serial " << serial
              << std::endl;
    return true;
}

void BaslerStreamMonitor::removeStream(const std::string& streamId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto it = m_producers.find(streamId);
    if (it == m_producers.end()) {
        return;
    }
    if (it->second) {
        it->second->stop();
    }
    m_producers.erase(it);  // last owner; deleter calls destroyBaslerProducer
    {
        std::lock_guard<std::mutex> hlock(m_headersMutex);
        m_videoHeaders.erase(streamId);
    }
    LOG(info) << "BaslerStreamMonitor: removed basler stream " << streamId << std::endl;
}

std::shared_ptr<IMediaDataProducer> BaslerStreamMonitor::getProducer(const std::string& streamId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto it = m_producers.find(streamId);
    return it != m_producers.end() ? it->second : nullptr;
}

std::string BaslerStreamMonitor::getVideoCodec(const std::string& streamId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    // The Basler producer encodes H.264; codec is constant for a known stream.
    return m_producers.count(streamId) != 0 ? "h264" : "";
}

void BaslerStreamMonitor::setVideoHeaders(const std::string& streamId,
                                          std::vector<std::vector<uint8_t>> headers)
{
    std::lock_guard<std::mutex> lock(m_headersMutex);
    m_videoHeaders[streamId] = std::move(headers);
}

std::queue<std::vector<uint8_t>> BaslerStreamMonitor::getVideoHeaders(const std::string& streamId)
{
    std::lock_guard<std::mutex> lock(m_headersMutex);
    std::queue<std::vector<uint8_t>> result;
    auto it = m_videoHeaders.find(streamId);
    if (it != m_videoHeaders.end()) {
        for (const auto& nal : it->second) {
            result.push(nal);
        }
    }
    return result;
}

} // namespace nv_vms
