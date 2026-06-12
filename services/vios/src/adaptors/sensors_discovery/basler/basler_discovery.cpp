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

#include "basler_discovery.h"

#include "logger.h"
#include "sensor_info.h"
#include "utils.h"

#include <pylon/PylonIncludes.h>

#include <dlfcn.h>

#include <cstdint>
#include <exception>
#include <memory>
#include <vector>

using namespace nv_vms;

namespace {

constexpr const char* kManufacturer = "Basler";

// Lazily dlopen the pylon runtime so the adaptor .so itself can load on
// systems without pylon installed. RTLD_GLOBAL is required so that pylon's
// own libraries can resolve each other's symbols, and so the adaptor's
// deferred symbols (left unresolved by --unresolved-symbols=ignore-in-shared-libs
// at link time) bind against this handle. The static + C++17 thread-safe
// initialisation guarantees the dlopen runs exactly once per process.
bool loadPylonRuntime()
{
    struct Loader {
        void* base{nullptr};
        Loader()
        {
            // libpylonbase: core camera/transport/enumeration classes + GenICam.
            // Discovery only enumerates devices, so libpylonbase is sufficient;
            // the producer that grabs/converts frames (stream-processor) loads
            // libpylonutility itself.
            // NOTE: the adaptor loader dlopens this .so with RTLD_NOW, so all
            // pylon symbols must already be in the process symbol space at
            // adaptor-load time - that is provided by LD_PRELOAD in the
            // deployment. This dlopen runs later (in start()) and therefore only
            // serves as an explicit handle + the basis for graceful-disable when
            // pylon is absent.
            base = dlopen("libpylonbase.so", RTLD_NOW | RTLD_GLOBAL);
            if (!base) {
                const char* err = dlerror();
                LOG(error) << "BaslerDiscovery: pylon SDK not available ("
                           << (err ? err : "unknown")
                           << "); basler discovery disabled" << endl;
            }
        }
    };
    static Loader loader;
    return loader.base != nullptr;
}

}  // namespace

extern "C" ISensorDiscoveryInterface* createObject()
{
    return new BaslerDiscovery();
}

extern "C" void destroyObject(BaslerDiscovery* object)
{
    delete object;
}

BaslerDiscovery::BaslerDiscovery() = default;

BaslerDiscovery::~BaslerDiscovery()
{
    try {
        LOG(info) << "Destroying BaslerDiscovery" << endl;
        stop();
        if (m_pylonInitialized) {
            Pylon::PylonTerminate();
        }
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~BaslerDiscovery: " << e.what() << endl; }
        catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~BaslerDiscovery" << endl; }
        catch (...) { (void)std::current_exception(); }
    }
}

void BaslerDiscovery::start()
{
    std::lock_guard<std::mutex> lock(m_monitorMutex);
    if (!m_exit) {
        LOG(info) << "BaslerDiscovery already running" << endl;
        return;
    }
    // Resolve the pylon SDK at runtime. If it's not installed, leave the
    // discovery task unstarted — other adaptors continue to function.
    if (!loadPylonRuntime()) {
        return;
    }
    if (!m_pylonInitialized) {
        try {
            Pylon::PylonInitialize();
            m_pylonInitialized = true;
        } catch (const Pylon::GenericException& e) {
            LOG(error) << "BaslerDiscovery: PylonInitialize failed: "
                       << e.GetDescription() << endl;
            return;
        }
    }
    m_exit = false;
    m_discoveryThread = std::thread([this] { this->discoveryTask(); });
    LOG(info) << "Started Basler sensor discovery task" << endl;
}

void BaslerDiscovery::stop()
{
    std::thread joinThread;
    {
        std::lock_guard<std::mutex> lock(m_monitorMutex);
        if (m_exit) {
            return;
        }
        m_exit = true;
        m_sleeperWait.notify_all();
        joinThread = std::move(m_discoveryThread);
    }
    if (joinThread.joinable()) {
        joinThread.join();
    }
    LOG(info) << "Stopped Basler sensor discovery task" << endl;
}

std::string BaslerDiscovery::buildSensorIdFromSerial(const std::string& serial)
{
    // Map serial -> deterministic UUID-like string so successive discoveries
    // resolve to the same sensor id without depending on database state.
    if (serial.empty()) {
        return generate_uuid();
    }
    return std::string("basler-") + serial;
}

