/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "vstmodule.h"
#include "rtspservermanager.h"
#include "logger.h"
#include "vst_common.h"

#include <dlfcn.h>

using namespace std;

std::unordered_map<std::string, ModuleId> g_moduleMap =
{
    {"rtspserver", ModuleRtspServer},
    {"sensor", ModuleSensorManagement},
    {"storage", ModuleStorageManagement},
    {"recorder", ModuleStreamRecorder},
    {"livestream", ModuleLiveStream},
    {"replaystream", ModuleReplayStream},
    {"streambridge", ModuleStreamBridge},
    {"streamprocessing", ModuleStreamProcessing}
};

ModuleLoader::ModuleLoader()
{
    LOG(info) << "Created ModuleLoader instance" << endl;
}

std::shared_ptr<DeviceManager> ModuleLoader::getDeviceManagerObject()
{
    if (m_deviceManager.get() == nullptr)
    {
        return nullptr;
    }
    return m_deviceManager;
}
#ifdef UNIT_TEST
void ModuleLoader::setDeviceManagerForTest(std::shared_ptr<DeviceManager> dm)
{
    m_deviceManager = std::move(dm);
}
#endif
string ModuleLoader::getDeviceId()
{
    string id;
    if (m_deviceManager.get() != nullptr)
    {
        id = m_deviceManager->id;
    }
    return id;
}

string ModuleLoader::getDeviceType()
{
    if (m_deviceManager.get() != nullptr)
    {
        return m_deviceManager->type;
    }
    return TYPE_UNKNOWN;
}

int ModuleLoader::initialize(ModuleId module_id)
{
    int ret = 0;
    
    // Parse and set media adaptor lib path early, independent of adaptor loading
    string mediaAdaptorPath = getMediaAdaptorLibPath();
    if (!mediaAdaptorPath.empty())
    {
        setMediaAdaptorLibPath(mediaAdaptorPath);
    }
    
    m_deviceManager = m_loader.loadAdaptor(module_id);
    if (m_deviceManager.get() != nullptr)
    {
        m_deviceManager->deviceManagerInit(module_id);
    }

    if (module_id == ModuleStreamRecorder || module_id == ModuleAll || module_id == ModuleStreamProcessing || module_id == ModuleStreamBridge)
    {
        if (m_deviceManager->requiredRecording() == true)
        {
            ret = loadStreamRecorderLib();
            if (ret != 0)
            {
                LOG(error) << "Failed to load Stream Recorder module" << endl;
                return ret;
            }
        }
        else
        {
            LOG(info) << "No need to load StreamRecorderLib" << endl;
        }
    }

    if (module_id == ModuleStorageManagement || module_id == ModuleAll || module_id == ModuleStreamProcessing || module_id == ModuleStreamBridge)
    {
        if (m_deviceManager->requiredStorageMngt() == true)
        {
            ret = loadStorageManagementLib();
            if (ret != 0)
            {
                LOG(error) << "Failed to load Storage management module" << endl;
                return ret;
            }

            ret = loadMediaAdaptor();
            if (ret != 0)
            {
                LOG(error) << "Failed to load media adaptor" << endl;
                return ret;
            }
        }
        else
        {
            LOG(info) << "No need to load StorageManagementLib" << endl;
        }
    }

    /* Load the individual module libs based on provided option */
    if (module_id == ModuleRtspServer || module_id == ModuleAll || module_id == ModuleStreamProcessing || module_id == ModuleStreamBridge)
    {
        if (m_deviceManager->requiredRtspServer() == true)
        {
            ret = loadRtspServerLib();
            if (ret != 0)
            {
                LOG(error) << "Failed to load RTSP server module" << endl;
                return ret;
            }
        }
        else
        {
            LOG(info) << "No need to load RtspServerLib" << endl;
        }
    }

    if (module_id == ModuleSensorManagement || module_id == ModuleAll )
    {
        ret = loadSensorManagementLib();
        if (ret != 0)
        {
            LOG(error) << "Failed to load Sensor management module" << endl;
            return ret;
        }
    }

    if (module_id == ModuleLiveStream || module_id == ModuleAll || module_id == ModuleStreamProcessing)
    {
        ret = loadPeerConnectionLiveLib();
        if (ret != 0)
        {
            LOG(error) << "Failed to load Peer Connection Live module" << endl;
            return ret;
        }
    }

    if (module_id == ModuleReplayStream || module_id == ModuleAll || module_id == ModuleStreamProcessing)
    {
        ret = loadPeerConnectionReplayLib();
        if (ret != 0)
        {
            LOG(error) << "Failed to load Peer Connection Replay module" << endl;
            return ret;
        }
    }

    if (module_id == ModuleStreamBridge || module_id == ModuleAll)
    {
        ret = loadStreamBridgeLib();
        if (ret != 0)
        {
            LOG(error) << "Failed to load StreamBridge module" << endl;
            return ret;
        }
    }

    return ret;
}

