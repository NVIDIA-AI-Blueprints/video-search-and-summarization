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

#pragma once

#include "unified_storage_manager.h"
#include <filesystem>
#include <regex>
#include <vector>

namespace nv_vms
{

/**
 * @brief Concrete implementation of unified storage manager for local file systems
 */
class UnifiedLocalStorageManager : public UnifiedStorageManager
{
public:
    UnifiedLocalStorageManager();
    virtual ~UnifiedLocalStorageManager();

    // Core interface
    bool isAvailable() const override;
    std::string getStorageMode() const override;

    // Configuration
    bool configureStorage(const StorageConfig& config) override;
    StorageConfig getStorageConfiguration() const override;

    // File operations (unified interface)
    DeleteResult deleteFile(const std::string& path) override;
    DeleteResult deleteDirectory(const std::string& path, bool recursive = false) override;
    
    // File existence checking (unified interface)
    bool isFileExist(const std::string& path) const override;
    
    // Multi-file delete operations
    MultiDeleteResult deleteMultipleFiles(const std::vector<std::string>& file_paths) override;
    MultiDeleteResult deleteFilesInDirectory(const std::string& directory_path, 
                                            const std::string& pattern = "",
                                            bool recursive = false) override;
    
    // Directory operations (local storage)
    bool createDirectory(const std::string& path, bool create_parents = true) override;
    bool directoryExists(const std::string& path) const override;
    
    // Statistics and monitoring
    StorageStats getManagerStats() const override;
    void resetManagerStats() override;

    // Health and diagnostics
    bool performHealthCheck() override;
    std::string getLastError() const override;

protected:
    // UnifiedStorageManager interface
    bool initializeStorage() override;
    bool cleanupStorage() override;

    // Local storage specific methods
    std::string getFullPath(const std::string& relative_path) const;
    bool isPathWithinBase(const std::string& path) const;
    std::vector<std::string> findFilesMatchingPattern(const std::string& directory_path, 
                                                      const std::string& pattern) const;
    uint64_t calculateDirectorySize(const std::string& path) const;
    size_t countFilesInDirectory(const std::string& path, bool recursive = false) const;

    // Member variables
    std::string m_base_path;
    bool m_create_directories;
    bool m_recursive_listing;
    uint32_t m_max_depth;
    bool m_include_hidden;
};

} // namespace nv_vms 