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

#define BOOST_TIMER_ENABLE_DEPRECATED

#include "fs_utils.h"
#include <boost/filesystem.hpp>
#include <boost/range/numeric.hpp>
#include "boost/filesystem/operations.hpp"
#include "boost/filesystem/path.hpp"
#include "boost/progress.hpp"
#include "boost/regex.hpp"
#include <boost/range/iterator_range.hpp>
#include <boost/format.hpp>
#include <boost/algorithm/string.hpp>
#include <map>
#include <boost/timer.hpp>
#include <boost/filesystem/fstream.hpp> // Required in 1.83
#include <boost/filesystem/string_file.hpp>

#include "logger.h"

namespace fs = boost::filesystem;

static size_t du(fs::path p)
{
    size_t total_size = 0;

    try
    {
        if (fs::exists(p))
        {
            if (fs::is_directory(p))
            {
                fs::recursive_directory_iterator dir_iter(p, fs::directory_options::skip_permission_denied);
                fs::recursive_directory_iterator end_iter;

                for (; dir_iter != end_iter; ++dir_iter)
                {
                    try
                    {
                        const fs::directory_entry& entry = *dir_iter;
                        if (fs::is_regular_file(entry))
                        {
                            total_size += fs::file_size(entry);
                        }
                    }
                    catch (const fs::filesystem_error& e)
                    {
                        LOG(error) << "Error accessing file: " << e.what() << endl;
                        continue;
                    }
                }
            }
            else if (fs::is_regular_file(p))
            {
                total_size = fs::file_size(p);
                LOG(info) << "Single file size: " << total_size << endl;
            }
        }
        else
        {
            LOG(error) << "File/Folder does not exist: " << p << endl;
        }
    }
    catch (const fs::filesystem_error& e)
    {
        LOG(error) << "Filesystem error: " << e.what() << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Standard exception: " << e.what() << endl;
    }
    catch (...)
    {
        LOG(error) << "Unknown exception occurred" << endl;
    }

    size_t size_mb = total_size / (1024 * 1024);
    LOG(verbose) << "Total size in MB: " << size_mb << endl;
    return size_mb;
}

void getDirSize(const string& dir_path, size_t& size)
{
    try
    {
        size = du(dir_path); // throws
    }
    catch(const std::exception& e)
    {
        size = 0;
        LOG(error) << "Failed to get directory size: " << e.what() << endl;
    }
    LOG(verbose) << "Size of dir: " << dir_path << " " << size << endl;
}

void getFileSize(const string& file_path, uint32_t& size)
{
    try
    {
        size = du(file_path); // throws
    }
    catch(const std::exception& e)
    {
        size = 0;
        LOG(error) << "Failed to get file size: " << e.what() << endl;
    }
    LOG(verbose) << "Size of file: " << file_path << " " << size << endl;
}

size_t getFileSizeInBytes(const string& file_path)
{
    size_t sizeInBytes = 0;
    try
    {
        sizeInBytes = fs::file_size(file_path); //throws
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to get file size: " << e.what() << endl;
    }
    return sizeInBytes;
}

bool deleteFile(const string& file_name)
{
    if (!isFileExist(file_name))
    {
        LOG(error) << "File does not exist, cannot delete: " << file_name << endl;
        return false;
    }
    try
    {
        LOG(info) << "Deleting File : " << file_name << endl;
        return fs::remove(file_name); //throws
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to remove file " << file_name << " : " << e.what() << endl;
        return false;
    }
}

void deleteEmptyDirectories(const string& dir_path, const string& root_dir)
{
    LOG(verbose) << "path: " << dir_path << "root_dir: " << root_dir << endl;
    try
    {
        if (fs::is_directory(dir_path)) // throws
        {
            fs::path p(dir_path);
            string parent = p.parent_path().string();
            if (fs::is_directory(p)) // throws
            {
                if( fs::is_empty(p)) // might throw
                {
                    LOG(verbose) << "Remove Empty path: " << p.string() << endl;
                    fs::remove(p); //throws
                }
                else
                {
                    return;
                }
            }
            if (fs::equivalent(parent, root_dir) == false) // throws
            {
                deleteEmptyDirectories(parent, root_dir);
            }
        }
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to delete directory for " << dir_path << " : " << e.what() << endl;
    }
}