ModuleId ModuleLoader::getModuleId(const std::string& moduleName)
{
    if (moduleName.empty() == true)
    {
        /* Empty module means build all */
        return ModuleAll;
    }
    auto it = g_moduleMap.find(moduleName);
    if (it != g_moduleMap.end())
    {
        return it->second;
    }
    else
    {
        return ModuleInvalid; // Invalid module
    }
}

string ModuleLoader::getModuleIdAsString(ModuleId module_id)
{
    string modulestr;
    if (module_id == ModuleAll)
    {
        /* Empty module means module-all */
        return modulestr;
    }
    for (const auto& pair : g_moduleMap)
    {
        if (pair.second == module_id)
        {
            modulestr = pair.first;
        }
    }
    return modulestr;
}

int ModuleLoader::loadRtspServerLib()
{
    int ret = 0;
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvrtspserver.so");
    m_handleRtspServer = dlopen(lib_path, RTLD_LAZY);
    if (!m_handleRtspServer)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvrtspserver.so");
        m_handleRtspServer = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvrtspserver.so");
    m_handleRtspServer = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handleRtspServer)
    {
        LOG(error) << "Cannot open rtsp server library: " << dlerror() << endl;
        ret = -1;
    }
    else
    {
        dlerror();
        createObject_t createObject = (createObject_t) dlsym(m_handleRtspServer, "createRtspServerManagerObject");
        deleteObject_t deleteObject = (deleteObject_t) dlsym(m_handleRtspServer, "deleteRtspServerManagerObject");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbols of rtsp server library" << dlsym_error << endl;
            dlclose(m_handleRtspServer);
            m_handleRtspServer = nullptr;
            return -1;
        }
        m_rtspServer = createObject();
        if (m_rtspServer)
        {
            m_rtspServer->m_deleteObject = deleteObject;
            m_rtspServer->postInit();
        }
        else
        {
            LOG(error) << "Failed to start rtsp-server module" << endl;
            return -1;
        }
        LOG(info) << "Loaded rtspServer module: " << createObject << endl;
    }
    return ret;
}

int ModuleLoader::loadStreamRecorderLib()
{
    int ret = 0;
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvstreamrecorder.so");
    m_handleStreamRecorder = dlopen(lib_path, RTLD_LAZY);
    if (!m_handleStreamRecorder)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvstreamrecorder.so");
        m_handleStreamRecorder = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvstreamrecorder.so");
    m_handleStreamRecorder = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handleStreamRecorder)
    {
        LOG(error) << "Cannot open stream recorder library: " << dlerror() << endl;
        ret = -1;
    }
    else
    {
        dlerror();
        createObject_t createObject = (createObject_t) dlsym(m_handleStreamRecorder, "createStreamRecorderObject");
        deleteObject_t deleteObject = (deleteObject_t) dlsym(m_handleStreamRecorder, "deleteStreamRecorderObject");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbols of stream recorder library" << dlsym_error << endl;
            dlclose(m_handleStreamRecorder);
            m_handleStreamRecorder = nullptr;
            return -1;
        }
        m_streamRecorder = createObject();
        if (m_streamRecorder)
        {
            m_streamRecorder->m_deleteObject = deleteObject;
        }
        else
        {
            LOG(error) << "Failed to start stream recorder module" << endl;
            return -1;
        }
        LOG(info) << "Loaded stream recorder module: " << createObject << endl;
    }
    return ret;
}