void BaslerDiscovery::doBaslerDiscovery(std::map<std::string, SensorInfo>& freshList)
{
    try {
        Pylon::CTlFactory& factory = Pylon::CTlFactory::GetInstance();
        Pylon::DeviceInfoList_t devices;
        const size_t n = factory.EnumerateDevices(devices);
        LOG(verbose) << "BaslerDiscovery: enumerated " << n << " device(s)" << endl;

        for (const auto& dev : devices) {
            const std::string deviceClass(dev.GetDeviceClass().c_str());
            const std::string vendor(dev.GetVendorName().c_str());
            // Only surface Basler-manufactured devices.
            if (vendor.find(kManufacturer) == std::string::npos) {
                continue;
            }

            const std::string serial(dev.GetSerialNumber().c_str());
            if (serial.empty()) {
                LOG(warning) << "BaslerDiscovery: skipping device with empty serial (model="
                             << dev.GetModelName().c_str() << ")" << endl;
                continue;
            }

            SensorInfo sensor;
            sensor.id = buildSensorIdFromSerial(serial);
            sensor.sensorId = sensor.id;
            sensor.type = SENSOR_TYPE_BASLER;
            sensor.name = std::string(dev.GetFriendlyName().c_str());
            sensor.model = std::string(dev.GetModelName().c_str());
            sensor.manufacturer = kManufacturer;
            sensor.serial_number = serial;
            sensor.hardware_id = serial;
            // CDeviceInfo::GetIpAddress() / GetMacAddress() return empty for non-GigE
            // transports; that's fine - leave the fields empty in those cases.
            sensor.ip = std::string(dev.GetIpAddress().c_str());
            sensor.location = std::string(dev.GetMacAddress().c_str());

            sensor.updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));

            // Advertise a single main stream. The real capture geometry/codec
            // are finalised by the producer when it opens the camera (later
            // stage); sane H.264/1080p defaults here are enough to take the
            // sensor to streaming state in the stream-processor.
            auto stream = std::make_shared<StreamInfo>();
            stream->sensorId     = sensor.id;
            stream->id           = sensor.id;
            stream->name         = sensor.name;
            stream->isMainStream = true;
            stream->updateStreamtype(StreamType::Rtsp);
            stream->updateErrorStatus(std::make_pair(StreamStatus::STREAM_STATUS_ONLINE,
                translateStreamStatusToString(StreamStatus::STREAM_STATUS_ONLINE)));
            SensorVideoEncoderSettingsValues encValues;
            encValues.encoding          = "h264";
            encValues.resolution.width  = "1920";
            encValues.resolution.height = "1080";
            encValues.frameRate         = "30";
            stream->updateVideoEncoderValues(encValues);
            sensor.streams.push_back(stream);

            LOG(verbose) << "BaslerDiscovery: class=" << deviceClass
                         << " model=" << sensor.model
                         << " serial=" << sensor.serial_number
                         << " ip=" << sensor.ip
                         << " mac=" << sensor.location << endl;

            freshList[serial] = std::move(sensor);
        }
    } catch (const Pylon::GenericException& e) {
        LOG(error) << "BaslerDiscovery: pylon exception during enumeration: "
                   << e.GetDescription() << endl;
    }
}

int BaslerDiscovery::addNewSensor(SensorInfo& sensor)
{
    sensor.isAutoDiscovered = true;
    sensor.updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
    return publishOnSensorFound(sensor);
}

void BaslerDiscovery::discoveryTask()
{
    while (!m_exit) {
        {
            std::lock_guard<std::mutex> lock(m_monitorMutex);
            m_freshList.clear();
            doBaslerDiscovery(m_freshList);

            refreshCacheSensorList();
            std::vector<shared_ptr<SensorInfo>> cacheList = getCacheSensorList();

            // Detect removed sensors (in cache but not in fresh list).
            for (const auto& cache : cacheList) {
                if (!cache || cache->type != SENSOR_TYPE_BASLER) {
                    continue;
                }
                if (m_freshList.find(cache->serial_number) == m_freshList.end()) {
                    LOG(info) << "BaslerDiscovery: removing sensor serial="
                              << cache->serial_number << endl;
                    publishOnSensorRemoved(cache->sensorId);
                }
            }

            // Detect new or returning sensors.
            for (auto& kv : m_freshList) {
                SensorInfo& fresh = kv.second;
                shared_ptr<SensorInfo> cacheMatch;
                for (const auto& cache : cacheList) {
                    if (cache && cache->type == SENSOR_TYPE_BASLER &&
                        cache->serial_number == fresh.serial_number) {
                        cacheMatch = cache;
                        break;
                    }
                }
                if (!cacheMatch) {
                    if (addNewSensor(fresh) == 0) {
                        LOG(info) << "BaslerDiscovery: added sensor serial="
                                  << fresh.serial_number
                                  << " model=" << fresh.model
                                  << " ip=" << fresh.ip << endl;
                    }
                } else if (cacheMatch->getSensorStatus() == SensorStatusOffline) {
                    publishOnSensorFound(*cacheMatch);
                }
            }
        }

        {
            std::unique_lock<std::mutex> lck(m_sleeperLock);
            m_sleeperWait.wait_for(lck, kPollInterval, [this] { return m_exit.load(); });
        }
    }

    LOG(info) << "Exiting BaslerDiscovery task" << endl;
}
