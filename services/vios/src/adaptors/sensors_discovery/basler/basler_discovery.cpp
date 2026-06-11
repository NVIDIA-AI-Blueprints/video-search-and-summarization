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
#include <pylon/ImageFormatConverter.h>  // CImageFormatConverter (libpylonutility)
#include <pylon/PylonImage.h>            // CPylonImage (libpylonutility)

#include <dlfcn.h>

#include <cstdint>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <vector>

using namespace nv_vms;

namespace {

constexpr const char* kManufacturer = "Basler";

// PoC frame-dump configuration.
constexpr const char* kGigEDeviceClass = "BaslerGigE";  // restrict open() to GigE transport
constexpr const char* kDumpDirEnv = "BASLER_DUMP_DIR";   // override output root
constexpr const char* kDefaultDumpDir = "/tmp/basler_poc";
constexpr uint32_t kDumpFrameCount = 100;                // frames to grab per camera
constexpr unsigned kGrabTimeoutMs = 5000;                // per-frame RetrieveResult timeout

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
        void* utility{nullptr};
        Loader()
        {
            // libpylonbase   : core camera/transport/grab classes + GenICam.
            // libpylonutility: CImageFormatConverter / CPylonImage, used to
            //                  demosaic + convert grabbed Bayer frames to I420.
            //                  It is NOT a dependency of libpylonbase, so it
            //                  must be loaded explicitly.
            // NOTE: the adaptor loader dlopens this .so with RTLD_NOW, so all
            // pylon symbols must already be in the process symbol space at
            // adaptor-load time - that is provided by LD_PRELOAD of BOTH libs in
            // the deployment. This dlopen runs later (in start()) and therefore
            // only serves as an explicit handle + the basis for graceful-disable
            // when pylon is absent.
            base = dlopen("libpylonbase.so", RTLD_NOW | RTLD_GLOBAL);
            utility = dlopen("libpylonutility.so", RTLD_NOW | RTLD_GLOBAL);
            if (!base || !utility) {
                const char* err = dlerror();
                LOG(error) << "BaslerDiscovery: pylon SDK not available ("
                           << (err ? err : "unknown")
                           << "); basler discovery disabled" << endl;
            }
        }
    };
    static Loader loader;
    return loader.base != nullptr && loader.utility != nullptr;
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