int ModuleLoader::loadStorageManagementLib()
{
    int ret = 0;
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvstoragemanagement.so");
    m_handleStorageManagement = dlopen(lib_path, RTLD_LAZY);
    if (!m_handleStorageManagement)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvstoragemanagement.so");
        m_handleStorageManagement = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvstoragemanagement.so");
    m_handleStorageManagement = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handleStorageManagement)
    {
        LOG(error) << "Cannot open storage management library: " << dlerror() << endl;
        ret = -1;
    }
    else
    {
        dlerror();
        createObject_t createObject = (createObject_t) dlsym(m_handleStorageManagement, "createStorageManagementObject");
        deleteObject_t deleteObject = (deleteObject_t) dlsym(m_handleStorageManagement, "deleteStorageManagementObject");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbols of storage management library" << dlsym_error << endl;
            dlclose(m_handleStorageManagement);
            m_handleStorageManagement = nullptr;
            return -1;
        }
        m_storageManagement = createObject();
        if (m_storageManagement)
        {
            m_storageManagement->m_deleteObject = deleteObject;
        }
        else
        {
            LOG(error) << "Failed to start storage management module" << endl;
            return -1;
        }
        LOG(info) << "Loaded storage management module: " << createObject << endl;
    }
    return ret;
}

int ModuleLoader::loadPeerConnectionManagerLib()
{
    int ret = 0;
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvwebrtc_streamer.so");
    m_handlePeerConnectionManager = dlopen(lib_path, RTLD_LAZY);
    if (!m_handlePeerConnectionManager)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvwebrtc_streamer.so");
        m_handlePeerConnectionManager = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvwebrtc_streamer.so");
    m_handlePeerConnectionManager = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handlePeerConnectionManager)
    {
        LOG(error) << "Cannot open peerconnection library: " << dlerror() << endl;
        ret = -1;
    }
    else
    {
        dlerror();
        createObject_t createObject = (createObject_t) dlsym(m_handlePeerConnectionManager, "createPeerConnectionManagerObject");
        deleteObject_t deleteObject = (deleteObject_t) dlsym(m_handlePeerConnectionManager, "deletePeerConnectionManagerObject");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbols of peerconnection library" << dlsym_error << endl;
            dlclose(m_handlePeerConnectionManager);
            m_handlePeerConnectionManager = nullptr;
            return -1;
        }
        m_peerConnectionManager = createObject();
        if (m_peerConnectionManager)
        {
            m_peerConnectionManager->m_deleteObject = deleteObject;
        }
        else
        {
            LOG(error) << "Failed to start peerconnection module" << endl;
            return -1;
        }
        LOG(info) << "Loaded peerconnection module: " << createObject << endl;
    }
    return ret;
}

int ModuleLoader::loadPeerConnectionLiveLib()
{
    int ret = 0;
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvpeerconnection_live.so");
    m_handlePeerConnectionLiveManager = dlopen(lib_path, RTLD_LAZY);
    if (!m_handlePeerConnectionLiveManager)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvpeerconnection_live.so");
        m_handlePeerConnectionLiveManager = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvpeerconnection_live.so");
    m_handlePeerConnectionLiveManager = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handlePeerConnectionLiveManager)
    {
        LOG(error) << "Cannot open peerconnection live library: " << dlerror() << endl;
        ret = -1;
    }
    else
    {
        dlerror();
        createObject_t createObject = (createObject_t) dlsym(m_handlePeerConnectionLiveManager, "createPeerConnectionLiveManagerObject");
        deleteObject_t deleteObject = (deleteObject_t) dlsym(m_handlePeerConnectionLiveManager, "deletePeerConnectionLiveManagerObject");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbols of peerconnection live library" << dlsym_error << endl;
            dlclose(m_handlePeerConnectionLiveManager);
            m_handlePeerConnectionLiveManager = nullptr;
            return -1;
        }
        m_peerConnectionLiveManager = createObject();
        if (m_peerConnectionLiveManager)
        {
            m_peerConnectionLiveManager->m_deleteObject = deleteObject;
        }
        else
        {
            LOG(error) << "Failed to start LivePeerConnection module" << endl;
            return -1;
        }
        LOG(info) << "Loaded LivePeerConnection module: " << createObject << endl;
    }
    return ret;
}

