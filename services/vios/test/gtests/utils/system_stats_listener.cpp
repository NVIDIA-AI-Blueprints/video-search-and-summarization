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

#include "system_stats_listener.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <sys/resource.h>
#include <unistd.h>

// ---------------------------------------------------------------------------
// SystemStatsCollector
// ---------------------------------------------------------------------------

SystemStatsSnapshot SystemStatsCollector::takeSnapshot()
{
    SystemStatsSnapshot snap;
    snap.timestamp = std::chrono::steady_clock::now();
    readCpuStats(snap);
    readTopCpuStats(snap);
    readMemoryStats(snap);
    readGpuStats(snap);
    readIOStats(snap);
    return snap;
}

void SystemStatsCollector::readCpuStats(SystemStatsSnapshot& snap)
{
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage) == 0)
    {
        snap.user_time_us   = static_cast<int64_t>(usage.ru_utime.tv_sec) * 1000000 + usage.ru_utime.tv_usec;
        snap.system_time_us = static_cast<int64_t>(usage.ru_stime.tv_sec) * 1000000 + usage.ru_stime.tv_usec;
    }
}

void SystemStatsCollector::readTopCpuStats(SystemStatsSnapshot& snap)
{
    pid_t pid = getpid();
    char cmd[128];
    snprintf(cmd, sizeof(cmd), "top -bn1 -p %d 2>/dev/null | tail -1", static_cast<int>(pid));

    FILE* pipe = popen(cmd, "r");
    if (!pipe)
        return;

    char buf[512];
    if (fgets(buf, sizeof(buf), pipe))
    {
        // top output columns: PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND
        // %CPU is the 9th field (1-indexed)
        std::istringstream iss(buf);
        std::string token;
        int col = 0;
        while (iss >> token)
        {
            ++col;
            if (col == 9)
            {
                try
                {
                    snap.top_cpu_pct = std::stod(token);
                    snap.top_available = true;
                }
                catch (...) {}
                break;
            }
        }
    }
    pclose(pipe);
}

void SystemStatsCollector::readMemoryStats(SystemStatsSnapshot& snap)
{
    std::ifstream status("/proc/self/status");
    if (!status.is_open())
        return;

    std::string line;
    while (std::getline(status, line))
    {
        if (line.compare(0, 6, "VmRSS:") == 0)
        {
            std::istringstream iss(line.substr(6));
            iss >> snap.vm_rss_kb;
        }
        else if (line.compare(0, 7, "VmSize:") == 0)
        {
            std::istringstream iss(line.substr(7));
            iss >> snap.vm_size_kb;
        }
    }
}

void SystemStatsCollector::readGpuStats(SystemStatsSnapshot& snap)
{
    FILE* pipe = popen(
        "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total "
        "--format=csv,noheader,nounits 2>/dev/null",
        "r");
    if (!pipe)
        return;

    char buf[256];
    if (fgets(buf, sizeof(buf), pipe))
    {
        int util = 0, memUsed = 0, memTotal = 0;
        if (sscanf(buf, "%d, %d, %d", &util, &memUsed, &memTotal) == 3)
        {
            snap.gpu_available    = true;
            snap.gpu_util_pct     = util;
            snap.gpu_mem_used_mib = memUsed;
            snap.gpu_mem_total_mib = memTotal;
        }
    }
    pclose(pipe);
}

void SystemStatsCollector::readIOStats(SystemStatsSnapshot& snap)
{
    std::ifstream io("/proc/self/io");
    if (!io.is_open())
        return;

    int parsed = 0;
    std::string line;
    while (std::getline(io, line))
    {
        // /proc/self/io fields are unique prefixes; use exact key matching
        // to avoid any ambiguity (e.g. "read_bytes:" vs "rchar:").
        std::string::size_type colon = line.find(':');
        if (colon == std::string::npos)
            continue;

        std::string key = line.substr(0, colon);
        std::istringstream iss(line.substr(colon + 1));

        if (key == "syscr")
        {
            iss >> snap.read_syscalls;
            ++parsed;
        }
        else if (key == "syscw")
        {
            iss >> snap.write_syscalls;
            ++parsed;
        }
        else if (key == "read_bytes")
        {
            iss >> snap.read_bytes;
            ++parsed;
        }
        else if (key == "write_bytes")
        {
            iss >> snap.write_bytes;
            ++parsed;
        }
    }

    snap.io_available = (parsed > 0);
}

