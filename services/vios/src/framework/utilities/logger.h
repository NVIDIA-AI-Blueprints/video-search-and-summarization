/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <cstdio>
#include <cstdarg>
#include <iostream>
#include <fstream>
#include <stdarg.h>
#include <thread>
#include <mutex>
#include <sstream>
#include <streambuf>
#include <atomic>
#include <string.h>

#include "utils.h"
#include "config.h"

#define __FILENAME__ (strrchr(__FILE__, '/') ? strrchr(__FILE__, '/') + 1 : __FILE__)
#define LOG(x) nv_logger::Logger::getInstance()->log(nv_logger::x, __FUNCTION__, __FILENAME__, __LINE__)
#define LOG2(x) nv_logger::Logger::getInstance()->log(nv_logger::x)
#define LOG_QOS(...) nv_logger::Logger::getInstance()->log_qos(__VA_ARGS__)
#define LOG_COLOR(x, ...) nv_logger::Logger::getInstance()->log_color(x, __VA_ARGS__)
#ifndef RELEASE
#define GET_LOG nv_logger::Logger::getInstance
#endif 
#define ENABLE_LOG
#define VA_ARG_MAX_BUFFER_LENGTH 1024*4

const std::string red("\033[0;31m");
const std::string yellow("\033[0;33m");
const std::string blue("\033[0;34m");
const std::string magenta("\033[0;35m");
const std::string reset("\033[0m");
const std::string none("");

using namespace std;

namespace nv_logger {

enum Level {
    error = 1,
    warning,
    info,
    verbose,
    verbose2
};

struct CoutToString
{
    CoutToString( std::streambuf * new_buffer ) 
        : old( std::cout.rdbuf( new_buffer ) )
    { }

    ~CoutToString( )
    {
        std::cout.rdbuf( old );
    }

private:
    std::streambuf * old;
};

class Logger {
    private:
        /* Retained for ABI compatibility with prebuilt shared libraries */
        static Logger* m_instance;

        /* Private constructor to prevent instancing. */
        Logger ()
        : m_userDebugLevel(info)
        , m_enableFileLog (false)
        , m_redirect(nullptr)
        {
        }

        ~Logger ()
        {
            if (m_redirect)
            {
                delete m_redirect;
                m_redirect = nullptr;
            }
            m_stringBuffer.clear();
            m_fileStream.close ();
            m_qosStream.close();
        }

        /* here ofstream instance will be stored */
        std::ofstream m_fileStream;

        /* here debug level of each log will be stored */
        std::atomic<Level> m_debugLevel {info};

        /* here user's debug level will be stored */
        std::atomic<Level> m_userDebugLevel;

        /* here user's file logging preference will be stored */
        bool m_enableFileLog;

        CoutToString *m_redirect;
        std::stringstream m_stringBuffer;
        std::mutex m_LogLock;

        /* QoS logging options */
        std::ofstream m_qosStream;

    public:
        /* get the static instance of Logger */
        static Logger* getInstance ();

        /* set user's preference of logging */
        void setLevel(int level);

        /* redirect cout to string */
        void setRedirect(bool flag);

        /* set user's preference of file logging */
        void setFileLogging(bool flag, std::string fileName);

        std::stringstream getLogStream();

        /* set QoS logging options */
        void setupQoSLogging();
#ifndef RELEASE
        /* record logs to send using websocket */
        std::stringstream m_logStream;

