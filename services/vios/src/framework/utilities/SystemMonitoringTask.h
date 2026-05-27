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

#ifndef SYSTEM_MONITORING_TASK_H
#define SYSTEM_MONITORING_TASK_H

#include <thread>
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <chrono>
#include <string>
#include <csignal>
#include <future>
#include <memory>
#include "logger.h"
#include "prometheus_client/prometheus_client.h"
#include "config.h"

using namespace std;

/**
 * @brief Standalone system monitoring task that collects CPU, RAM, GPU, and storage metrics
 * 
 * This class provides a self-contained system monitoring solution that runs in a separate thread.
 * It handles graceful shutdown via signal handlers (Ctrl+C) and sends metrics to Prometheus.
 */
class SystemMonitoringTask {
private:
    // Thread management
    thread m_monitoringThread;
    atomic<bool> m_shouldExit{false};
    condition_variable m_cv;
    mutex m_cvMutex;
    
    // Configuration
    string m_containerName;
    chrono::milliseconds m_monitoringInterval{5000}; // Default, overridden by config
    
    // Signal handling - static for C-style signal handler
    static SystemMonitoringTask* s_instance;
    static void signalHandler(int signal);
    
    // Core monitoring functions (extracted from StreamMonitor)
    string getContainerName();
    double getContainerCpuUsage();
    double getContainerMemoryUsage();
    double getContainerGpuUsage();
    double getAvailableStorageSpaceMB();
    
    // Internal methods
    void monitoringLoop();
    void waitForNextIteration(chrono::steady_clock::time_point startTime);
    void collectAndSendMetrics();
    void setupSignalHandling();
    void cleanupSignalHandling();
    
public:
    /**
     * @brief Constructor - initializes but does not start monitoring
     */
    SystemMonitoringTask();
    
    /**
     * @brief Destructor - ensures graceful shutdown
     */
    ~SystemMonitoringTask();
    
    /**
     * @brief Start system monitoring in a separate thread
     */
    void start();
    
    /**
     * @brief Stop system monitoring gracefully
     */
    void stop();
    
    /**
     * @brief Check if monitoring is currently running
     */
    bool isRunning() const;
    
    /**
     * @brief Set monitoring interval (overrides config default)
     * @param intervalMs Monitoring interval in milliseconds
     */
    void setMonitoringInterval(int intervalMs);
    
    // Prevent copying
    SystemMonitoringTask(const SystemMonitoringTask&) = delete;
    SystemMonitoringTask& operator=(const SystemMonitoringTask&) = delete;
};

#endif // SYSTEM_MONITORING_TASK_H 