// ---------------------------------------------------------------------------
// SystemStatsTestListener
// ---------------------------------------------------------------------------

static const char* const RESULTS_CSV_PATH = "test_results.csv";
static const char* const STATS_CSV_PATH   = "system_stats.csv";

SystemStatsTestListener::SystemStatsTestListener(bool csvEnabled)
    : m_csvEnabled(csvEnabled) {}

void SystemStatsTestListener::OnTestProgramStart(const ::testing::UnitTest& /*unit_test*/)
{
    if (!m_csvEnabled)
        return;

    m_resultsCsv.open(RESULTS_CSV_PATH, std::ios::out | std::ios::trunc);
    if (m_resultsCsv.is_open())
    {
        m_resultsCsv << "TestSuite,TestName,Status,Duration_s,FailureMessage" << std::endl;
        std::cout << "[STATS] CSV: " << RESULTS_CSV_PATH << " opened" << std::endl;
    }

    m_statsCsv.open(STATS_CSV_PATH, std::ios::out | std::ios::trunc);
    if (m_statsCsv.is_open())
    {
        m_statsCsv << "TestName,WallTime_s,"
                   << "CPU_User_s,CPU_System_s,CPU_Avg_Pct,CPU_Top_Pct,"
                   << "RSS_Before_KB,RSS_After_KB,RSS_Delta_KB,"
                   << "VmSize_Before_KB,VmSize_After_KB,VmSize_Delta_KB,"
                   << "IO_Read_Bytes,IO_Write_Bytes,IO_Read_Syscalls,IO_Write_Syscalls,"
                   << "GPU_Util_Before_Pct,GPU_Util_After_Pct,"
                   << "GPU_Mem_Before_MiB,GPU_Mem_After_MiB,GPU_Mem_Total_MiB"
                   << std::endl;
        std::cout << "[STATS] CSV: " << STATS_CSV_PATH << " opened" << std::endl;
    }
}

void SystemStatsTestListener::OnTestProgramEnd(const ::testing::UnitTest& /*unit_test*/)
{
    if (m_resultsCsv.is_open())
    {
        m_resultsCsv.close();
        std::cout << "[STATS] CSV: " << RESULTS_CSV_PATH << " written" << std::endl;
    }
    if (m_statsCsv.is_open())
    {
        m_statsCsv.close();
        std::cout << "[STATS] CSV: " << STATS_CSV_PATH << " written" << std::endl;
    }
}

void SystemStatsTestListener::OnTestStart(const ::testing::TestInfo& /*test_info*/)
{
    m_before = m_collector.takeSnapshot();
}

void SystemStatsTestListener::OnTestEnd(const ::testing::TestInfo& test_info)
{
    SystemStatsSnapshot after = m_collector.takeSnapshot();

    using namespace std::chrono;
    double wall_sec = duration<double>(after.timestamp - m_before.timestamp).count();

    std::string name = std::string(test_info.test_suite_name()) + "." + test_info.name();
    printReport(name, m_before, after);
    writeResultsCsvRow(test_info, wall_sec);
    writeStatsCsvRow(name, m_before, after, wall_sec);
}

// ---------------------------------------------------------------------------
// CSV output
// ---------------------------------------------------------------------------

static std::string escapeCsvField(const std::string& field)
{
    if (field.find_first_of(",\"\n") == std::string::npos)
        return field;
    std::string escaped = "\"";
    for (char c : field)
    {
        if (c == '"') escaped += '"';
        escaped += c;
    }
    escaped += '"';
    return escaped;
}