void BaslerDiscovery::dumpFirstFrames(const SensorInfo& sensor)
{
    namespace fs = std::filesystem;

    const char* envDir = std::getenv(kDumpDirEnv);
    const fs::path outDir = fs::path(envDir ? envDir : kDefaultDumpDir) / sensor.serial_number;

    std::error_code ec;
    fs::create_directories(outDir, ec);
    if (ec) {
        LOG(error) << "BaslerDiscovery: cannot create dump dir " << outDir.string()
                   << ": " << ec.message() << endl;
        return;
    }

    try {
        // Open this specific camera by serial, restricted to the GigE transport
        // layer. Uses the same pylon symbols already bound at runtime via the
        // dlopen(RTLD_GLOBAL) of libpylonbase.so in loadPylonRuntime().
        Pylon::CDeviceInfo deviceInfo;
        deviceInfo.SetDeviceClass(kGigEDeviceClass);
        deviceInfo.SetSerialNumber(Pylon::String_t(sensor.serial_number.c_str()));

        Pylon::CInstantCamera camera(Pylon::CTlFactory::GetInstance().CreateDevice(deviceInfo));
        camera.Open();

        // Pixel format is constant for the run; record it for offline debayering.
        Pylon::String_t pixelFormat = "unknown";
        GenApi::INodeMap& nodemap = camera.GetNodeMap();
        if (GenApi::CEnumerationPtr pf = nodemap.GetNode("PixelFormat");
            pf.IsValid() && GenApi::IsReadable(pf)) {
            pixelFormat = pf->ToString();
        }

        LOG(info) << "BaslerDiscovery: dumping " << kDumpFrameCount
                  << " frames from serial=" << sensor.serial_number
                  << " to " << outDir.string() << endl;

        // Grab oldest-first (default GrabStrategy_OneByOne); auto-stops after count.
        camera.StartGrabbing(kDumpFrameCount);

        // Demosaic + convert each Bayer frame to planar I420 (YUV420planar) with
        // pylon's CImageFormatConverter. We write TWO contiguous streams: the raw
        // sensor frames (Bayer) and the converted I420 frames. Each is a stream of
        // fixed-size, header-less frames, so a raw player scrubs by frame.
        Pylon::CImageFormatConverter converter;
        converter.OutputPixelFormat = Pylon::PixelType_YUV420planar;  // I420
        Pylon::CPylonImage i420Image;  // reusable conversion target

        std::ofstream rawStream, i420Stream;
        fs::path rawPath, i420Path;
        uint32_t saved = 0;
        while (camera.IsGrabbing() && !m_exit) {
            Pylon::CGrabResultPtr result;
            camera.RetrieveResult(kGrabTimeoutMs, result, Pylon::TimeoutHandling_ThrowException);
            if (!result->GrabSucceeded()) {
                LOG(warning) << "BaslerDiscovery: grab failed for serial=" << sensor.serial_number
                             << ": " << result->GetErrorDescription().c_str() << endl;
                continue;
            }

            // Open both streams + sidecar on the first successful frame, once the
            // geometry is known, so it can be encoded into the filenames.
            if (!rawStream.is_open()) {
                const std::string dims = std::to_string(result->GetWidth()) + "x"
                                       + std::to_string(result->GetHeight());
                rawPath  = outDir / ("basler_" + sensor.serial_number + "_" + dims
                                     + "_" + std::string(pixelFormat.c_str()) + ".raw");
                i420Path = outDir / ("basler_" + sensor.serial_number + "_" + dims + "_I420.yuv");
                rawStream.open(rawPath, std::ios::binary);
                i420Stream.open(i420Path, std::ios::binary);
                if (!rawStream || !i420Stream) {
                    LOG(error) << "BaslerDiscovery: cannot open dump file(s) under "
                               << outDir.string() << endl;
                    break;
                }
                std::ofstream meta(outDir / "metadata.txt");
                meta << "model=" << sensor.model << "\n"
                     << "serial=" << sensor.serial_number << "\n"
                     << "width=" << result->GetWidth() << "\n"
                     << "height=" << result->GetHeight() << "\n"
                     << "source_pixel_format=" << pixelFormat.c_str() << "\n"
                     << "raw_image_bytes=" << result->GetImageSize() << "\n"
                     << "converted_format=I420 (YUV420planar)\n"
                     << "frames=" << kDumpFrameCount << "\n";
            }

            // Raw sensor frame (Bayer mosaic).
            rawStream.write(static_cast<const char*>(result->GetBuffer()),
                            static_cast<std::streamsize>(result->GetImageSize()));

            // Converted I420 frame (demosaic + colorspace conversion happen here).
            converter.Convert(i420Image, result);
            i420Stream.write(static_cast<const char*>(i420Image.GetBuffer()),
                             static_cast<std::streamsize>(i420Image.GetImageSize()));
            ++saved;
        }

        camera.StopGrabbing();
        camera.Close();
        LOG(info) << "BaslerDiscovery: dumped " << saved << " frame(s) for serial="
                  << sensor.serial_number << " (raw=" << rawPath.string()
                  << ", i420=" << i420Path.string() << ")" << endl;
    } catch (const Pylon::GenericException& e) {
        LOG(error) << "BaslerDiscovery: frame dump failed for serial=" << sensor.serial_number
                   << ": " << e.GetDescription() << endl;
    }
}

void BaslerDiscovery::discoveryTask()
{
    while (!m_exit) {
        std::vector<SensorInfo> toDump;  // cameras newly added this cycle (PoC frame dump)
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
                // PoC: grab frames once per camera per process run - whether the
                // camera is brand-new or already registered (e.g. restored from
                // the DB on restart). m_dumpedSerials (checked below) keeps it
                // idempotent so a present camera is dumped at most once.
                toDump.push_back(fresh);
            }
        }

        // Dump frames OUTSIDE m_monitorMutex so a concurrent stop() is not
        // blocked for the duration of the grab. Each serial is dumped once.
        for (const auto& sensor : toDump) {
            if (m_exit) {
                break;
            }
            if (m_dumpedSerials.count(sensor.serial_number) != 0) {
                continue;
            }
            dumpFirstFrames(sensor);
            m_dumpedSerials.insert(sensor.serial_number);
        }

        {
            std::unique_lock<std::mutex> lck(m_sleeperLock);
            m_sleeperWait.wait_for(lck, kPollInterval, [this] { return m_exit.load(); });
        }
    }

    LOG(info) << "Exiting BaslerDiscovery task" << endl;
}
