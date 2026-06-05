/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "logger.h"
#include "config.h"
#include <sstream>
#include <iostream>
#include <string.h>
#include "profiler.h"

using namespace std;
using namespace nv_logger;
Logger* Logger::m_instance = nullptr;
stringstream nv_vms::MeasureExecutionTime::funcProfileResult;
bool nv_vms::MeasureExecutionTime::only_once = true;

Logger* Logger::getInstance()
{
    static Logger instance;
    m_instance = &instance;
    return m_instance;
}

void Logger::setLevel(int level)
{
    m_userDebugLevel = static_cast<Level>(level);
}

void Logger::setRedirect(bool flag)
{
    std::lock_guard<std::mutex> lock(m_LogLock);
    if (flag)
    {
        m_redirect = new CoutToString(m_stringBuffer.rdbuf());
    }
    else
    {
        if (m_redirect)
        {
            delete m_redirect;
            m_redirect = nullptr;
        }
    }
}

void Logger::setFileLogging(bool flag, std::string fileName)
{
    std::lock_guard<std::mutex> lock(m_LogLock);
    if (!flag)
    {
        if (m_fileStream.is_open())
        {
            m_fileStream.close();
        }
        m_enableFileLog = false;
        return;
    }
    if (m_fileStream.is_open())
    {
        m_fileStream.close();
    }
    m_fileStream.open(fileName, std::ofstream::out | std::ofstream::app);
    if (m_fileStream.fail())
    {
        m_enableFileLog = false;
        LOG(error) << "Failed to open log file: " << fileName << endl;
    }
    else
    {
        m_enableFileLog = true;
    }
}

std::stringstream Logger::getLogStream()
{
    std::lock_guard<std::mutex> lock(m_LogLock);
    std::stringstream ss(m_stringBuffer.str());
    return ss;
}

void Logger::setupQoSLogging()
{
    string qosLogfile;
    string fname = "vms_qos_" + getCurrentTime() + ".csv";

    qosLogfile = GET_CONFIG().qos_logfile_path + string("/") + fname;

    m_qosStream.open(qosLogfile, std::ofstream::out  | std::ofstream::app);
    if (m_qosStream.fail())
    {
        LOG(error) << "Failed to open log file" << endl;
    }
    LOG(info) << "QoS logging started at:" << qosLogfile << endl;
}