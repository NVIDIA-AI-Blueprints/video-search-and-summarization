/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <memory>
#include "HttpServerRequestHandler.h"
#include "adaptor_loader.h"
#include "media_adaptor_loader.h"
#include "vms_media_interface.h"

using namespace std;

class IVstModule;

typedef IVstModule* (*createObject_t) (void);
typedef void (*deleteObject_t) (IVstModule*);

class IVstModule
{
    public:
        createObject_t m_createObject;
        deleteObject_t m_deleteObject;
        IVstModule* m_module;
        std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>>  m_func;
        nv_vms::IMediaInterface* m_mediaInterface = nullptr;

        virtual const std::map<std::string,HttpServerRequestHandler::httpFunction, std::less<>> getHttpApi() { return m_func; };
        virtual void postInit() {}
        virtual ~IVstModule() {}
};

class ModuleLoader
{
    public:
        static ModuleLoader* getInstance()
        {
            static ModuleLoader instance;
            return &instance;
        }
        ModuleLoader();
        ~ModuleLoader();

        int initialize(ModuleId module_id = ModuleAll);
        void deInitialize();
        string getDeviceId();
        string getDeviceType();
        std::shared_ptr<DeviceManager> getDeviceManagerObject();
#ifdef UNIT_TEST
        /** For unit tests only: set DeviceManager so StreamMonitor/QosRtspClient get a valid instance. */
        void setDeviceManagerForTest(std::shared_ptr<DeviceManager> dm);
#endif
        ModuleId getModuleId(const std::string& moduleName);
        string getModuleIdAsString(ModuleId module_id);

        IVstModule* getRtspServerMgmtInstance()
        {
            return m_rtspServer;
        }
        IVstModule *getRecorderInstance()
        {
            return m_streamRecorder;
        }
        IVstModule *getStorageMngtInstance()
        {
            return m_storageManagement;
        }
        IVstModule *getSensorMngtInstance()
        {
            return m_sensorManagement;
        }
        IVstModule *getPeerConnectionManager()
        {
            return m_peerConnectionManager;
        }
        IVstModule *getPeerConnectionLiveInstance()
        {
            return m_peerConnectionLiveManager;
        }
        IVstModule *getPeerConnectionReplayInstance()
        {
            return m_peerConnectionReplayManager;
        }
        IVstModule *getStreamBridgeInstance()
        {
            return m_streamBridge;
        }

        nv_vms::IMediaInterface* getMediaInterface()
        {
            return m_mediaAdaptorHandle.instance;
        }

        void setMediaAdaptorLibPath(const std::string& path)
        {
            m_mediaAdaptorLibPath = path;
        }

        IVstModule* m_rtspServer = nullptr;
        IVstModule* m_streamRecorder = nullptr;
        IVstModule* m_storageManagement = nullptr;
        IVstModule* m_sensorManagement = nullptr;
        IVstModule* m_peerConnectionManager = nullptr;
        IVstModule* m_peerConnectionLiveManager = nullptr;
        IVstModule* m_peerConnectionReplayManager = nullptr;
        IVstModule* m_streamBridge = nullptr;
    private:
        int loadRtspServerLib();
        int loadStreamRecorderLib();
        int loadStorageManagementLib();
        int loadSensorManagementLib();
        int loadPeerConnectionManagerLib();
        int loadPeerConnectionLiveLib();
        int loadPeerConnectionReplayLib();
        int loadStreamBridgeLib();
        int loadMediaAdaptor();
        void unloadMediaAdaptor();

        void* m_handleRtspServer = nullptr;
        void* m_handleStreamRecorder = nullptr;
        void* m_handleStorageManagement = nullptr;
        void* m_handleSensorManagement = nullptr;
        void* m_handlePeerConnectionManager = nullptr;
        void* m_handlePeerConnectionLiveManager = nullptr;
        void* m_handlePeerConnectionReplayManager = nullptr;
        void* m_handleStreamBridge = nullptr;
        AdaptorLoader m_loader;
        std::shared_ptr<DeviceManager> m_deviceManager;
        nv_vms::MediaAdaptorLoader::MediaAdaptorHandle m_mediaAdaptorHandle{};
        std::string m_mediaAdaptorLibPath;
};
