/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "SystemMonitoringTask.h"
#include <fstream>
#include <sstream>
#include <iostream>
#include <cstdlib>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <algorithm>
#include <cstring>
#include <thread>
#include <mutex>
#include <chrono>
#include <future>
#include <csignal>

using namespace std;

// Static member initialization
SystemMonitoringTask* SystemMonitoringTask::s_instance = nullptr;

SystemMonitoringTask::SystemMonitoringTask() {
    LOG(info) << "SystemMonitoringTask created" << endl;
}

SystemMonitoringTask::~SystemMonitoringTask() {
    try {
        LOG(info) << "SystemMonitoringTask destructor called" << endl;
        stop();
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~SystemMonitoringTask: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~SystemMonitoringTask" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void SystemMonitoringTask::start() {
    // Guard against multiple starts
    if (m_monitoringThread.joinable()) {
        LOG(warning) << "SystemMonitoringTask already running" << endl;
        return;
    }
    
    // Reset exit flag
    m_shouldExit = false;
    
    // Initialize container name
    m_containerName = getContainerName();
    
    // Set monitoring interval from config
    int intervalSec = GET_CONFIG().system_metric_interval_sec;
    if (intervalSec <= 0) intervalSec = 5; // Default fallback
    {
        std::unique_lock<std::mutex> lock(m_cvMutex);
        m_monitoringInterval = std::chrono::milliseconds(intervalSec * 1000);  // Write interval under lock
    }
    LOG(info) << "System monitoring interval set to " << intervalSec << " seconds" << endl;
    
    // Set up signal handling
    setupSignalHandling();
    
    // Start the monitoring thread
    m_monitoringThread = std::thread(&SystemMonitoringTask::monitoringLoop, this);
    
    LOG(info) << "SystemMonitoringTask started for container: " << m_containerName << endl;
}

void SystemMonitoringTask::stop() {
    if (!m_monitoringThread.joinable()) {
        return; // Already stopped
    }
    
    LOG(info) << "Stopping SystemMonitoringTask..." << endl;
    
    // Signal the thread to exit
    {
        std::lock_guard<std::mutex> lock(m_cvMutex);
        m_shouldExit = true;
    }
    m_cv.notify_all();
    
    // Wait for thread to finish with timeout
    if (m_monitoringThread.joinable()) {
        auto future = std::async(std::launch::async, [this]() {
            m_monitoringThread.join();
        });
        
        // 5 second timeout for graceful shutdown
        if (future.wait_for(std::chrono::seconds(5)) == std::future_status::timeout) {
            LOG(error) << "SystemMonitoringTask shutdown timeout, detaching thread to prevent resource leak" << endl;
            // Detach the thread to prevent owning a joinable thread and avoid resource leaks
            m_monitoringThread.detach();
        }
    }
    
    cleanupSignalHandling();
    LOG(info) << "SystemMonitoringTask stopped" << endl;
}

bool SystemMonitoringTask::isRunning() const {
    return m_monitoringThread.joinable() && !m_shouldExit;
}

void SystemMonitoringTask::setMonitoringInterval(int intervalMs) {
    {
        unique_lock<mutex> lock(m_cvMutex);
        m_monitoringInterval = chrono::milliseconds(intervalMs);  // Write interval under lock
    }
    LOG(info) << "Monitoring interval set to " << intervalMs << " ms" << endl;
}

void SystemMonitoringTask::setupSignalHandling() {
    // Store instance for signal handler
    s_instance = this;
    
    // Register signal handlers
    signal(SIGINT, SystemMonitoringTask::signalHandler);   // Ctrl+C
    signal(SIGTERM, SystemMonitoringTask::signalHandler);  // Termination
    signal(SIGHUP, SystemMonitoringTask::signalHandler);   // Hang up
    
    LOG(info) << "Signal handlers registered" << endl;
}

void SystemMonitoringTask::signalHandler(int signalNum) {
    LOG(info) << "Received signal " << signalNum << " - initiating graceful shutdown" << endl;
    
    if (s_instance) {
        s_instance->stop();
    }
    
    // Trigger application-wide shutdown
    exit(signalNum);
}

void SystemMonitoringTask::cleanupSignalHandling() {
    // Restore default signal handlers
    signal(SIGINT, SIG_DFL);
    signal(SIGTERM, SIG_DFL);
    signal(SIGHUP, SIG_DFL);
    
    s_instance = nullptr;
    
    LOG(info) << "Signal handlers cleaned up" << endl;
}

void SystemMonitoringTask::monitoringLoop() {
    LOG(info) << "System monitoring loop started for container: " << m_containerName << endl;
    
    try {
        while (!m_shouldExit) {
            auto startTime = chrono::steady_clock::now();
            
            try {
                // Collect and send metrics
                collectAndSendMetrics();
                
            } catch (const exception& e) {
                LOG(error) << "Error collecting metrics: " << e.what() << endl;
                // Continue monitoring despite errors
            }
            
            // Wait for next iteration with proper timing
            waitForNextIteration(startTime);
        }
    } catch (const exception& e) {
        LOG(error) << "Fatal error in monitoring loop: " << e.what() << endl;
    }
    
    LOG(info) << "System monitoring loop exited" << endl;
}

void SystemMonitoringTask::collectAndSendMetrics() {
    // Collect container-specific metrics
    double cpuUsage = getContainerCpuUsage();
    double ramUsage = getContainerMemoryUsage();
    double gpuUsage = getContainerGpuUsage();
    double availableStorageMB = getAvailableStorageSpaceMB();
    
    // Send metrics to Prometheus with container name
    PrometheusClient* prometheus = GET_PROMETHEUS();
    if (prometheus) {
        prometheus->updateCpuUsageByContainer(cpuUsage, m_containerName);
        prometheus->updateRamUsageByContainer(ramUsage, m_containerName);
        prometheus->updateGpuUsageByContainer(gpuUsage, m_containerName);
        prometheus->updateAvailableStorageSpace(availableStorageMB);
    } else {
        LOG(warning) << "Prometheus client not available" << endl;
    }
}

void SystemMonitoringTask::waitForNextIteration(chrono::steady_clock::time_point startTime) {
    auto endTime = chrono::steady_clock::now();
    auto elapsed = endTime - startTime;
    
    unique_lock<mutex> lock(m_cvMutex);
    auto sleepDuration = m_monitoringInterval - elapsed;  // Read interval under lock
    
    // Ensure we never have negative sleep duration to avoid tight spin loops
    // Even when behind schedule, yield CPU time by waiting for at least 0ms
    if (sleepDuration < chrono::milliseconds(0)) {
        sleepDuration = chrono::milliseconds(0);
    }
    
    // Always wait (even with 0 duration) to yield CPU time and check exit condition
    m_cv.wait_for(lock, sleepDuration, [this] { return m_shouldExit.load(); });
}

// ============================================================================
// EXTRACTED SYSTEM METRICS FUNCTIONS FROM StreamMonitor
// ============================================================================

string SystemMonitoringTask::getContainerName() {
    // First, try to get container name from CONTAINER_NAME environment variable
    const char* containerName = std::getenv("CONTAINER_NAME");
    if (containerName && containerName[0] != '\0') {
        return string(containerName);
    }
    
    // Fallback: try to get container name from hostname (Docker sets this automatically)
    const char* hostname = std::getenv("HOSTNAME");
    if (hostname) {
        return string(hostname);
    }
    
    // Final fallback: read from /proc/sys/kernel/hostname
    ifstream file("/proc/sys/kernel/hostname");
    if (file.is_open()) {
        string hostName;
        getline(file, hostName);
        file.close();
        return hostName;
    }
    
    return "unknown";
}

double SystemMonitoringTask::getContainerCpuUsage() {
    static unsigned long long lastUserTime = 0, lastSystemTime = 0;
    static auto lastTs = chrono::steady_clock::now();
    
    // Try cgroup v2 first with improved implementation
    ifstream cgroupV2File("/sys/fs/cgroup/cpu.stat");
    if (cgroupV2File.is_open()) {
        string line;
        unsigned long long userTime = 0, systemTime = 0;
        
        // Parse user_usec and system_usec from cpu.stat
        while (getline(cgroupV2File, line)) {
            if (line.find("user_usec") == 0) {
                istringstream iss(line);
                string key;
                iss >> key >> userTime;
            } else if (line.find("system_usec") == 0) {
                istringstream iss(line);
                string key;
                iss >> key >> systemTime;
            }
        }
        cgroupV2File.close();

        // First call - initialize baseline values
        if (lastUserTime == 0 && lastSystemTime == 0) {
            lastUserTime = userTime;
            lastSystemTime = systemTime;
            lastTs = chrono::steady_clock::now();
            return 0.0;
        }

        // Calculate time elapsed and CPU time difference
        auto now = chrono::steady_clock::now();
        double seconds = chrono::duration<double>(now - lastTs).count();
        if (seconds <= 0.0) return 0.0;
        
        // Calculate total CPU time difference (in microseconds)
        double totalDiff = (userTime - lastUserTime) + (systemTime - lastSystemTime);
        
        // Update for next calculation
        lastTs = now;
        lastUserTime = userTime;
        lastSystemTime = systemTime;
        
        // Normalize by number of CPU cores to get percentage per core
        unsigned int cpuCnt = thread::hardware_concurrency();
        if (cpuCnt == 0) cpuCnt = 1;              // fallback
        double percent = (totalDiff / 1e6) / seconds * 100.0 / cpuCnt;
        return percent;
    }
    
    // Try cgroup v1 as fallback
    unsigned long long userTime = 0, systemTime = 0;
    ifstream cgroupFile("/sys/fs/cgroup/cpuacct/cpuacct.stat");
    if (cgroupFile.is_open()) {
        string line;
        while (getline(cgroupFile, line)) {
            if (line.find("user") == 0) {
                istringstream iss(line);
                string key;
                iss >> key >> userTime;
            } else if (line.find("system") == 0) {
                istringstream iss(line);
                string key;
                iss >> key >> systemTime;
            }
        }
        cgroupFile.close();
        
        if (lastUserTime == 0 && lastSystemTime == 0) {
            lastUserTime = userTime;
            lastSystemTime = systemTime;
            return 0.0;
        }
        
        unsigned long long totalDiff = (userTime - lastUserTime) + (systemTime - lastSystemTime);
        lastUserTime = userTime;
        lastSystemTime = systemTime;
        
        // Convert from USER_HZ to percentage (rough estimation)
        return (double)totalDiff / 100.0;
    }
    
    return 0.0; // No container metrics available
}

double SystemMonitoringTask::getContainerMemoryUsage() {
    // Try cgroup v1 first
    ifstream usageFile("/sys/fs/cgroup/memory/memory.usage_in_bytes");
    ifstream limitFile("/sys/fs/cgroup/memory/memory.limit_in_bytes");
    
    if (usageFile.is_open() && limitFile.is_open()) {
        unsigned long long usage = 0, limit = 0;
        usageFile >> usage;
        limitFile >> limit;
        usageFile.close();
        limitFile.close();
        
        // Check if limit is reasonable (not the default huge value)
        if (limit > 0 && usage > 0 && limit < (1ULL << 60)) {
            return (double)usage / limit * 100.0;
        }
    }
    
    // Try cgroup v2
    ifstream cgroupV2File("/sys/fs/cgroup/memory.current");
    ifstream cgroupV2LimitFile("/sys/fs/cgroup/memory.max");
    
    if (cgroupV2File.is_open() && cgroupV2LimitFile.is_open()) {
        unsigned long long usage = 0, limit = 0;
        cgroupV2File >> usage;
        
        string limitStr;
        cgroupV2LimitFile >> limitStr;
        cgroupV2File.close();
        cgroupV2LimitFile.close();
        
        if (limitStr != "max") {
            limit = stoull(limitStr);
            if (limit > 0 && usage > 0) {
                return (double)usage / limit * 100.0;
            }
        } else {
            // For unlimited containers, calculate percentage against system memory
            if (usage > 0) {
                // Read system memory from /proc/meminfo
                ifstream meminfoFile("/proc/meminfo");
                if (meminfoFile.is_open()) {
                    string line;
                    unsigned long long totalSystemMem = 0;
                    
                    while (getline(meminfoFile, line)) {
                        if (line.find("MemTotal:") == 0) {
                            istringstream iss(line);
                            string key, unit;
                            iss >> key >> totalSystemMem >> unit;
                            totalSystemMem *= 1024; // Convert KB to bytes
                            break;
                        }
                    }
                    meminfoFile.close();
                    
                    if (totalSystemMem > 0) {
                        return (double)usage / totalSystemMem * 100.0;
                    }
                }
            }
        }
    }
    
    return 0.0; // No container metrics available
}

double SystemMonitoringTask::getContainerGpuUsage() {
    // Get container-wise GPU usage using nvidia-smi pmon
    ifstream file("/proc/driver/nvidia/gpus/0000:01:00.0/information");
    if (!file.is_open()) {
        return 0.0; // No NVIDIA GPU available
    }
    file.close();
    
    // Get current container's name for comparison
    string currentContainerName = getContainerName();
    
    FILE* pipe = popen("nvidia-smi pmon -c 1 2>/dev/null", "r");
    if (!pipe) {
        return 0.0;
    }
    
    char buffer[512];
    double totalGpuUsage = 0.0;
    bool foundData = false;
    bool hasActiveProcesses = false;
    
    // Skip header lines (start with #)
    while (fgets(buffer, sizeof(buffer), pipe)) {
        if (buffer[0] == '#') {
            continue;
        }
        
        // Check if this line indicates no processes (contains dashes)
        string bufferStr(buffer);
        if (bufferStr.find("     -     -      -      -      -      -      -      -    -") != string::npos) {
            continue;
        }
        
        // Parse pmon output: gpu pid type sm mem enc dec command
        // Use safe parsing instead of dangerous sscanf
        int gpu, pid, sm, mem, enc, dec;
        string command;
        
        bool parseSuccess = false;
        try
        {
            istringstream iss(buffer);
            string typeStr;
            
            // Parse each field with proper validation
            if (iss >> gpu >> pid >> typeStr >> sm >> mem >> enc >> dec >> command)
            {
                // Handle type field - we read it but don't need to store it
                if (!typeStr.empty())
                {
                    parseSuccess = true;
                }
            }
        }
        catch (const exception& e)
        {
            // Log parsing error but continue processing
            LOG(warning) << "Failed to parse nvidia-smi pmon output: " << e.what() << endl;
        }
        
        if (parseSuccess && pid > 0 && sm >= 0) {
            hasActiveProcesses = true;
            
            // Try to determine if this process belongs to current container
            bool isCurrentContainer = false;
            
            // Check if command matches container name or if we're in unknown container
            if (currentContainerName == "unknown" || 
                command.find(currentContainerName) != string::npos) {
                isCurrentContainer = true;
            }
            
            if (isCurrentContainer || currentContainerName == "unknown") {
                totalGpuUsage += sm; // sm is the streaming multiprocessor utilization %
                foundData = true;
            }
        }
    }
    
    pclose(pipe);
    
    // If no active processes found, return 0.0 (this is normal - GPU not in use)
    if (!hasActiveProcesses) {
        return 0.0;
    }
    
    // If no container-specific data found but there are active processes, return 0.0
    if (!foundData) {
        return 0.0;
    }
    
    // Cap at 100% in case multiple processes add up to more
    return min(totalGpuUsage, 100.0);
}

double SystemMonitoringTask::getAvailableStorageSpaceMB() {
    // Check the VST_VOLUME storage filesystem usage
    struct statvfs stat;
    const char* path = getenv("VST_VOLUME");
    
    // If VST_VOLUME environment variable is not set, fallback to root filesystem
    if (!path) {
        path = "/";
    }
    
    if (statvfs(path, &stat) != 0) {
        // Fallback to root filesystem if VST_VOLUME path doesn't exist
        path = "/";
        if (statvfs(path, &stat) != 0) {
            return 0.0;
        }
    }
    
    unsigned long long free = stat.f_bavail * stat.f_frsize;
    return (double)free / (1024.0 * 1024.0); // Convert bytes to MB
} 