string getDirPath(const string& filename)
{
    string dir_path = "";
    fs::path p(filename);
    try
    {
        if ( fs::exists(p) ) // throws
        {
            dir_path = p.parent_path().string();
        }
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to get dir path for " << filename << " : " << e.what() << endl;
    }
    return  dir_path;
}

string format_vector(const std::vector<std::string> &v)
{
    string regex_str(".(");
    for(const auto &s : v)
    {
        regex_str += s + string("|");
    }
    regex_str = regex_str.substr(0, regex_str.size() -1 );
    regex_str += string(")$");
    return regex_str;
}

int getVideoFiles(const string& dir_path, const std::vector<string>& containers, vector<string>& list)
{
    int ret = 0;
    string regex_str = format_vector(containers);
    boost::regex e(regex_str, boost::regex::icase);
    std::string filename;
    fs::path p(dir_path);

    try
    {
        if (fs::exists(p) && fs::is_directory(p))
        {
            fs::recursive_directory_iterator dir_iter(p, fs::directory_options::skip_permission_denied);
            fs::recursive_directory_iterator end_iter;

            for (; dir_iter != end_iter; ++dir_iter)
            {
                try
                {
                    const fs::directory_entry& f = *dir_iter;
                    if (fs::is_regular_file(f.status()))
                    {
                        filename = f.path().string();
                        // ignore files which have whitespace
                        if (!checkWhiteSpace(filename) && boost::regex_search(filename, e))
                        {
                            LOG(info) << "Found video file: " << filename << endl;
                            list.push_back(filename);
                        }
                    }
                }
                catch (const fs::filesystem_error& e)
                {
                    LOG(error) << "Error accessing file: " << e.what() << endl;
                    continue;
                }
            }
        }
        else
        {
            LOG(error) << "Directory does not exist or is not accessible: " << dir_path << endl;
            ret = -1;
        }
    }
    catch (const std::exception& e)
    {
        ret = -1;
        LOG(error) << "getVideoFiles fail: " << e.what() << endl;
    }

    return ret;
}

bool isFileExist(const std::string& file_name)
{
    try
    {
        return boost::filesystem::exists(file_name); // throws
    }
    catch(const std::exception& e)
    {
        LOG(error) << "isFileExist fail: " << e.what() << endl;
        return false;
    }

}

bool isDirExist(const std::string& dir_path)
{
    try
    {
        return boost::filesystem::exists(dir_path); // throws
    }
    catch(const std::exception& e)
    {
        LOG(error) << "isDirExist fail: " << e.what() << endl;
        return false;
    }
}

bool createDir(const string& dir_path)
{
    bool success = false;
    LOG(info) << "Creating dir_path: " << dir_path << endl;

    try
    {
        if (fs::exists(dir_path)) // throws
        {
            return true;
        }
        success = fs::create_directories(dir_path); //throws
    }
    catch(const std::exception& e)
    {
        std::cerr << "Failed to create directory:" << dir_path << ", Exception error:" << e.what() << endl;
    }
    if (success)
    {
        updateFilePermissions(dir_path,
                permissions::owner_all | permissions::group_read |
                permissions::others_read | permissions::others_exe);
    }
    if (success == false)
    {
        LOG(info) << "Failed to create directory dir_path: " << dir_path << endl;
    }
    return success;
}

bool createFile(const string& file_path, const string& file_content)
{
    try
    {
        if (fs::exists(file_path)) // throws
        {
            LOG(error) << "file already exists" << endl;
            return false;
        }
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to check file " << file_path << ": " << e.what() << endl;
    }
    std::ofstream out(file_path);
    if (!file_content.empty())
    {
        out << file_content;
    };
    updateFilePermissions(file_path, permissions::owner_all | permissions::group_read);
    return true;
}

void updateFilePermissions(const string& file_path, const int& perm)
{
    try
    {
        fs::permissions(file_path, (fs::perms)perm); // throws
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to apply file permissions: " << e.what() << endl;
    }
}

bool isEmptyFile(const string& file_path)
{
    try
    {
        if(fs::is_empty(file_path)) // throws
        {
            return true;
        }
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Empty file check failed " << file_path << ": " << e.what() << endl;
    }
    return false;
}