void SystemStatsTestListener::writeResultsCsvRow(const ::testing::TestInfo& test_info, double wall_sec)
{
    if (!m_resultsCsv.is_open())
        return;

    const char* status = test_info.result()->Passed()  ? "PASSED" :
                         test_info.result()->Skipped() ? "SKIPPED" : "FAILED";

    std::string failure_msg;
    for (int i = 0; i < test_info.result()->total_part_count(); ++i)
    {
        const ::testing::TestPartResult& part = test_info.result()->GetTestPartResult(i);
        if (part.failed())
        {
            if (!failure_msg.empty()) failure_msg += " | ";
            failure_msg += part.summary();
        }
    }

    m_resultsCsv << escapeCsvField(test_info.test_suite_name()) << ","
                 << escapeCsvField(test_info.name()) << ","
                 << status << ","
                 << std::fixed << std::setprecision(3) << wall_sec << ","
                 << escapeCsvField(failure_msg)
                 << std::endl;
}

void SystemStatsTestListener::writeStatsCsvRow(const std::string& test_name,
                                               const SystemStatsSnapshot& before,
                                               const SystemStatsSnapshot& after,
                                               double wall_sec)
{
    if (!m_statsCsv.is_open())
        return;

    double user_sec = (after.user_time_us - before.user_time_us) / 1e6;
    double sys_sec  = (after.system_time_us - before.system_time_us) / 1e6;
    double avg_cpu  = (wall_sec > 0.0) ? ((user_sec + sys_sec) / wall_sec * 100.0) : 0.0;
    double top_cpu  = after.top_available ? after.top_cpu_pct : -1.0;

    int64_t rss_delta  = after.vm_rss_kb - before.vm_rss_kb;
    int64_t vmsz_delta = after.vm_size_kb - before.vm_size_kb;

    int64_t io_rd  = (before.io_available && after.io_available) ? (after.read_bytes - before.read_bytes) : -1;
    int64_t io_wr  = (before.io_available && after.io_available) ? (after.write_bytes - before.write_bytes) : -1;
    int64_t io_rsc = (before.io_available && after.io_available) ? (after.read_syscalls - before.read_syscalls) : -1;
    int64_t io_wsc = (before.io_available && after.io_available) ? (after.write_syscalls - before.write_syscalls) : -1;

    int gpu_util_b = (before.gpu_available) ? before.gpu_util_pct : -1;
    int gpu_util_a = (after.gpu_available)  ? after.gpu_util_pct  : -1;
    int gpu_mem_b  = (before.gpu_available) ? before.gpu_mem_used_mib : -1;
    int gpu_mem_a  = (after.gpu_available)  ? after.gpu_mem_used_mib  : -1;
    int gpu_mem_t  = (after.gpu_available)  ? after.gpu_mem_total_mib : -1;

    m_statsCsv << escapeCsvField(test_name) << ","
               << std::fixed << std::setprecision(3) << wall_sec << ","
               << std::fixed << std::setprecision(3) << user_sec << ","
               << std::fixed << std::setprecision(3) << sys_sec << ","
               << std::fixed << std::setprecision(1) << avg_cpu << ","
               << std::fixed << std::setprecision(1) << top_cpu << ","
               << before.vm_rss_kb << "," << after.vm_rss_kb << "," << rss_delta << ","
               << before.vm_size_kb << "," << after.vm_size_kb << "," << vmsz_delta << ","
               << io_rd << "," << io_wr << "," << io_rsc << "," << io_wsc << ","
               << gpu_util_b << "," << gpu_util_a << ","
               << gpu_mem_b << "," << gpu_mem_a << "," << gpu_mem_t
               << std::endl;
}

// ---------------------------------------------------------------------------
// Report formatting helpers
// ---------------------------------------------------------------------------

static std::string formatBytes(int64_t bytes)
{
    std::ostringstream oss;
    if (bytes >= 1048576)
        oss << std::fixed << std::setprecision(1) << (bytes / 1048576.0) << " MB";
    else if (bytes >= 1024)
        oss << std::fixed << std::setprecision(1) << (bytes / 1024.0) << " KB";
    else
        oss << bytes << " B";
    return oss.str();
}

static std::string formatKbAsMb(int64_t kb)
{
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(1) << (kb / 1024.0) << " MB";
    return oss.str();
}