int ModuleLoader::loadPeerConnectionReplayLib()
{
    int ret = 0;
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvpeerconnection_replay.so");
    m_handlePeerConnectionReplayManager = dlopen(lib_path, RTLD_LAZY);
    if (!m_handlePeerConnectionReplayManager)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvpeerconnection_replay.so");
        m_handlePeerConnectionReplayManager = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvpeerconnection_replay.so");
    m_handlePeerConnectionReplayManager = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handlePeerConnectionReplayManager)
    {
        LOG(error) << "Cannot open peerconnection replay library: " << dlerror() << endl;
        ret = -1;
    }
    else
    {
        dlerror();
        createObject_t createObject = (createObject_t) dlsym(m_handlePeerConnectionReplayManager, "createPeerConnectionReplayManagerObject");
        deleteObject_t deleteObject = (deleteObject_t) dlsym(m_handlePeerConnectionReplayManager, "deletePeerConnectionReplayManagerObject");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbols of peerconnection replay library" << dlsym_error << endl;
            dlclose(m_handlePeerConnectionReplayManager);
            m_handlePeerConnectionReplayManager = nullptr;
            return -1;
        }
        m_peerConnectionReplayManager = createObject();
        if (m_peerConnectionReplayManager)
        {
            m_peerConnectionReplayManager->m_deleteObject = deleteObject;
        }
        else
        {
            LOG(error) << "Failed to start ReplayPeerConnection module" << endl;
            return -1;
        }
        LOG(info) << "Loaded ReplayPeerConnection module: " << createObject << endl;
    }
    return ret;
}

int ModuleLoader::loadStreamBridgeLib()
{
    int ret = 0;
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvstreambridge.so");
    m_handleStreamBridge = dlopen(lib_path, RTLD_LAZY);
    if (!m_handleStreamBridge)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvstreambridge.so");
        m_handleStreamBridge = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvstreambridge.so");
    m_handleStreamBridge = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handleStreamBridge)
    {
        LOG(error) << "Cannot open streambridge library: " << dlerror() << endl;
        ret = -1;
    }
    else
    {
        dlerror();
        createObject_t createObject = (createObject_t) dlsym(m_handleStreamBridge, "createStreamBridgeObject");
        deleteObject_t deleteObject = (deleteObject_t) dlsym(m_handleStreamBridge, "deleteStreamBridgeObject");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbols of streambridge library" << dlsym_error << endl;
            dlclose(m_handleStreamBridge);
            m_handleStreamBridge = nullptr;
            return -1;
        }
        m_streamBridge = createObject();
        if (m_streamBridge)
        {
            m_streamBridge->m_deleteObject = deleteObject;
        }
        else
        {
            LOG(error) << "Failed to start StreamBridge module" << endl;
            return -1;
        }
        LOG(info) << "Loaded StreamBridge module: " << createObject << endl;
    }
    return ret;
}

int ModuleLoader::loadSensorManagementLib()
{
    int ret = 0;
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvsensormanagement.so");
    m_handleSensorManagement = dlopen(lib_path, RTLD_LAZY);
    if (!m_handleSensorManagement)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libnvsensormanagement.so");
        m_handleSensorManagement = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libnvsensormanagement.so");
    m_handleSensorManagement = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!m_handleSensorManagement)
    {
        LOG(error) << "Cannot open sensor management library: " << dlerror() << endl;
        ret = -1;
    }
    else
    {
        dlerror();
        createObject_t createObject = (createObject_t) dlsym(m_handleSensorManagement, "createSensorManagementObject");
        deleteObject_t deleteObject = (deleteObject_t) dlsym(m_handleSensorManagement, "deleteSensorManagementObject");
        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbols of sensor management library" << dlsym_error << endl;
            dlclose(m_handleSensorManagement);
            m_handleSensorManagement = nullptr;
            return -1;
        }
        m_sensorManagement = createObject();
        if (m_sensorManagement)
        {
            m_sensorManagement->m_deleteObject = deleteObject;
        }
        else
        {
            LOG(error) << "Failed to start sensor management module" << endl;
            return -1;
        }
        LOG(info) << "Loaded sensor management module: " << createObject << endl;
    }
    return ret;
}

