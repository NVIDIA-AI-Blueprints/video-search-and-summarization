/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <vector>
#include <jsoncpp/json/json.h>
#include "logger.h"
#include "config.h"

using namespace std;

#define GET_CMDLINE_PARSER nv_vms::CmdLineParser::getInstance
namespace nv_vms {
class CmdLineParser
{
    private:
        /* Here will be the instance stored. */
        static CmdLineParser* m_instance;
        std::string m_adaptorConfigFilePath;
        std::string m_vmsConfigFilePath;
        std::string m_onvifFilePath;
        std::string m_deviceDetailsFilePath;
        std::string m_cameraBackListFilePath;
        std::string m_rtspStreamsFilePath;

        /* Private constructor to prevent instancing. */
        CmdLineParser () : m_adaptorConfigFilePath(ADAPTOR_CONFIG_FILE)
                         , m_vmsConfigFilePath(VMS_CONFIG_FILE)
                         , m_onvifFilePath(ONVIF_CAMERA_LIST_FILE)
                         , m_deviceDetailsFilePath(DEVICE_DETAILS_FILE)
                         , m_cameraBackListFilePath(CAMERA_BLACK_LIST_FILE)
                         , m_rtspStreamsFilePath(RTSP_STREAMS_FILE)
        {
        }
        ~CmdLineParser () {}

    public:
        int parseCommandLine (int argc, char *argv[]);
        static CmdLineParser* getInstance ();
        std::string getVmsConfigFilePath() { return m_vmsConfigFilePath; }
        std::string getCameraBackListFilePath() { return m_cameraBackListFilePath; }
        std::string getAdaptorConfigFilePath() { return m_adaptorConfigFilePath; }
        std::string getOnvifFilePath() { return m_onvifFilePath; }
        std::string getDeviceDetailsFilePath() { return m_deviceDetailsFilePath; }
        std::string getRtspStreamsFilePath() { return m_rtspStreamsFilePath; }
};

} //nv_vms