        template <typename T>
        void appendToLogStream(const T& value)
        {
            static const size_t max_size = 15000;
            if (static_cast<size_t>(m_logStream.tellp()) > max_size)
            {
                m_logStream.clear();
                m_logStream.str(std::string());
            }
            std::stringstream buf;
            buf << value;
            m_logStream << buf.str();
        }
#endif
        /* << operator overloaded to print or log in file */
        template <typename T>
        Logger& operator<<(const T& x)
        {
#ifdef ENABLE_LOG
            if (m_userDebugLevel >= m_debugLevel)
            {
                if (m_enableFileLog)
                {
                    m_fileStream << x;
                    m_fileStream.flush();
                }
                else if (m_debugLevel == error)
                {
                    if (GET_CONFIG().enable_highlighting_logs)
                    {
                        std::cout << red << x << reset;
                    }
                    else
                    {
                        std::cout << x;
                    }
                }
                else if (m_debugLevel == warning)
                {
                    if (GET_CONFIG().enable_highlighting_logs)
                    {
                        std::cout << yellow << x << reset;
                    }
                    else
                    {
                        std::cout << x;
                    }
                }
                else
                {
                    std::cout << x ;
                }
#ifndef RELEASE
                appendToLogStream(x);
#endif
            }
#endif
            return *this;
        }
        Logger& operator<<(std::ostream& (*os)(std::ostream&))
        {
#ifdef ENABLE_LOG
            if (m_userDebugLevel >= m_debugLevel)
            {
                if (m_enableFileLog)
                {
                    m_fileStream << os;
                    m_fileStream.flush();
                }
                else
                {
                    std::cout << os;
                }
#ifndef RELEASE
                appendToLogStream(os);
#endif
            }
#endif
            return *this;
        }

        Logger& log(Level n, const string functionName, const string fileName, const int lineNumber )
        {
#ifdef ENABLE_LOG
            m_debugLevel = n;
            if (m_userDebugLevel >= m_debugLevel)
            {
                std::stringstream buf;
                buf << "[";
                buf << getCurrentTimeMS() << ":";
                buf << std::this_thread::get_id() << ":";
                buf << fileName << ":";
                buf << lineNumber << ": ";
                buf << functionName << "]\t";

                if (m_enableFileLog )
                {
                    m_fileStream << buf.str();
                }
                else
                {
                    std::cout << buf.str();
                }
#ifndef RELEASE
                m_logStream << buf.str();
#endif
            }
#endif
            return *this;
        }

        Logger& log(Level n)
        {
            m_debugLevel = n;
            return *this;
        }
#ifndef RELEASE
        std::string getLogs() const
        {
            return m_logStream.str();
        }
#endif
        void log_qos(string format, ...)
        {
            va_list args;
            const int max_len = VA_ARG_MAX_BUFFER_LENGTH;
            char buffer[max_len] = { 0 };

            // retrieve the variable arguments
            va_start(args, format.c_str());
            
            // Safe formatted output with bounds checking and null termination
            int written = vsnprintf(buffer, max_len, format.c_str(), args);
            
            // Ensure null termination and handle potential truncation
            if (written >= max_len)
            {
                buffer[max_len - 1] = '\0';  // Ensure null termination
            }
            else if (written < 0)
            {
                buffer[0] = '\0';  // Handle encoding error
                LOG(error) << "Error in vsnprintf" << endl;
            }

            m_qosStream << buffer;
            m_qosStream.flush();

            va_end(args);
        }

        void log_color(string color, string format, ...)
        {
            va_list args;
            const int max_len = VA_ARG_MAX_BUFFER_LENGTH;
            char buffer[max_len] = { 0 };

            // retrieve the variable arguments
            va_start(args, format.c_str());
            
            // Safe formatted output with bounds checking and null termination
            int written = vsnprintf(buffer, max_len, format.c_str(), args);
            
            // Ensure null termination and handle potential truncation
            if (written >= max_len)
            {
                buffer[max_len - 1] = '\0';  // Ensure null termination
            }
            else if (written < 0)
            {
                buffer[0] = '\0';  // Handle encoding error
                LOG(error) << "Error in vsnprintf" << endl;
            }

            if (m_enableFileLog)
            {
                m_fileStream << buffer;
                m_fileStream.flush();
            }
            else
            {
                if (GET_CONFIG().enable_highlighting_logs)
                {
                    std::cout << color << buffer << reset << endl;
                }
                else
                {
                    std::cout << buffer << endl;
                }
            }
            va_end(args);
        }
};
} // nv_logger