std::string getPasswordHash(const string& username)
{
    //regex for username:realm:password
    boost::regex expr{".+:.+:.+"};
    fs::ifstream fileHandler(GET_CONFIG().password_file_path);
    std::string passwordHash = EMPTY_STRING;
    std::string line;
    while (getline(fileHandler, line))
    {
        if(boost::regex_match(line, expr))
        {
            //if regex matches then search for username
            vector<string> tokens;
            try
            {
                boost::split(tokens, line, boost::is_any_of(":"));
                if(tokens[0] == username)
                {
                    passwordHash = tokens[2];
                    break;
                }
            }
            catch(const std::exception& e)
            {
                LOG(error) << "boost::split failed " << maskSensitiveData(username, MaskType::USERNAME) << ": " << e.what() << endl;
            }
        }
        else
        {
            //password file not in correct format
            LOG(error) << "Corrupted password file" << endl;
            break;
        }
    }
    return passwordHash;
}

string getFilePathWithName(const string& file_path, const string& file_name)
{
    fs::path dir(file_path);
    fs::path file(file_name);
    fs::path full_path = dir / file;
    return full_path.string();
}

size_t getAvailableSpace(const string& drive)
{
    try
    {
        fs::space_info si = fs::space(drive); // throws
        return si.available;
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Space check failed " << drive << ": " << e.what() << endl;
    }
    return 0;
}

size_t getStorageCapacity(const string& drive)
{
    try
    {
        //returns disk storage capacity in bytes
        fs::space_info si = fs::space(drive); // throws
        return si.capacity;
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Storage Capacity check failed " << drive << ": " << e.what() << endl;
    }
    return 0;
}

size_t getFreeSpace(const string& drive)
{
    try
    {
        fs::space_info si = fs::space(drive); // throws
        return si.free;
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Space check failed " << drive << ": " << e.what() << endl;
    }
    return 0;
}

size_t getUsedSpace(const string& drive)
{
    try
    {
        fs::space_info si = fs::space(drive);
        // Used space = Total capacity - Free space
        return (si.capacity - si.available);
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Space check failed " << drive << ": " << e.what() << endl;
    }
    return 0;
}

std::vector<string> getDirEntries(const string& dir_path)
{
    std::vector<std::string> entries;

    try
    {
        fs::path path(dir_path);
        if (fs::exists(path) && fs::is_directory(path))
        {
            fs::directory_iterator end_iter;

            for (fs::directory_iterator dir_iter(path, fs::directory_options::skip_permission_denied);
            dir_iter != end_iter; ++dir_iter)
            {
                try
                {
                    entries.push_back(dir_iter->path().filename().string());
                }
                catch (const fs::filesystem_error& ex)
                {
                    LOG(error) << "Error accessing entry " << dir_iter->path().string()
                    << ": " << ex.what() << endl;
                }
            }
        }
        else
        {
            LOG(error) << "Path does not exist or is not a directory: " << dir_path << endl;
        }
    }
    catch (const fs::filesystem_error& ex)
    {
        LOG(error) << "Error accessing directory " << dir_path << ": " << ex.what() << endl;
    }

    return entries;
}

uint64_t getFileTimestamp(const string& filepath)
{
    try
    {
        time_t writeTime = fs::last_write_time(filepath); // throws
        writeTime *= 1000;

        LOG(info) << " -*-*-* file start time ms:" << writeTime << endl;
        return writeTime;
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to get file timestamp " << filepath << ": " << e.what() << endl;
    }
    return 0;
}

string getFileName(const string& file_path)
{
    fs::path f(file_path);
    return f.filename().stem().string();
}

string getFileNameWithExtension(const string& file_path)
{
    fs::path f(file_path);
    return f.filename().string();
}

string getFileExtension(const string& file_path)
{
    fs::path f(file_path);
    return f.extension().string();
}

string getUniqueFilePath(std::string fileName, std::string fileLocation)
{
    fs::path name(fileName);
    fs::path root_path (fileLocation);
    fs::path total_path = root_path / name;
    try
    {
        if (boost::filesystem::exists(total_path) == false) // throws
        {
            return total_path.string();
        }
        int num = 0;
        while(boost::filesystem::exists(total_path)) // throws
        {
            num++;
            fs::path new_file = name.stem().string() + std::string("_") +
                                std::to_string(num) + name.extension().string();
            total_path = root_path / new_file ;
        }
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to get unique file path " << total_path << ": " << e.what() << endl;
    }
    return total_path.string();
}

string getFileNameFromHeader(const char* content_disposition)
{
    //Content-Disposition: attachment; filename=file.mp4
    vector<string> tokens;
    std::string fileName = EMPTY_STRING;
    if(content_disposition != nullptr)
    {
        boost::split(tokens, content_disposition, boost::is_any_of("="));
        if(tokens.size() == 2)
        {
            fileName = tokens[1];
        }
    }
    return fileName;
}

