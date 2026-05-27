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

using namespace std;

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

void getDirSize(const string& dir_path, size_t& size);
void getFileSize(const string& dir_path, uint32_t& size);
bool deleteFile(const string& file_name);
void deleteEmptyDirectories(const string& dir_path, const string& root_dir);
string getDirPath(const string& filename);
int getVideoFiles(const string& dir_path, const std::vector<string>& containers, vector<string>& list);
bool isFileExist(const std::string& file_name);
bool createDir(const string& path);
void updateFilePermissions(const string& file_path, const int& perm);
bool isDirExist(const std::string& path);
size_t getAvailableSpace(const string& drive);
std::vector<string> getDirEntries(const string& dir_path);
uint64_t getFileTimestamp(const string& filepath);
string getFileName(const string& file_path);
string getFileNameWithExtension(const string& file_path);
string getFileExtension(const string& file_path);
string getUniqueFilePath(std::string fileName, std::string fileLocation);
string getFileNameFromHeader(const char* content_disposition);
string getExtensionFromHeader(const char* content_type);
string getCurrentDirPath();
size_t getFileSizeInBytes(const string& file_path);
size_t getStorageCapacity(const string& drive);
size_t getFreeSpace(const string& drive);
size_t getUsedSpace(const string& drive);
string appendDirectory(const string& p1, const string& p2);
vector<std::string> getFilesInDirectory(const string& dir);
bool deleteDirectory(const std::string& dir);
bool createFile(const string& file_path, const string& file_content = "");
bool isEmptyFile(const string& file_path);
std::string getPasswordHash(const string& username);
std::string getFilePathWithName(const string& file_path, const string& file_name);
std::string readFileIntoString(const string &path);
bool replaceFile(const string& src_file_name, const string& dst_file_name);
string format_vector(const std::vector<std::string> &v);
bool writeBinaryFile(const string& file_path, const string& binary_data);