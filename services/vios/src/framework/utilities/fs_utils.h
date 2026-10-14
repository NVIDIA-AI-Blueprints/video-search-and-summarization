/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <iostream>
#include <memory>
#include <string>
#include <vector>


enum permissions
{
    no_perms = 0,
    owner_read = 0400,  // S_IRUSR, Read permission, owner
    owner_write = 0200, // S_IWUSR, Write permission, owner
    owner_exe = 0100,   // S_IXUSR, Execute/search permission, owner
    owner_all = 0700,   // S_IRWXU, Read, write, execute/search by owner

    group_read = 040,   // S_IRGRP, Read permission, group
    group_write = 020,  // S_IWGRP, Write permission, group
    group_exe = 010,    // S_IXGRP, Execute/search permission, group
    group_all = 070,    // S_IRWXG, Read, write, execute/search by group

    others_read = 04,   // S_IROTH, Read permission, others
    others_write = 02,  // S_IWOTH, Write permission, others
    others_exe = 01,    // S_IXOTH, Execute/search permission, others
    others_all = 07,    // S_IRWXO, Read, write, execute/search by others

    all_all = 0777,     // owner_all|group_all|others_all
};

void getDirSize(const std::string& dir_path, size_t& size);
void getFileSize(const std::string& dir_path, uint32_t& size);
bool deleteFile(const std::string& file_name);
void deleteEmptyDirectories(const std::string& dir_path, const std::string& root_dir);
std::string getDirPath(const std::string& filename);
int getVideoFiles(const std::string& dir_path, const std::vector<std::string>& containers, std::vector<std::string>& list);
bool isFileExist(const std::string& file_name);
bool createDir(const std::string& path);
void updateFilePermissions(const std::string& file_path, const int& perm);
bool isDirExist(const std::string& path);
size_t getAvailableSpace(const std::string& drive);
std::vector<std::string> getDirEntries(const std::string& dir_path);
uint64_t getFileTimestamp(const std::string& filepath);
std::string getFileName(const std::string& file_path);
std::string getFileNameWithExtension(const std::string& file_path);
std::string getFileExtension(const std::string& file_path);
std::string getUniqueFilePath(std::string fileName, std::string fileLocation);
std::string getFileNameFromHeader(const char* content_disposition);
std::string getExtensionFromHeader(const char* content_type);
std::string getCurrentDirPath();
size_t getFileSizeInBytes(const std::string& file_path);
size_t getStorageCapacity(const std::string& drive);
size_t getFreeSpace(const std::string& drive);
size_t getUsedSpace(const std::string& drive);
std::string appendDirectory(const std::string& p1, const std::string& p2);
std::vector<std::string> getFilesInDirectory(const std::string& dir);
bool deleteDirectory(const std::string& dir);
bool createFile(const std::string& file_path, const std::string& file_content = "");
bool isEmptyFile(const std::string& file_path);
std::string getPasswordHash(const std::string& username);
std::string getFilePathWithName(const std::string& file_path, const std::string& file_name);
std::string readFileIntoString(const std::string &path);
bool replaceFile(const std::string& src_file_name, const std::string& dst_file_name);
std::string format_vector(const std::vector<std::string> &v);
bool writeBinaryFile(const std::string& file_path, const std::string& binary_data);