string getExtensionFromHeader(const char* content_type)
{
    //Content-Type: video/mp4
    vector<string> tokens;
    std::string fileExtension = EMPTY_STRING;
    if(content_type != nullptr)
    {
        boost::split(tokens, content_type, boost::is_any_of("/"));
        if(tokens.size() == 2)
        {
            fileExtension = tokens[1];
        }
    }
    return fileExtension;
}

string getCurrentDirPath()
{
    return fs::current_path().string();
}

string appendDirectory(const string& p1, const string& p2)
{
    boost::filesystem::path p(p1);
    p /= p2;
    try
    {
        boost::filesystem::create_directory(p); // throws
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to create directory " << p.string() << ": " << e.what() << endl;
    }
    return p.string();
}

string pathToString(fs::path path)
{
    return path.string();
}

bool sortFiles(string s1, string s2)
{
    bool compare = false;
    string file1 = getFileName(s1);
    string file2 = getFileName(s2);
    string num1 = "0";
    string num2 = "0";
    size_t found1 = file1.find_last_of('_');
    size_t found2 = file2.find_last_of('_');
    if (found1 != string::npos)
    {
        num1 = file1.substr(found1 + 1);
    }
    if (found2 != string::npos)
    {
        num2 = file2.substr(found2 + 1);
    }

    try
    {
        compare = abs(stol(num1)) < abs(stol(num2)) ? true : false;
    }
    catch(const std::exception& e)
    {
        LOG(error) << e.what() << " Unable to sort chunks" << endl;
    }

    return compare;
}

vector<std::string> getFilesInDirectory(const std::string& dir)
{
    //sort and return files based on their last write time
    std::vector<std::string> files_in_directory;
    fs::path myFolder(dir);

    try
    {
        if (fs::exists(myFolder) && fs::is_directory(myFolder))
        {
            fs::directory_iterator end_iter;
            for (fs::directory_iterator dir_iter(myFolder, fs::directory_options::skip_permission_denied);
            dir_iter != end_iter; ++dir_iter)
            {
                try
                {
                    if (fs::is_regular_file(dir_iter->status()))
                    {
                        files_in_directory.push_back(dir_iter->path().string());
                    }
                }
                catch (const fs::filesystem_error& ex)
                {
                    LOG(warning) << "Error accessing file " << dir_iter->path().string()
                    << ": " << ex.what() << endl;
                }
            }
        }
        else
        {
            LOG(error) << "Directory does not exist or is not accessible: " << dir << endl;
        }
    }
    catch (const fs::filesystem_error& ex)
    {
        LOG(error) << "Error accessing directory " << dir << ": " << ex.what() << endl;
        throw std::runtime_error("Failed to access directory: " + std::string(ex.what()));
    }

    std::sort(files_in_directory.begin(), files_in_directory.end(), sortFiles);
    return files_in_directory;
}

bool deleteDirectory(const std::string& dir)
{
    try
    {
        return fs::remove_all(dir); // throws
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to delete directory " << dir << ": " << e.what() << endl;
    }
    return false;
}

string readFileIntoString(const string &path)
{
    std::string result;
    try
    {
#ifndef JETSON_PLATFORM
        fs::load_string_file(path, result);
#endif
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to read file: " << path << " " << e.what() << endl;
        return "";
    }

    return base64_encode(result.c_str(), result.size());
}

bool replaceFile(const string& src_file_name, const string& dst_file_name)
{
    if (isFileExist(src_file_name))
    {
        fs::copy_file(src_file_name, dst_file_name, fs::copy_options::overwrite_existing);
        return true;
    }
    return false;
}

bool writeBinaryFile(const string& file_path, const string& binary_data)
{
    try
    {
        std::ofstream outFile(file_path, std::ios::binary);
        if (!outFile.is_open())
        {
            LOG(error) << "Failed to open file for writing: " << file_path << endl;
            return false;
        }

        outFile.write(binary_data.c_str(), binary_data.length());
        outFile.close();
        
        if (outFile.fail())
        {
            LOG(error) << "Failed to write binary data to file: " << file_path << endl;
            return false;
        }
        
        LOG(info) << "Successfully wrote " << binary_data.length() << " bytes to: " << file_path << endl;
        return true;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception writing binary file " << file_path << ": " << e.what() << endl;
        return false;
    }
}