int ModuleLoader::loadMediaAdaptor()
{
    unloadMediaAdaptor();

    if (m_mediaAdaptorLibPath.empty())
    {
        LOG(info) << "No media adaptor configured for storage management" << endl;
        return 0;
    }

    LOG(info) << "Loading media adaptor library: " << m_mediaAdaptorLibPath << endl;

    nv_vms::MediaAdaptorLoader::MediaAdaptorHandle handle;
    int ret = nv_vms::MediaAdaptorLoader::load(m_mediaAdaptorLibPath, handle);
    if (ret != 0 || handle.instance == nullptr || handle.destroy == nullptr)
    {
        LOG(error) << "Failed to load media adaptor library: " << m_mediaAdaptorLibPath << endl;
        if (handle.libraryHandle != nullptr)
        {
            dlclose(handle.libraryHandle);
        }
        return -1;
    }

    LOG(info) << "Loaded media adaptor library: " << m_mediaAdaptorLibPath << endl;
    m_mediaAdaptorHandle = handle;

    return 0;
}

void ModuleLoader::unloadMediaAdaptor()
{
    if (m_mediaAdaptorHandle.instance != nullptr && m_mediaAdaptorHandle.destroy != nullptr)
    {
        m_mediaAdaptorHandle.destroy(m_mediaAdaptorHandle.instance);
        m_mediaAdaptorHandle.instance = nullptr;
    }

    if (m_mediaAdaptorHandle.libraryHandle != nullptr)
    {
        dlclose(m_mediaAdaptorHandle.libraryHandle);
        m_mediaAdaptorHandle.libraryHandle = nullptr;
    }

    m_mediaAdaptorHandle = {};
}

void ModuleLoader::deInitialize()
{
    if(m_handlePeerConnectionManager)
    {
        m_peerConnectionManager->m_deleteObject(m_peerConnectionManager);
        dlclose(m_handlePeerConnectionManager);
        m_handlePeerConnectionManager = nullptr;
    }
    m_peerConnectionManager = nullptr;

    if(m_handlePeerConnectionLiveManager)
    {
        m_peerConnectionLiveManager->m_deleteObject(m_peerConnectionLiveManager);
        dlclose(m_handlePeerConnectionLiveManager);
        m_handlePeerConnectionLiveManager = nullptr;
    }
    m_peerConnectionLiveManager = nullptr;

    if(m_handlePeerConnectionReplayManager)
    {
        m_peerConnectionReplayManager->m_deleteObject(m_peerConnectionReplayManager);
        dlclose(m_handlePeerConnectionReplayManager);
        m_handlePeerConnectionReplayManager = nullptr;
    }
    m_peerConnectionReplayManager = nullptr;

    if(m_handleStreamBridge)
    {
        m_streamBridge->m_deleteObject(m_streamBridge);
        dlclose(m_handleStreamBridge);
        m_handleStreamBridge = nullptr;
    }
    m_streamBridge = nullptr;

    if(m_handleStreamRecorder)
    {
        m_streamRecorder->m_deleteObject(m_streamRecorder);
        dlclose(m_handleStreamRecorder);
        m_handleStreamRecorder = nullptr;
    }
    m_streamRecorder = nullptr;

    if(m_handleRtspServer)
    {
        m_rtspServer->m_deleteObject(m_rtspServer);
        dlclose(m_handleRtspServer);
        m_handleRtspServer = nullptr;
    }
    m_rtspServer = nullptr;

    if(m_handleStorageManagement)
    {
        m_storageManagement->m_deleteObject(m_storageManagement);
        dlclose(m_handleStorageManagement);
        m_handleStorageManagement = nullptr;
    }
    m_storageManagement = nullptr;

    if(m_handleSensorManagement)
    {
        m_sensorManagement->m_deleteObject(m_sensorManagement);
        dlclose(m_handleSensorManagement);
        m_handleSensorManagement = nullptr;
    }
    m_sensorManagement = nullptr;

    unloadMediaAdaptor();
}

ModuleLoader::~ModuleLoader()
{
    LOG(info) << "Deleting ModuleLoader instance" << endl;
}