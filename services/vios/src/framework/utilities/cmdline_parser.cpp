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

#include "cmdline_parser.h"

#include <curl/curl.h>
#include <sstream>
#include <iostream>
#include <iomanip>
#include <string.h>

using namespace std;
using namespace nv_vms;

/* Null, because instance will be initialized on demand. */
CmdLineParser* CmdLineParser::m_instance = 0;

CmdLineParser* CmdLineParser::getInstance()
{
    if (m_instance == 0)
    {
        m_instance = new CmdLineParser();
    }

    return m_instance;
}

static void
print_help(void)
{
    cerr << "\n./launch_mms"
            "\n\t--vstConfigFile <path_to_vst_config.json>"
            "\n\t--adaptorConfigFile <path_to_adaptor_config.json>"
            "\n\t--configFile <path_to_onvif_camera_list.json>"
            "\n\t--rtspStreamsFile <path_to_rtsp_streams.json>"
            "\n\t--debug-level 1 - 5"
            "\n\t  1 - error"
            "\n\t  2 - warning"
            "\n\t  3 - info"
            "\n\t  4 - verbose"
            "\n\t  5 - more verbose"
            "\n\t--log-to-file - To enable file logging\n";
}

int CmdLineParser::parseCommandLine(int argc, char *argv[])
{
    char **argp = argv;
    char *arg = *(++argp);

    LOG(verbose) << "Parsing Command Line" << endl;

    if ((arg && (!strcmp(arg, "-h") || !strcmp(arg, "--help"))))
    {
       print_help();
       exit(EXIT_SUCCESS);
    }
    
    nv_logger::Logger *l = nv_logger::Logger::getInstance();
    
    while ((arg = *(argp)))
    {
        if (!strcmp(arg, "-h") || !strcmp(arg, "--help"))
        {
           print_help();
           exit(EXIT_SUCCESS);
        }
        else if (!strncmp(arg, "--vstConfigFile", 12))
        {
            argp++;
            m_vmsConfigFilePath = string(*argp);
            argp++;
        }
        else if (!strncmp(arg, "--adaptorConfigFile", 12))
        {
            argp++;
            m_adaptorConfigFilePath = string(*argp);
            argp++;
        }
        else if (!strncmp(arg, "--onvifFile", 12))
        {
            argp++;
            m_onvifFilePath = string(*argp);
            argp++;
        }
        else if (!strncmp(arg, "--rtspStreamsFile", 16))
        {
            argp++;
            m_rtspStreamsFilePath = string(*argp);
            argp++;
        }
        else if (!strncmp(arg, "--debug-level", 13))
        {
            argp++;
            l->setLevel(stoi(*argp));
            argp++;
        }
        else if (!strncmp(arg, "--log-to-file", 13))
        {
            argp++;
            string path = string(*argp);
            l->setFileLogging(true, path);
            argp++;
        }
        else
        {
           cout << "Unknown command line argument : " << arg << endl;
           exit(EXIT_SUCCESS);
        }
    }
    return 0;
}
