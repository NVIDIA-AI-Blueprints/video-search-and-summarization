/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#ifndef SYSTEM_STATS_LISTENER_H
#define SYSTEM_STATS_LISTENER_H

#include "gtest/gtest.h"
#include <chrono>
#include <cstdint>
#include <fstream>
#include <string>

struct SystemStatsSnapshot
{
    std::chrono::steady_clock::time_point timestamp;

    // CPU (from getrusage)
    int64_t user_time_us   = 0;
    int64_t system_time_us = 0;

    // CPU (from top)
    bool   top_available = false;
    double top_cpu_pct   = 0.0;

    // Memory (from /proc/self/status)
    int64_t vm_rss_kb  = 0;
    int64_t vm_size_kb = 0;

    // GPU (from nvidia-smi)
    bool gpu_available    = false;
    int  gpu_util_pct     = 0;
    int  gpu_mem_used_mib = 0;
    int  gpu_mem_total_mib = 0;

    // I/O (from /proc/self/io)
    bool    io_available  = false;
    int64_t read_bytes    = 0;
    int64_t write_bytes   = 0;
    int64_t read_syscalls = 0;
    int64_t write_syscalls = 0;
};

class SystemStatsCollector
{
public:
    SystemStatsSnapshot takeSnapshot();

private:
    void readCpuStats(SystemStatsSnapshot& snap);
    void readTopCpuStats(SystemStatsSnapshot& snap);
    void readMemoryStats(SystemStatsSnapshot& snap);
    void readGpuStats(SystemStatsSnapshot& snap);
    void readIOStats(SystemStatsSnapshot& snap);
};

/**
 * GTest event listener that captures system stats before/after each test
 * and prints a per-test report.
 */
class SystemStatsTestListener : public ::testing::EmptyTestEventListener
{
public:
    explicit SystemStatsTestListener(bool csvEnabled = false);

    void OnTestProgramStart(const ::testing::UnitTest& unit_test) override;
    void OnTestProgramEnd(const ::testing::UnitTest& unit_test) override;
    void OnTestStart(const ::testing::TestInfo& test_info) override;
    void OnTestEnd(const ::testing::TestInfo& test_info) override;

private:
    void printReport(const std::string& test_name,
                     const SystemStatsSnapshot& before,
                     const SystemStatsSnapshot& after);
    void writeResultsCsvRow(const ::testing::TestInfo& test_info, double wall_sec);
    void writeStatsCsvRow(const std::string& test_name,
                          const SystemStatsSnapshot& before,
                          const SystemStatsSnapshot& after,
                          double wall_sec);

    bool                 m_csvEnabled;
    SystemStatsCollector m_collector;
    SystemStatsSnapshot  m_before;
    std::ofstream        m_resultsCsv;
    std::ofstream        m_statsCsv;
};

#endif // SYSTEM_STATS_LISTENER_H
