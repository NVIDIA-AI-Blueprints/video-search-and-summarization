/*
 * SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
/*this class is for measuring wall clock time not cpu time*/
#include <chrono>
#include <iostream>
#include <bits/stdc++.h>

#define PROFILER_OUT_FILE "vms_function_profiler_result.csv"
#ifdef FUNCTION_PROFILER
#ifndef MEASURE_FUNCTION_EXECUTION_TIME
#define MEASURE_FUNCTION_EXECUTION_TIME \
                    const nv_vms::MeasureExecutionTime \
                    measureExecutionTime(__PRETTY_FUNCTION__, __LINE__);

#define MEASURE_FUNCTION_EXECUTION_TIME_WITH_TAG(tag) \
                    const nv_vms::MeasureExecutionTime \
                    measureExecutionTime(__PRETTY_FUNCTION__, __LINE__, tag);

#endif
#ifndef DUMP_FUNCTION_PROFILER_RESULT
#define DUMP_FUNCTION_PROFILER_RESULT \
                    nv_vms::MeasureExecutionTime::writeToFile();
#endif
#else
#define MEASURE_FUNCTION_EXECUTION_TIME
#define MEASURE_FUNCTION_EXECUTION_TIME_WITH_TAG(tag)
#define DUMP_FUNCTION_PROFILER_RESULT
#endif

namespace nv_vms
{

class MeasureExecutionTime
{
private:
    int line_number;
    std::string tag;
    const std::chrono::steady_clock::time_point begin;
    std::string caller;
    static std::stringstream funcProfileResult;
    static bool only_once;
public:
    MeasureExecutionTime(const std::string& caller_, const int line_num): line_number(line_num)
                                                    , tag("")
                                                    , begin(std::chrono::steady_clock::now()) 
    {
        if (only_once)
        {
            funcProfileResult << "Funtion name" << "," << "process time (ms)" << "\n";
            only_once = false;
        }
        caller = std::string("\"") + caller_ + std::string("\"");
    }
    MeasureExecutionTime(const std::string& caller_, const int line_num, std::string in_tag): line_number(line_num)
                                                    , tag(in_tag)
                                                    , begin(std::chrono::steady_clock::now()) 
    {
        if (only_once)
        {
            funcProfileResult << "Funtion name" << "," << "process time (ms)" << "\n";
            only_once = false;
        }
        caller = std::string("\"") + caller_ + std::string("\"");
    }
    ~MeasureExecutionTime()
    {
        const auto duration=std::chrono::steady_clock::now() - begin;
        if (tag.empty())
        {
            funcProfileResult << caller <<"(" << line_number << ")" << "," <<std::chrono::duration_cast<std::chrono::microseconds>(duration).count() << "\n";
        }
        else
        {
            funcProfileResult << caller <<"(" << line_number << ")"<<"(" << tag << ")" << "," <<std::chrono::duration_cast<std::chrono::microseconds>(duration).count() << "\n";
        }
    }

    static void writeToFile()
    {
        static bool only_once = true;
        if (only_once)
        {
            std::ofstream func_time_file (PROFILER_OUT_FILE);
            func_time_file << funcProfileResult.rdbuf();
            func_time_file.close();
            only_once = false;
        }
        funcProfileResult.seekg(0, std::ios::beg);
        std::string line;
        while(std::getline(funcProfileResult, line))
        {
            std::cout << line << std::endl;
        }
    }
};

} //nv_vms