static std::string formatDeltaKb(int64_t delta_kb)
{
    std::ostringstream oss;
    oss << (delta_kb >= 0 ? "+" : "") << std::fixed << std::setprecision(1) << (delta_kb / 1024.0) << " MB";
    return oss.str();
}

void SystemStatsTestListener::printReport(const std::string& test_name,
                                          const SystemStatsSnapshot& before,
                                          const SystemStatsSnapshot& after)
{
    using namespace std::chrono;
    double wall_sec = duration<double>(after.timestamp - before.timestamp).count();
    double user_sec = (after.user_time_us - before.user_time_us) / 1e6;
    double sys_sec  = (after.system_time_us - before.system_time_us) / 1e6;

    std::cout << "\n  [STATS] ----- System Stats: " << test_name << " -----" << std::endl;

    std::cout << "  [STATS]   Wall time:      " << std::fixed << std::setprecision(3) << wall_sec << " s" << std::endl;

    double avg_cpu_pct = (wall_sec > 0.0) ? ((user_sec + sys_sec) / wall_sec * 100.0) : 0.0;

    std::cout << "  [STATS]   CPU user:       " << std::fixed << std::setprecision(3) << user_sec
              << " s   |  CPU system:    " << std::fixed << std::setprecision(3) << sys_sec << " s" << std::endl;
    std::cout << "  [STATS]   CPU avg usage:  " << std::fixed << std::setprecision(1) << avg_cpu_pct << "%" << std::endl;

    if (after.top_available)
    {
        std::cout << "  [STATS]   CPU (top):      " << std::fixed << std::setprecision(1)
                  << after.top_cpu_pct << "%" << std::endl;
    }

    int64_t rss_delta  = after.vm_rss_kb - before.vm_rss_kb;
    int64_t vmsz_delta = after.vm_size_kb - before.vm_size_kb;
    std::cout << "  [STATS]   Memory RSS:     " << formatKbAsMb(before.vm_rss_kb)
              << " (before) -> " << formatKbAsMb(after.vm_rss_kb)
              << " (after)  [" << formatDeltaKb(rss_delta) << "]" << std::endl;
    std::cout << "  [STATS]   Memory VmSize:  " << formatKbAsMb(before.vm_size_kb)
              << " (before) -> " << formatKbAsMb(after.vm_size_kb)
              << " (after)  [" << formatDeltaKb(vmsz_delta) << "]" << std::endl;

    if (before.io_available && after.io_available)
    {
        int64_t rd = after.read_bytes - before.read_bytes;
        int64_t wr = after.write_bytes - before.write_bytes;
        int64_t rsc = after.read_syscalls - before.read_syscalls;
        int64_t wsc = after.write_syscalls - before.write_syscalls;
        std::cout << "  [STATS]   I/O read:       " << formatBytes(rd) << " (" << rsc << " syscalls)" << std::endl;
        std::cout << "  [STATS]   I/O write:      " << formatBytes(wr) << " (" << wsc << " syscalls)" << std::endl;
    }
    else
    {
        std::cout << "  [STATS]   I/O:            N/A (/proc/self/io not accessible)" << std::endl;
    }

    if (before.gpu_available && after.gpu_available)
    {
        int mem_delta = after.gpu_mem_used_mib - before.gpu_mem_used_mib;
        std::ostringstream delta_str;
        delta_str << (mem_delta >= 0 ? "+" : "") << mem_delta << " MiB";

        std::cout << "  [STATS]   GPU util:       " << before.gpu_util_pct
                  << "% (before) -> " << after.gpu_util_pct << "% (after)" << std::endl;
        std::cout << "  [STATS]   GPU memory:     " << before.gpu_mem_used_mib << "/" << before.gpu_mem_total_mib
                  << " MiB (before) -> " << after.gpu_mem_used_mib << "/" << after.gpu_mem_total_mib
                  << " MiB (after)  [" << delta_str.str() << "]" << std::endl;
    }
    else
    {
        std::cout << "  [STATS]   GPU:            N/A (nvidia-smi not available)" << std::endl;
    }

    std::cout << "  [STATS] -----------------------------------------------------------\n" << std::endl;
}
