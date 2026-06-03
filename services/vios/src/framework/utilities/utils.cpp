/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "utils.h"
#include "logger.h"
#include "cmdline_parser.h"
#include "OverlayDataTypes.h"

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fstream>
#include <regex>
#include <sstream>
#include <iomanip>
#include <uuid/uuid.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <cctype>
#include <algorithm>
#include <chrono>
#include <sstream>
#include <arpa/inet.h>
#include <random>
#include <bits/stdc++.h>
#include <netdb.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <ifaddrs.h>
#include <algorithm>
#include <iterator>
#include <regex>

#include<boost/algorithm/string.hpp>
#include <boost/algorithm/string/trim.hpp>
#include <boost/format.hpp>
#include<boost/regex.hpp>
#include <boost/stacktrace.hpp>

constexpr const char* ENCRYPTION_AES_KEY = "WnZr4u7x!A%D*G-KaPdSgVkYp3s5v8y/";
constexpr const char* SSL_COMMON_NAME = "VMS Webserver";
constexpr int MAX_GPU_COUNT = 100;
constexpr int NUM_DIGITS_SECOND = 10;
constexpr int NUM_DIGITS_MILLI_SECOND = 13;
constexpr int NUM_DIGITS_MICRO_SECOND = 16;
constexpr int NUM_DIGITS_NANO_SECOND = 19;
constexpr const char* HTTP_PROTOCOL = "http://";
constexpr const char* HTTPS_PROTOCOL = "https://";
constexpr const char* ZERO_EPOCH_TIME = "1970-01-01T00:00:00.000Z";
constexpr int MAX_QUERY_STRING_LENGTH = 4096;
constexpr int MAX_QUERY_PARAMS = 100;
constexpr int MAX_QUERY_PARAM_KEY_LENGTH = 256;
constexpr int MAX_QUERY_PARAM_VALUE_LENGTH = 4096;
constexpr int MAX_QUERY_PARAM_VALUES_PER_KEY = 10;
constexpr const char* GPU_DEV = "/dev/nvidia0";

uint32_t g_init_avaiable_memory;
bool g_isGpuPresent = false;
int g_gpuIndex = 0;
string g_gpuNodePath;
string g_hostIp;
#ifdef JETSON_PLATFORM
bool g_isJetsonGpuMode = false;
#endif

static const std::string base64_chars =
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789+/";

bool replaceString(std::string& str, const std::string& from, const std::string& to)
{
    size_t start_pos = str.find(from);
    if(start_pos == std::string::npos)
        return false;
    str.replace(start_pos, from.length(), to);
    return true;
}

void stripString(string& original)
{
    original.erase(std::remove(original.begin(), original.end(), '\n'), original.end());
}

void eraseString(string& orignal, const string& toErase)
{
    size_t pos = string::npos;
    // Search for the substring in string in a loop untill nothing is found
    while ((pos  = orignal.find(toErase) )!= string::npos)
    {
        // If found then erase it from string
        orignal.erase(pos, toErase.length());
    }
}

void eraseString(string& orignal, string start, int len)
{
    size_t pos = string::npos;
    if ((pos  = orignal.find(start) )!= string::npos)
    {
        // If found then erase it from string
        orignal.erase(pos, len);
    }
}

string jsonToString(const Json::Value& json)
{
    Json::FastWriter fastWriter;
    return fastWriter.write(json);
}

vector<string> splitString(const string line, const string& search)
{
    vector<string> arr;
    int spacePos;
    int currPos = 0;
    int k = 0;
    int prevPos = 0;

    if (line.empty() || search.empty())
    {
        LOG(error) << "Invalid inputs" << endl;
        return arr;
    }

    do
    {
        spacePos = line.find(search,currPos);
        if(spacePos >= 0)
        {
            currPos = spacePos;
            arr.push_back(line.substr(prevPos, currPos - prevPos));
            currPos++;
            prevPos = currPos;
            k++;
        }
    }while( spacePos >= 0);

    arr.push_back(line.substr(prevPos,line.length()));
    return arr;
}

void insertString(string& orignal, const string& afterToken, const string& subString)
{
    size_t pos = string::npos;

    pos = orignal.find(afterToken);
    if (pos != string::npos)
    {
        orignal.insert(pos + afterToken.size(), subString);
    }
}

#ifdef JETSON_PLATFORM
bool isJetsonGpuPresent()
{
    if (access(GPU_DEV, F_OK) == 0)
    {
        return true;
    }
    return false;
}
#endif

bool iequals(const string& a, const string& b)
{
    return std::equal(a.begin(), a.end(),
                      b.begin(), b.end(),
                      [](char str1, char str2) {
                          return tolower(str1) == tolower(str2);
                      });
}

bool findStringIgnoreCase(const std::string &str, const std::string &token)
{
  auto it = std::search(
    str.begin(), str.end(),
    token.begin(),   token.end(),
    [](unsigned char ch1, unsigned char ch2) { return std::toupper(ch1) == std::toupper(ch2); }
  );
  return (it != str.end() );
}

string decimalToHex(const int& number)
{
    std::stringstream ss;
    ss<< std::hex << number;
    std::string res ( ss.str() );
    return res;
}

/* compareISOTime function returns false if error i.e. Start Time (st) > End Time (et) or invalid input */
bool compareISOTime(const string& st, const string& et)
{
    std::time_t epoch_st = isoToEpoch (st);
    std::time_t epoch_et = isoToEpoch (et);
    if ((!epoch_st && st != ZERO_EPOCH_TIME) || (!epoch_et && et != ZERO_EPOCH_TIME))
    {
        /* Return error i.e. false */
        return false;
    }
    return epoch_st >= epoch_et ? false : true;
}

bool validateISOTime(const string& time)
{
    std::time_t epoch = isoToEpoch(time);
    if (!epoch && time != ZERO_EPOCH_TIME)
    {
        return false;
    }
    return true;
}

bool isFloat( string myString )
{
    std::istringstream iss(myString);
    float f;
    iss >> noskipws >> f; // noskipws considers leading whitespace invalid
    // Check the entire string was consumed and if either failbit or badbit is set
    return iss.eof() && !iss.fail();
}

bool isNumber(const std::string& s)
{
    return !s.empty() && std::find_if(s.begin(),
        s.end(), [](unsigned char c) { return !std::isdigit(c); }) == s.end();
}

bool valueWithinRange(const string& value, const string& lower, const string& upper)
{
    if (isFloat(value) == false || isFloat(lower) == false || isFloat(upper) == false)
    {
        return false;
    }
    float v = std::stof(value);
    float low = std::stof(lower);
    float high = std::stof(upper);
    return (low <= v && v <= high);
}

template <class Type>
bool findElement(vector<Type> v, Type& entry)
{
    auto it = find_if(v.begin(), v.end(), [&entry](Type& obj) { return obj == entry; } );
    return (it != v.end());
}

string getHostIP()
{
    char buffer[INET_ADDRSTRLEN];
    string host_ip;

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock == -1)
    {
        LOG(error) << "Failed to create socket" << endl;
        return host_ip;
    }

    const char* kGoogleDnsIp = "8.8.8.8";
    uint16_t kDnsPort = 53;
    struct sockaddr_in serv;
    memset(&serv, 0, sizeof(serv));
    serv.sin_family = AF_INET;
    serv.sin_addr.s_addr = inet_addr(kGoogleDnsIp);
    serv.sin_port = htons(kDnsPort);

    if (connect(sock, (const sockaddr*) &serv, sizeof(serv)) == -1)
    {
        LOG(error) << "Failed to socket connect" << endl;
        close(sock);
        return host_ip;
    }

    sockaddr_in name;
    socklen_t namelen = sizeof(name);
    if (getsockname(sock, (sockaddr*) &name, &namelen) == -1)
    {
        LOG(error) << "Failed to get socket name" << endl;
        close(sock);
        return host_ip;
    }

    const char* p = inet_ntop(AF_INET, &name.sin_addr, buffer, sizeof(buffer));
    if (p)
    {
        host_ip = string(p);
    }
    close(sock);
    return host_ip;
}

Json::Value loadVmsConfig()
{
    Json::Value config;
    Json::Reader reader;
    std::ifstream file((GET_CMDLINE_PARSER()->getVmsConfigFilePath()).c_str());
    if(file.good())
    {
        reader.parse(file, config, true);
    }
    return config;
}

Json::Value loadStorageConfig(const string& storage_config_file_path)
{
    Json::Value config;
    Json::Reader reader;
    std::ifstream file(storage_config_file_path.c_str());
    if(file.good())
    {
        reader.parse(file, config, true);
    }
    return config;
}

Json::Value scanCameraBackList()
{
    Json::Value backlist;
    Json::Reader reader;
    std::ifstream file((GET_CMDLINE_PARSER()->getCameraBackListFilePath()).c_str());
    if(file.good())
    {
        reader.parse(file, backlist, true);
    }
    return backlist;
}

Json::Value getAdaptorInfo()
{
    Json::Value config;
    Json::Reader reader;
    std::ifstream file((GET_CMDLINE_PARSER()->getAdaptorConfigFilePath()).c_str());
    if(file.good())
    {
        reader.parse(file, config, true);
    }
    else
    {
        LOG(error) << "Could not parse VMS Config file " << endl;
    }

    return config;
}

string getMediaAdaptorLibPath()
{
    Json::Value config = getAdaptorInfo();
    auto server_array = config["vst"];

    string adaptor_str;
    char *env = getenv("ADAPTOR");
    if (env != nullptr)
    {
        adaptor_str = string(env);
    }

    /* Find-out which vst adaptor to use */
    unsigned int adaptor_index_to_use = 0;
    if (!adaptor_str.empty())
    {
        for (unsigned int i = 0; i < server_array.size(); i++)
        {
            Json::Value info = server_array[i];
            string adaptor_name = info.get("name", "").asString();
            if (adaptor_name == adaptor_str)
            {
                adaptor_index_to_use = i;
                break;
            }
        }
    }
    else
    {
        for (unsigned int i = 0; i < server_array.size(); i++)
        {
            Json::Value info = server_array[i];
            bool enabled = info.get("enabled", false).asBool();
            if (enabled)
            {
                adaptor_index_to_use = i;
                break;
            }
        }
    }

    Json::Value info = server_array[adaptor_index_to_use];
    string media_adaptor_lib_path = info.get("media_adaptor_lib_path", "").asString();
    
    return media_adaptor_lib_path;
}

Json::Value getOnvifInfo()
{
    Json::Value config;
    Json::Reader reader;
    LOG(verbose) <<"Get Onvif info" << endl;
    std::ifstream file((GET_CMDLINE_PARSER()->getOnvifFilePath()).c_str());
    if(file.good())
    {
        //file >> config;
        if(!reader.parse(file, config, true)){
                //for some reason it always fails to parse
            //std::cout  << "Failed to parse configuration\n"
            //         << reader.getFormattedErrorMessages();
        }
    }
    return config;
}

Json::Value readDeviceDetails()
{
    Json::Value config;
    Json::Reader reader;
    LOG(verbose) <<"Get Onvif info" << endl;
    std::ifstream file((GET_CMDLINE_PARSER()->getDeviceDetailsFilePath()).c_str());
    if(file.good())
    {
        //file >> config;
        if(!reader.parse(file, config, true)){
                //for some reason it always fails to parse
            //std::cout  << "Failed to parse configuration\n"
            //         << reader.getFormattedErrorMessages();
        }
    }
    return config;
}


string className(const string& prettyFunction)
{
    size_t colons = prettyFunction.find("::");
    if (colons == std::string::npos)
        return "::";
    size_t begin = prettyFunction.substr(0,colons).rfind(" ") + 1;
    size_t end = colons - begin;

    return prettyFunction.substr(begin, end);
}

string methodName(const string& prettyFunction)
{
    size_t colons = prettyFunction.find("::");
    size_t begin = prettyFunction.substr(0,colons).rfind(" ") + 1;
    size_t end = prettyFunction.rfind("(") - begin;

    return prettyFunction.substr(begin, end) + "()";
}

inline int ParseInt(const char* value)
{
    return std::strtol(value, nullptr, 10);
}

std::time_t getEpocTime(const std::string input)
{
    std::tm tm = {};
    std::time_t epoc = 0;

    // Parse the input string using strptime
    const char* result = ::strptime(input.c_str(), "%Y%m%dT%H%M%S", &tm);
    if (result != nullptr)
    {
        // Convert parsed time to time since epoch
        std::time_t timeInSeconds = std::mktime(&tm) - timezone;

        // Convert to a time_point
        auto time_point = std::chrono::system_clock::from_time_t(timeInSeconds);

        // Check for fractional seconds (e.g., ".123" in "20231005T123456.123")
        double fractionalSeconds = 0.0;
        const char* fractionStart = strchr(result, '.');
        if (fractionStart != nullptr)
        {
            fractionalSeconds = std::stod(fractionStart); // Parse fractional part
        }

        // Convert to microseconds and add fractional part
        epoc = std::chrono::duration_cast<std::chrono::microseconds>(
                   time_point.time_since_epoch())
                   .count();
        epoc += static_cast<std::time_t>(fractionalSeconds * 1'000'000); // Add fractional microseconds
    }

    return epoc; // Return epoch time in seconds
}

std::time_t getEpocTimeInMS(const std::string input, bool isISOTime /*true*/)
{
    std::tm tm = {};
    std::time_t epoc = 0;

    // Parse the input string based on the format
    const char* result = nullptr;
    if (isISOTime)
    {
        result = ::strptime(input.c_str(), "%Y-%m-%dT%H:%M:%S", &tm);
    }
    else
    {
        result = ::strptime(input.c_str(), "%Y%m%dT%H%M%S", &tm);
    }

    if (result != nullptr)
    {
        // Handle timezone offset if necessary
        std::time_t timeInSeconds = std::mktime(&tm) - timezone;

        // Convert to a time_point
        auto time_point = std::chrono::system_clock::from_time_t(timeInSeconds);

        // Check for fractional seconds (e.g., ".123" in "2023-10-05T12:34:56.123")
        double fractionalSeconds = 0.0;
        const char* fractionStart = strchr(input.c_str(), '.');
        if (fractionStart != nullptr)
        {
            fractionalSeconds = std::stod(fractionStart); // Parse fractional part
        }

        // Convert to milliseconds
        epoc = std::chrono::duration_cast<std::chrono::milliseconds>(
                   time_point.time_since_epoch())
                   .count();

        // Add fractional part in milliseconds
        epoc += static_cast<std::time_t>(fractionalSeconds * 1000);
    }

    return epoc;
}

int64_t parseTimeToEpochMs(const string& timeStr)
{
    if (timeStr.empty())
    {
        return 0;
    }

    if (timeStr.rfind("ms:", 0) == 0)
    {
        try
        {
            return std::stoll(timeStr.substr(3));
        }
        catch (const std::invalid_argument& e)
        {
            LOG(error) << "Failed to parse ms-prefixed time: " << timeStr << " error: " << e.what() << endl;
            return 0;
        }
        catch (const std::out_of_range& e)
        {
            LOG(error) << "Failed to parse ms-prefixed time: " << timeStr << " error: " << e.what() << endl;
            return 0;
        }
    }

    return static_cast<int64_t>(getEpocTimeInMS(timeStr));
}

int64_t getDuration(const string startTime, const string endTime)
{
    std::time_t start =  getEpocTime(startTime);
    std::time_t end =  getEpocTime(endTime);
    if ((start == 0 && end == 0) || end == 0)
    {
        return 0;
    }
    return end - start + 1;
}

const std::string convertEpocToISO8601 (int64_t epoch64)
{
    std::tm tm_buf{};
    int64_t ts = epoch64 / 1000000;
    if (!gmtime_r(&ts, &tm_buf))
    {
        LOG(error) << "convertEpocToISO8601: gmtime_r failed for epoch=" << epoch64 << endl;
        return "";
    }
    int64_t millisecond_time = (epoch64 % 1000000) / 1000;
    std::ostringstream oss;
    oss << std::put_time(&tm_buf, "%Y%m%dT%H%M%S")
        << "." << std::setfill('0') << std::setw(3) << millisecond_time;
    return oss.str();
}

// epochs64 is in microseconds
const std::string convertEpocToISO8601_2 (int64_t epoch64)
{
    std::tm tm_buf{};
    int64_t ts = epoch64 / 1000000;
    if (!gmtime_r(&ts, &tm_buf))
    {
        LOG(error) << "convertEpocToISO8601_2: gmtime_r failed for epoch=" << epoch64 << endl;
        return "";
    }
    int64_t millisecond_time = (epoch64 % 1000000) / 1000;
    std::ostringstream oss;
    oss << std::put_time(&tm_buf, "%Y-%m-%dT%H:%M:%S")
        << "." << std::setfill('0') << std::setw(3) << millisecond_time << "Z";
    return oss.str();
}

const std::string convertEpocNsToISO8601(uint64_t epoch_ns)
{
    int64_t ts_seconds = epoch_ns / 1000000000; // seconds
    int64_t ts_microseconds = (epoch_ns % 1000000000) / 1000; // microseconds
    std::tm tm_buf{};
    if (!gmtime_r(&ts_seconds, &tm_buf))
    {
        LOG(error) << "convertEpocNsToISO8601: gmtime_r failed for epoch_ns=" << epoch_ns << endl;
        return "";
    }

    std::ostringstream oss;
    oss << std::put_time(&tm_buf, "%Y-%m-%dT%H:%M:%S")
        << "." << ts_microseconds << "Z";
    return oss.str();
}

const string convertEpocToHumanTime(int64_t epoch64)
{
    //std::cout << epoch64 << "\n";
    epoch64 /= 1000;
    // this goes ok (if epoch64 is big enough), since /1000 makes it
    // small enough to go into time_t
    time_t t = epoch64;
    std::string ctime_buf(26, '\0');
    return std::string(ctime_r(&t, ctime_buf.data()));
}

std::string timeStampToHReadble(const time_t rawtime)
{
    struct tm dt;
    if (localtime_r(&rawtime, &dt) == nullptr)
        return std::string();
    std::ostringstream oss;
    oss << std::put_time(&dt, "%m%d%H%M%y");
    return oss.str();
}

static std::mutex g_getTimeLock;
const std::string getCurrentTime()
{
    std::lock_guard<std::mutex> devicesLock(g_getTimeLock);
    time_t     now = time(nullptr);
    struct tm  tstruct;
    if (localtime_r(&now, &tstruct) == nullptr)
        return {};
    std::ostringstream oss;
    oss << std::put_time(&tstruct, "%Y-%m-%dT%XZ");
    return oss.str();
}

const std::string getCurrentUtcTime()
{
    time_t now = time(nullptr);
    if (tm gmtm{}; gmtime_r(&now, &gmtm) != nullptr)
    {
        std::ostringstream oss;
        oss << std::put_time(&gmtm, "%Y-%m-%dT%XZ");
        return oss.str();
    }
    LOG(error) << "Failed to get the UTC date and time" << endl;
    return std::string();
}

const std::string getOffsetUtcTime(int milliseconds)
{
    auto time = std::chrono::system_clock::now() + std::chrono::milliseconds(milliseconds);
    time_t tnow = std::chrono::system_clock::to_time_t(time);

    if (tm gmtm{}; gmtime_r(&tnow, &gmtm) != nullptr)
    {
        std::ostringstream oss;
        oss << std::put_time(&gmtm, "%Y-%m-%dT%XZ");
        return oss.str();
    }
    LOG(error) << "Failed to get the UTC date and time" << endl;
    return std::string();
}

const string getCurrentTimeMS()
{
    string t;
    std::lock_guard<std::mutex> devicesLock(g_getTimeLock);
    auto now = std::chrono::system_clock::now();
    auto seconds = std::chrono::time_point_cast<std::chrono::seconds>(now);
    auto fraction = now - seconds;
    auto milliseconds = std::chrono::duration_cast<std::chrono::milliseconds>(fraction);

    time_t tnow = std::chrono::system_clock::to_time_t(now);
    tm ptm{};
    if (gmtime_r(&tnow, &ptm) == nullptr)
        return {};

    t = std::to_string(ptm.tm_year + 1900);
    t = t + string("-") + std::to_string(ptm.tm_mon + 1);
    t = t + string("-") + std::to_string(ptm.tm_mday);
    t = t + string("T");
    t = t + std::to_string(ptm.tm_hour);
    t = t + string(":") +  std::to_string(ptm.tm_min);
    t = t + string(":") +  std::to_string(ptm.tm_sec);
    t = t + string(".") +  std::to_string(static_cast<int>(milliseconds.count()));
    t = t + string("Z");

    return t;
}

std::tuple<std::string, std::string, std::string> getCurrentTimeInHHMMSS()
{
    auto now = std::chrono::system_clock::now();
    std::time_t currentTime = std::chrono::system_clock::to_time_t(now);
    std::tm utcTime{};
    if (gmtime_r(&currentTime, &utcTime) == nullptr)
        return std::make_tuple(std::string("00"), std::string("00"), std::string("00"));

    int hours = utcTime.tm_hour;
    int minutes = utcTime.tm_min;
    int seconds = utcTime.tm_sec;

    // Validate and handle rollover - seconds should never be >= 60
    if (seconds >= 60) {
        seconds = 0;
        minutes++;
        if (minutes >= 60) {
            minutes = 0;
            hours++;
            if (hours >= 24) {
                hours = 0;
            }
        }
    }

    // Convert hours, minutes, and seconds to strings
    std::string hoursStr = std::to_string(hours);
    std::string minutesStr = std::to_string(minutes);
    std::string secondsStr = std::to_string(seconds);

    // Pad single-digit values with leading zeros
    if (hours < 10) hoursStr = "0" + hoursStr;
    if (minutes < 10) minutesStr = "0" + minutesStr;
    if (seconds < 10) secondsStr = "0" + secondsStr;

    return std::make_tuple(hoursStr, minutesStr, secondsStr);
}

std::tuple<std::string, std::string, std::string> getCurrentDateInDDMMYYYY()
{
  // Get the current system time with compensation
  std::chrono::system_clock::time_point now = std::chrono::system_clock::now();
  std::time_t currentTime = std::chrono::system_clock::to_time_t(now);

  std::tm utcTime{};
  if (gmtime_r(&currentTime, &utcTime) == nullptr)
      return std::make_tuple(std::string("1970"), std::string("01"), std::string("01"));

  int year = utcTime.tm_year + 1900;
  int month = utcTime.tm_mon + 1;    // tm_mon is 0-based
  int day = utcTime.tm_mday;

  // Convert year, month, and day to strings
  std::string yearStr = std::to_string(year);
  std::string monthStr = std::to_string(month);
  std::string dayStr = std::to_string(day);

  // Pad single-digit values with leading zeros
  if (month < 10) monthStr = "0" + monthStr;
  if (day < 10) dayStr = "0" + dayStr;

  return std::make_tuple(dayStr, monthStr, yearStr);
}

static int stringToint(const std::string& str)
{
    int result = 0;
    for (char ch : str)
    {
        if (ch < '0' || ch > '9')
        {
            LOG(error) << "Invalid character in numeric string " << str << endl;
            return 0;
        }
        result = result * 10 + (ch - '0');
    }
    return result;
}

int64_t convertStringToSeconds(const std::string& str_time /* HH:MM:SS */)
{
    if (str_time.empty())
        return 0;

    // Regular expression to match HH:MM:SS or HH:MM:SS.mmm
    std::regex time_regex(R"((\d+):(\d+):(\d+)(?:\.(\d+))?)");
    std::smatch match;

    int h = 0, m = 0, s = 0, ms = 0;

    if (std::regex_match(str_time, match, time_regex)) {
        // Extract hours, minutes, seconds, and optional milliseconds
        h = stringToint(match[1].str());
        m = stringToint(match[2].str());
        s = stringToint(match[3].str());
        if (match[4].matched)
        {
            // Check if milliseconds are present
            ms = stringToint(match[4].str());
        }
    }

    // Calculate total seconds and milliseconds
    int64_t total_seconds = h * 3600 + m * 60 + s;
    int64_t total_ms = total_seconds * 1000 + ms;

    return total_ms;
}

string getRelativeTimeUsingFrameId(const int64_t& frameId, const double& framerate)
{
    char timeString[256];
    string hhmmss;
    int64_t h, m, s;

    if (frameId < 0 || framerate <= 0)
    {
        return hhmmss;
    }
    int64_t total_milliseconds = frameId * (1000/framerate);
    int64_t seconds = total_milliseconds / 1000;
    int64_t millisec = total_milliseconds % 1000;

    h = (seconds/3600);
    m = (seconds -(3600*h))/60;
    s = (seconds -(3600*h)-(m*60));
    snprintf(timeString, sizeof(timeString), "%02ld:%02ld:%02ld.%03ld", h, m, s, millisec);
    hhmmss = timeString;
    return hhmmss;
}

int64_t getFrameIdUsingRelativeTime(const string& str_time, const double& framerate)
{
    int64_t frameId = -1;
    if (str_time.empty() || framerate <= 0)
    {
        return frameId;
    }
    int64_t total_ms = convertStringToSeconds(str_time);
    frameId = (total_ms / (1000.00/framerate)) + 0.5;
    return frameId;
}

void posixTimeResolutionScale(float& secScale, float& msScale, float& usScale, float& nsScale)
{
    // Get posix_time resolution
    boost::date_time::time_resolutions res = boost::posix_time::time_duration::resolution();
    switch (res)
    {
        case boost::date_time::time_resolutions::sec:
            secScale = 1;
            msScale = 1e-3;
            usScale = 1e-6;
            nsScale = 1e-9;
            break;
        case boost::date_time::time_resolutions::tenth:
            secScale = 1e+1;
            msScale = 1e-2;
            usScale = 1e-5;
            nsScale = 1e-8;
            break;
        case boost::date_time::time_resolutions::hundredth :
            secScale = 1e+2;
            msScale = 1e-1;
            usScale = 1e-4;
            nsScale = 1e-7;
            break;
        case boost::date_time::time_resolutions::milli:
            secScale = 1e+3;
            msScale = 1;
            usScale = 1e-3;
            nsScale = 1e-6;
            break;
        case boost::date_time::time_resolutions::ten_thousandth:
            secScale = 1e+4;
            msScale = 1e+1;
            usScale = 1e-2;
            nsScale = 1e-5;
            break;
        case boost::date_time::time_resolutions::micro:
            secScale = 1e+6;
            msScale = 1e+3;
            usScale = 1;
            nsScale = 1e-3;
            break;
        case boost::date_time::time_resolutions::nano:
            secScale = 1e+9;
            msScale = 1e-6;
            usScale = 1e-3;
            nsScale = 1;
            break;
        default:
            LOG(error) << "Unsupported POSIX time resolution." << endl;
            break;
    }

    return;
}

// str_time is in format of %Y-%m-%dT%H:%M:%S.%fZ.
// %f represents milliseconds.
boost::posix_time::ptime stringToPosixTime(const std::string& str_time, float msScale)
{
    // https://stackoverflow.com/questions/25393683/stdstring-to-stdchrono-time-point
    std::string tp_str;
    std::tm tm = {0};

    // // %Y - [0, 60] since 1900
    // tp_str = str_time.substr(0,4);
    // tm.tm_year = stoi(tp_str);
    // tm.tm_year -= 1900;

    // // %m - [0, 11] since January
    // tp_str = str_time.substr(5,2);
    // tm.tm_mon = stoi(tp_str);
    // tm.tm_mon -= 1;

    // // %d - [1, 31] day
    // tp_str = str_time.substr(8,2);
    // tm.tm_mday = stoi(tp_str);

    // // %H - [0, 23] since midnight
    // tp_str = str_time.substr(11,2);
    // tm.tm_hour = stoi(tp_str);

    // // %M - [0, 59] after the hour
    // tp_str = str_time.substr(14,2);
    // tm.tm_min = stoi(tp_str);

    // // %S - [0, 59] after the minute
    // tp_str = str_time.substr(17,2);
    // tm.tm_sec = stoi(tp_str);

    // std::time_t tt = std::mktime(&tm);

    // %f - [0, 999] milliseconds after the sec
    tp_str = str_time.substr(20, str_time.length() - 1 - 20);
    int ms = stoi(tp_str);

    // // std::chrono::system_clock::time_point tp;
    // // tp = std::chrono::system_clock::from_time_t(tt) + std::chrono::microseconds{usec};

    // // // Convert "%Y-%m-%dT%H:%M:%S.%fZ" to "%Y-%m-%d %H:%M:%S.%f". %f in microseconds
    // // std::string ts = str_time;
    // // ts[10] = ' ';
    // // ts = ts.substr(0, ts.length()-1);// drop the last 'Z'
    // // // https://www.boost.org/doc/libs/1_55_0/doc/html/date_time/posix_time.html
    // // boost::posix_time::ptime pt(boost::posix_time::time_from_string(ts));
    // // boost::posix_time::ptime pt(boost::posix_time::date(tm.tm_year, tm.tm_mon, tm.tm_mday),
    // //     boost::posix_time::time_duration(tm.tm_hour, tm.tm_min, tm.tm_sec, usec));
    // boost::posix_time::ptime pt = boost::posix_time::from_time_t(tt);
    // float secScale, msScale, usScale, nsScale;
    // posixTimeResolutionScale(secScale, msScale, usScale, nsScale);
    // pt += boost::posix_time::time_duration(0, 0, 0, int(ms * msScale));

    boost::posix_time::ptime pt;
    if (strptime(str_time.c_str(), "%Y-%m-%dT%H:%M:%S", &tm))
    {
        std::time_t tt = std::mktime(&tm);
        pt = boost::posix_time::from_time_t(tt);
        pt += boost::posix_time::time_duration(0, 0, 0, int(ms * msScale));
    }
    else
    {
        LOG(error) << "Failed to parse " << str_time << endl;
    }

    return pt;
}

const std::string posixTimeToString(const boost::posix_time::ptime& pt)
{
    // https://boost.sourceforge.net/regression-logs/cs-win32_metacomm/doc/html/date_time.posix_time.html#ptime_to_string
    // '2002-01-31T10:00:01,123456789'
    std::string str_time = boost::posix_time::to_iso_extended_string(pt);

    // Replace ',' with '.'.
    str_time[19] = '.';

    // Replace '123456789' with '123Z'
    str_time[23] = 'Z';

    // '2002-01-31T10:00:01.123Z'
    str_time.resize(24);

    return str_time;
}

string generate_uuid()
{
    uuid_t uuid;
    uuid_generate_random ( uuid );
    char uid[37];
    uuid_unparse ( uuid, uid );
    return string(uid);
}

// Convert ISO timestamp to readable format without delimiters
string convertUTCToHumanReadableFormat(const string& utcTimeStr)
{
    // lambda to generate current time string
    auto getCurrentTimeString = []() -> string {
        auto now = std::chrono::system_clock::now();
        auto time_t_now = std::chrono::system_clock::to_time_t(now);
        std::tm tm_buf{};
        if (gmtime_r(&time_t_now, &tm_buf) == nullptr)
            return std::string();
        std::stringstream ss;
        ss << std::put_time(&tm_buf, "%Y%m%d%H%M%S");
        return ss.str();
    };
    
    try
    {
        // Parse using existing isoToEpoch function and convert back to tm structure
        std::time_t epochMs = isoToEpoch(utcTimeStr);
        std::time_t epochSec = epochMs / 1000;
        std::tm tm_buf{};
        const std::tm* tm = gmtime_r(&epochSec, &tm_buf);
        
        if (!validateISOTime(utcTimeStr) ||
            (epochMs == 0 && utcTimeStr != ZERO_EPOCH_TIME) ||
            tm == nullptr)
        {
            return getCurrentTimeString();
        }
        
        std::stringstream result;
        result << std::put_time(tm, "%Y%m%d%H%M%S");
        return result.str();
    }
    catch (...)
    {
        // Return current time on any error
        return getCurrentTimeString();
    }
}

// Generate a user-friendly ID: prefix_YYYYMMDD_HHMMSS_XXXX
string getUniqueIdFromUTCTime(const string& utcTimeStr, const string& prefix)
{
    // Helper lambda to validate prefix and return UUID on failure
    auto validatePrefix = [](const string& prefix) -> bool {
        if (prefix.length() > 64) {
            LOG(warning) << "Prefix too long: " << prefix.length() << " chars. Using UUID fallback." << endl;
            return false;
        }
        
        // Check for invalid characters (allow alphanumeric, underscore, hyphen)
        for (char c : prefix) {
            if (!std::isalnum(c) && c != UNDERSCORE_CHAR && c != '-') {
                LOG(warning) << "Invalid character in prefix: '" << c << "'. Using UUID fallback." << endl;
                return false;
            }
        }
        return true;
    };
    
    try
    {
        // Validate prefix if provided
        if (!prefix.empty() && !validatePrefix(prefix)) {
            return generate_uuid();
        }
        
        string dateTimePart = convertUTCToHumanReadableFormat(utcTimeStr);
        string result;
        
        // Add prefix with underscore if needed
        if (!prefix.empty()) {
            result = prefix + (prefix.back() != UNDERSCORE_CHAR ? UNDERSCORE_STR : EMPTY_STRING);
        }
        
        // Format: [prefix_]YYYYMMDD_HHMMSS_XXXXX
        result += dateTimePart.substr(0, 8) + UNDERSCORE_STR +     // YYYYMMDD_
                  dateTimePart.substr(8, 6) + UNDERSCORE_STR +     // HHMMSS_
                  generate_uuid().substr(0, 5);                    // XXXXX
        
        return result;
    }
    catch (...)
    {
        return generate_uuid();
    }
}

string sanitizeTimestampForFilename(const string& timeStr)
{
    string sanitized = timeStr;
    for (char& c : sanitized) {
        if (c == ':' || c == '.' || c == 'T' || c == 'Z') {
            c = '_';
        }
    }
    return sanitized;
}

string sanitizePrefix(const string& raw)
{
    string sanitized;
    sanitized.reserve(raw.size());
    for (char c : raw)
    {
        if (std::isalnum(static_cast<unsigned char>(c)) || c == UNDERSCORE_CHAR || c == HYPHEN_CHAR)
        {
            sanitized.push_back(c);
        }
        else if (c == WHITESPACE_CHAR)
        {
            sanitized.push_back(UNDERSCORE_CHAR);
        }
    }
    // Trim leading/trailing underscores or hyphens
    if (!sanitized.empty())
    {
        const string trimChars = string(1, UNDERSCORE_CHAR) + string(1, HYPHEN_CHAR);
        size_t start = sanitized.find_first_not_of(trimChars);
        if (start == string::npos)
        {
            sanitized.clear();
        }
        else
        {
            size_t end = sanitized.find_last_not_of(trimChars);
            sanitized = sanitized.substr(start, end - start + 1);
        }
    }
    return sanitized;
}

std::pair<string, string> getCameraErrorCodeString(VmsErrorCode code)
{
    switch((int)code)
    {
        case NoError: return std::make_pair("NoError", "No Error");
        case CameraUnauthorizedError: return std::make_pair("CameraUnauthorizedError", "Camera is not authorized");
        case ClientUnauthorizedError: return std::make_pair("ClientUnauthorizedError","Client is not authorized");
        case InvalidParameterError: return std::make_pair("InvalidParameterError", "Invalid or out of range parameters");
        case CameraNotFoundError: return std::make_pair("CameraNotFoundError", "Camera not found OR camera id is not valid");
        case CommunicationError: return std::make_pair("CommunicationError", "Camera communication error");
        case VMSNotSupportedError: return std::make_pair("VMSNotSupportedError", "Operation/Action not supported");
        case MethodNotAllowedError: return std::make_pair("MethodNotAllowedError", "Method Not Allowed");
        case DeviceRequestTimeoutError: return std::make_pair("DeviceRequestTimeoutError", "Request Timout");
        case VMSInsufficientStorage: return std::make_pair("VMSInsufficientStorage", "Insufficient Storage");
        case VMSNoDataError: return std::make_pair("VMSNoDataError", "No valid streams found for the given timestamps");
        case ResourceConflictError: return std::make_pair("ResourceConflictError", "Resource Conflict");
        case PayloadTooLargeError: return std::make_pair("PayloadTooLargeError", "Payload Too Large");
        case UnsupportedMediaTypeError: return std::make_pair("UnsupportedMediaTypeError", "Unsupported Media Type");
        case UnprocessableEntityError: return std::make_pair("UnprocessableEntityError", "Unprocessable Entity");
        case TooManyRequestsError: return std::make_pair("TooManyRequestsError", "Too Many Requests");
        case ServiceUnavailableError: return std::make_pair("ServiceUnavailableError", "Service Unavailable");
        default: return std::make_pair("VMSInternalError","VMS internal processing error");
    }
}


VmsErrorCode getCameraErrorCode(const string& error)
{
    if(error == "NoError")
    {
        return VmsErrorCode::NoError;
    }
    if(error == "CameraUnauthorizedError")
    {
        return VmsErrorCode::CameraUnauthorizedError;
    }
    if(error == "ClientUnauthorizedError")
    {
        return VmsErrorCode::ClientUnauthorizedError;
    }
    if(error == "InvalidParameterError")
    {
        return VmsErrorCode::InvalidParameterError;
    }
    if(error == "CameraNotFoundError")
    {
        return VmsErrorCode::CameraNotFoundError;
    }
    if(error == "CommunicationError")
    {
        return VmsErrorCode::CommunicationError;
    }
    if(error == "VMSNotSupportedError")
    {
        return VmsErrorCode::VMSNotSupportedError;
    }
    if(error == "MethodNotAllowedError")
    {
        return VmsErrorCode::MethodNotAllowedError;
    }
    if(error == "DeviceRequestTimeoutError")
    {
        return VmsErrorCode::DeviceRequestTimeoutError;
    }
    if(error == "VMSInsufficientStorage")
    {
        return VmsErrorCode::VMSInsufficientStorage;
    }
    if(error == "ResourceConflictError")
    {
        return VmsErrorCode::ResourceConflictError;
    }
    if(error == "PayloadTooLargeError")
    {
        return VmsErrorCode::PayloadTooLargeError;
    }
    if(error == "UnsupportedMediaTypeError")
    {
        return VmsErrorCode::UnsupportedMediaTypeError;
    }
    if(error == "UnprocessableEntityError")
    {
        return VmsErrorCode::UnprocessableEntityError;
    }
    if(error == "TooManyRequestsError")
    {
        return VmsErrorCode::TooManyRequestsError;
    }
    if(error == "ServiceUnavailableError")
    {
        return VmsErrorCode::ServiceUnavailableError;
    }
    return VmsErrorCode::VMSInternalError;
}

std::pair<int, string> translateVmsErrorCodeToCameraHttpErrorCode(VmsErrorCode code)
{
    switch((int)code)
    {
        case NoError: return std::make_pair(200, "OK");
        case CameraUnauthorizedError: return std::make_pair(403, "Forbidden");
        case ClientUnauthorizedError: return std::make_pair(401, "Unauthorized");
        case InvalidParameterError: return std::make_pair(400, "Bad Request");
        case CameraNotFoundError: return std::make_pair(404,"Not Found");
        case MethodNotAllowedError: return std::make_pair(405, "Method Not Allowed");
        case DeviceRequestTimeoutError: return std::make_pair(408, "Request Timout");
        case VMSNotSupportedError: return std::make_pair(501, "Not Implemented");
        case VMSInsufficientStorage: return std::make_pair(507, "Insufficient Storage");
        case VMSNoDataError: return std::make_pair(404, "Not Found");
        case ResourceConflictError: return std::make_pair(409, "Conflict");
        case PayloadTooLargeError: return std::make_pair(413, "Payload Too Large");
        case UnsupportedMediaTypeError: return std::make_pair(415, "Unsupported Media Type");
        case UnprocessableEntityError: return std::make_pair(422, "Unprocessable Entity");
        case TooManyRequestsError: return std::make_pair(429, "Too Many Requests");
        case ServiceUnavailableError: return std::make_pair(503, "Service Unavailable");
        case CommunicationError:
        default: return std::make_pair(500, "Internal Server Error");
    }
}

VmsErrorCode translateCameraHttpErrorCodeToVmsErrorCode(int code)
{
    switch(code)
    {
        case 200: return VmsErrorCode::NoError;
        case 400: return VmsErrorCode::CameraUnauthorizedError;
        case 401: return VmsErrorCode::CameraUnauthorizedError;
        case 403: return VmsErrorCode::CameraUnauthorizedError;
        case 404: return VmsErrorCode::CameraNotFoundError;
        case 405: return VmsErrorCode::MethodNotAllowedError;
        case 408: return VmsErrorCode::DeviceRequestTimeoutError;
        case 501: return VmsErrorCode::VMSNotSupportedError;
        case 507: return VmsErrorCode::VMSInsufficientStorage;
        case 409: return VmsErrorCode::ResourceConflictError;
        case 413: return VmsErrorCode::PayloadTooLargeError;
        case 415: return VmsErrorCode::UnsupportedMediaTypeError;
        case 422: return VmsErrorCode::UnprocessableEntityError;
        case 429: return VmsErrorCode::TooManyRequestsError;
        case 503: return VmsErrorCode::ServiceUnavailableError;
        default: return VmsErrorCode::VMSInternalError;
    }
}

string translateStreamStatusToString(StreamStatus value)
{
    switch(value)
    {
        case STREAM_STATUS_ONLINE: return "stream_online";
        case STREAM_STATUS_OFFLINE: return "stream_offline";
        case STREAM_STATUS_STREAMING: return "stream_streaming";
        case STREAM_STATUS_END_OF_STREAM: return "stream_end_of_stream";
        case STREAM_STATUS_REMOVED: return "stream_removed";
        case STREAM_STATUS_UNKNOWN:
        default: return "stream_status_unknown";
    }
}

StreamStatus stringToStreamStatus(const string& event)
{
    if (event == "stream_online") return STREAM_STATUS_ONLINE;
    if (event == "stream_offline") return STREAM_STATUS_OFFLINE;
    if (event == "stream_streaming") return STREAM_STATUS_STREAMING;
    if (event == "stream_end_of_stream") return STREAM_STATUS_END_OF_STREAM;
    if (event == "stream_removed") return STREAM_STATUS_REMOVED;
    return STREAM_STATUS_UNKNOWN;
}

/* return 1 if IP string is
valid, else return 0 */
bool validateIpAddress(const string &ipAddress)
{
    struct sockaddr_in sa;
    int result = inet_pton(AF_INET, ipAddress.c_str(), &(sa.sin_addr));
    return result == 1;
}

bool validateAndStripRtspUrl(string& url, string& ipAddress, string& user, string& pass)
{
    // Used boost::regex with extended and case-insensitive options for better performance
    boost::regex urlRegex("rtsp://(([^:@]+):([^:@]+)@)?([^:/]+)(:(\\d+))?(/.*)?",
                         boost::regex::extended | boost::regex::icase);
    boost::smatch match;

    try
    {
        if (!boost::regex_match(url, match, urlRegex))
        {
            LOG(error) << "URL does not match expected RTSP format" << endl;
            return false;
        }

        // Extract components from the regex match
        if (!match[2].str().empty())
        {
            user = match[2].str(); // Username (optional)
        }
        if (!match[3].str().empty())
        {
            pass = match[3].str(); // Password (optional)
        }
        ipAddress = match[4].str(); // IP or hostname
        string port = match[6].str(); // Port (optional)
        string path = match[7].str(); // Path (optional)

        // Validate or resolve the IP address
        if (!validateIpAddress(ipAddress))
        {
            struct addrinfo hints{};
            struct addrinfo *res = nullptr;
            hints.ai_family = AF_INET;
            hints.ai_socktype = SOCK_STREAM;
            if (int gai_err = getaddrinfo(ipAddress.c_str(), nullptr, &hints, &res); gai_err == 0 && res)
            {
                std::string resolvedBuf(INET_ADDRSTRLEN, '\0');
                const auto *addr = (const struct sockaddr_in *)res->ai_addr;
                const char *resolvedIp = inet_ntop(AF_INET, &addr->sin_addr, resolvedBuf.data(), INET_ADDRSTRLEN);
                freeaddrinfo(res);
                if (resolvedIp)
                {
                    ipAddress = resolvedIp;
                    // Update the URL with resolved IP
                    boost::regex hostRegex(match[4].str());
                    url = boost::regex_replace(url, hostRegex, resolvedIp);
                }
                else
                {
                    LOG(error) << "Failed to convert resolved IP address" << endl;
                    return false;
                }
            }
            else
            {
                LOG(error) << "Failed to resolve hostname: " << ipAddress << endl;
                return false;
            }
        }

        LOG(info) << "Parsed RTSP URL - user: " << maskSensitiveData(user, MaskType::USERNAME)
                 << " password: " << maskSensitiveData(pass, MaskType::PASSWORD)
                 << " ipAddress: " << ipAddress
                 << " port: " << port
                 << " path: " << path << endl;

        return validateIpAddress(ipAddress);
    }
    catch (const boost::regex_error& e)
    {
        LOG(error) << "Regex error: " << e.what() << endl;
        return false;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Error processing URL: " << e.what() << endl;
        return false;
    }
}

bool ping(const string& ip)
{
    string cmd;
    unsigned int retry = 3;
    if (ip.empty() && validateIpAddress(ip) == false)
    {
        return false;
    }
ping_retry:
    cmd = string("timeout 1 ping -c1 ") + ip.c_str() + string(" > /dev/null 2>&1");
    int ret = system(cmd.c_str());
    if (ret == 0)
    {
        return true;
    }
    else if (retry-- > 0)
    {
        goto ping_retry;
    }
    return false;
}

bool pingHostname(const string& dnsName)
{
    struct addrinfo hints{};
    struct addrinfo *res = nullptr;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    if (int gai_err = getaddrinfo(dnsName.c_str(), nullptr, &hints, &res); gai_err == 0 && res)
    {
        std::string ipBuf(INET_ADDRSTRLEN, '\0');
        const auto *addr = (const struct sockaddr_in *)res->ai_addr;
        inet_ntop(AF_INET, &addr->sin_addr, ipBuf.data(), INET_ADDRSTRLEN);
        freeaddrinfo(res);
        return ping(ipBuf);
    }
    return false;
}

std::string getIPaddress(const std::string &url)
{
    // Regular expression to match IP address in the URL for both http and https
    std::regex ipPattern(R"(https?://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}))");
    std::smatch matches;

    if (std::regex_search(url, matches, ipPattern))
    {
        return matches[1].str();
    }

    return "";
}

int getPrefixLength(const string& netmask)
{
    int ret = 0;
    int count_ones = 0;
    std::uint8_t byte;
    std::uint8_t buf[sizeof(struct in_addr)];

    ret = inet_pton(AF_INET, netmask.c_str(), &buf);
    if (ret < 0)
    {
        LOG(error) <<  "Error in inet_pton " << endl;
        perror("inet_pton");
    }
    for (unsigned int i = 0; i < sizeof(struct in_addr); i++)
    {
        byte = buf[i];
        for (int j = 0; j < 8; j++)
        {
            count_ones += (byte & 1);
            byte >>= 1;
        }
    }
    LOG(info) << "netmask: " << netmask << ", prefix_len: " << count_ones << endl;
    return count_ones;
}

string getNetmaskFromPrefixLen(const int& prefixLength)
{
    string netMask;
    uint32_t fullMask = 0xFFFFFFFF;

    uint32_t maskedNumber = fullMask << (sizeof(fullMask)*8 - prefixLength);
    uint8_t *cptr = (uint8_t *)&maskedNumber;
    for (uint32_t i = 0; i < sizeof(maskedNumber); i++) {
        char cp[8];
        snprintf(cp, 8, "%d", *cptr);
        netMask = string(cp) + netMask;
        if(i < 3)
        {
            netMask = "." + netMask;
            cptr++;
        }
    }
    return netMask;
}

int runCMD(const string& cmd, string& result, bool strip_newline)
{
    int ret = 0;
    std::array<char, 128> buffer;
    FILE* raw_pipe = popen(cmd.c_str(), "r");
    if (!raw_pipe) 
    {
        LOG(error) <<  "Error in getting size of dir: " << endl;
        ret = -1;
    }
    else
    {
        while (fgets(buffer.data(), buffer.size(), raw_pipe) != nullptr)
        {
            result += buffer.data();
            if(strip_newline)
            {
                result.erase(std::remove(result.begin(), result.end(), '\n'), result.end());
            }
        }
        pclose(raw_pipe);
    }
    return ret;
}

void setRecvMaxSocketBufferSize (uint32_t socket_buffer_size)
{
    LOG(info) << "Setting OS's socket receive max buffer size to = " << socket_buffer_size << endl;
    std::string cmd = "sysctl net.core.rmem_max=" + to_string(socket_buffer_size);
    std::string output;
    if (runCMD (cmd, output) == -1)
    {
        LOG(error) << "Error Setting OS's internal recv receive buffer " << endl;
    }
}

void setSendMaxSocketBufferSize (uint32_t socket_buffer_size)
{
    LOG(info) << "Setting OS's socket send max buffer size to = " << socket_buffer_size << endl;
    std::string cmd = "sysctl net.core.wmem_max=" + to_string(socket_buffer_size);
    std::string output;
    if (runCMD (cmd, output) == -1)
    {
        LOG(error) << "Error Setting OS's internal send receive buffer " << endl;
    }
}

int getHostInfo(Json::Value& info)
{
    const std::string cmd = "hostnamectl status";
    std::string result;
    int ret = runCMD(cmd, result);
    if (ret == 0)
    {
        std::string line;
        std::istringstream f(result);
        std::regex lineRegex(R"(^\s*(.+?)\s*:\s*(.+?)\s*$)"); // Regex to match "key : value"
        std::smatch match;

        while (std::getline(f, line))
        {
            if (std::regex_match(line, match, lineRegex) && match.size() == 3)
            {
                std::string key = std::regex_replace(match[1].str(), std::regex("^ +| +$|( ) +"), "$1");
                std::string value = std::regex_replace(match[2].str(), std::regex("^ +| +$|( ) +"), "$1");
                info[key] = value;
            }
        }
    }
    return ret;
}

void resolveEnvironmentVariable(const string env, string& out)
{
    if (env.empty())
    {
        return;
    }
    char *env_value = getenv(env.c_str());
    if (env_value != nullptr)
    {
        out = string(env_value);
    }
    return;
}

std::time_t isoToEpoch(const std::string input, bool nanosec /*false*/)
{
    /* Converts standard ISO time like 2021-01-29T10:52:16.915Z to Timestamp */
    std::tm tm = {};
    std::time_t epoc = 0;

    // Parse the main date and time part
    const char* date = ::strptime(input.c_str(), "%Y-%m-%dT%H:%M:%S", &tm);
    if (date != nullptr)
    {
        // Compute the time since epoch in seconds
        auto time_point = std::chrono::system_clock::from_time_t(std::mktime(&tm) - timezone);

        // Extract fractional seconds (if present) after the '.' in ISO format
        std::string remaining(date);
        double fractional_seconds = 0.0;
        if (!remaining.empty() && remaining[0] == '.')
        {
            size_t endPos = remaining.find_first_not_of("0123456789", 1);
            std::string fractionPart = remaining.substr(1, endPos - 1);
            fractional_seconds = std::stod("0." + fractionPart);
        }

        // Convert to microseconds since epoch
        epoc = time_point.time_since_epoch() / std::chrono::microseconds(1) + static_cast<std::time_t>(fractional_seconds * 1000000.0);
    }

    if (nanosec)
    {
        return epoc;
    }
    return epoc / 1000; // Convert to milliseconds if nanosec is false
}

string getAbsolutePath(string rel_path)
{
    char resolved_path[PATH_MAX];
    char *res = realpath(rel_path.c_str(), resolved_path);
    if (res == nullptr)
    {
        perror("realpath");
        LOG(error) << "Failed to get absolute path" << endl;
        return rel_path;
    }
    return string(resolved_path);
}


Json::Value stringToJson(string in)
{
    Json::Value out;
    Json::CharReaderBuilder builder;
    Json::CharReader* reader = builder.newCharReader();
    std::string errors;

    reader->parse(in.c_str(),
                in.c_str() + in.size(),
                &out,
                &errors);
    delete reader;
    return out;
}

string stringToHex(const string& str, bool conver_to_upper_case)
{
    ostringstream ret;
    for (string::size_type i = 0; i < str.length(); ++i)
    {
        ret << std::hex << std::setfill('0') << std::setw(2) <<
        (conver_to_upper_case ? std::uppercase : std::nouppercase) << (int)str[i];
    }

    return ret.str();
}

std::vector<uint8_t> toBytes(const std::string& input)
{
    std::vector<uint8_t> bytes;
    for (const unsigned char c : input)
    {
        bytes.push_back(static_cast<uint8_t>(c));
    }
    return bytes;
}

std::string hexToString(const std::string& in)
{
    std::string output;
    if ((in.length() % 2) != 0)
    {
        LOG(error) << "string is not valid" << endl;
        return output;
    }

    size_t cnt = in.length() / 2;
    for (size_t i = 0; cnt > i; ++i)
    {
        uint32_t s = 0;
        std::stringstream ss;
        ss << std::hex << in.substr(i * 2, 2);
        ss >> s;

        output.push_back(static_cast<unsigned char>(s));
    }
    return output;
}

std::string getUTCtoLocalTime(string utcTime)
{
    std::time_t epochs = isoToEpoch(utcTime);
    epochs /= 1000;
    // Check for cron string
    if (epochs == 0)
    {
        return utcTime;
    }

    /*Convert UTC TIME To Local TIme*/
    struct tm tm;
    if (localtime_r(&epochs, &tm) == nullptr)
        return {};
    std::ostringstream oss;
    oss << std::put_time(&tm, "%Y-%m-%dT%H:%M:%S");
    return oss.str();
}

std::string getUTCtoLocalISOTime(std::time_t utcTime)
{
    struct tm  ts;
    int ms = utcTime % 1000000;
    utcTime = utcTime / 1000000;
    if (localtime_r(&utcTime, &ts) == nullptr)
        return {};
    std::ostringstream oss;
    oss << std::put_time(&ts, "%a %Y-%m-%d %H:%M:%S") << "." << ms;
    return oss.str();
}

vector<string> getNwInterfaceList()
{
  struct ifaddrs *ifaddr, *ifa;
  vector<string> list;
  if (getifaddrs(&ifaddr) == -1)
  {
    perror("getifaddrs");
    return list;
  }
  /* Walk through linked list, maintaining head pointer so we
     can free list later */
  for (ifa = ifaddr; ifa != nullptr; ifa = ifa->ifa_next)
  {
    if (ifa->ifa_addr == nullptr || ifa->ifa_addr->sa_family != AF_PACKET)
    {
        continue;
    }
    list.push_back(ifa->ifa_name);
  }
  freeifaddrs(ifaddr);
  return list;
}
static int64_t getBandwidth(const string& direction, const string& interface)
{
    int64_t value = 0;
    string cmd = string ("cat /sys/class/net/") + interface + string("/statistics/") + direction + string("_bytes");
    string result;
    int ret = runCMD(cmd, result);
    if (ret == 0)
    {
        try
        {
            value = std::stoll(result);
        }
        catch(const std::exception& e)
        {
            std::cerr << e.what() << '\n';
        }
    }
    return value;
}
Json::Value getBandwidth(string interfaces)
{
    Json::Value out = Json::arrayValue;
    vector<string> list = getNwInterfaceList();
    if (interfaces.empty())
    {
        interfaces = "eth0";
    }
    vector<string> nw_list = splitString(interfaces, ",");
    for (size_t i = 0; i < nw_list.size(); i++)
    {
        Json::Value nw_param;
        string interface = nw_list[i];
        eraseString(interface, " ");
        if (std::find(list.begin(), list.end(), interface) == list.end())
        {
            continue;
        }
        int64_t tx1 = getBandwidth("tx", interface);
        int64_t rx1 = getBandwidth("rx", interface);
        sleep(1);
        int64_t tx2 = getBandwidth("tx", interface);
        int64_t rx2 = getBandwidth("rx", interface);
        double tx_speed = (tx2 - tx1)/1000.0 * 8;
        double rx_speed = (rx2 - rx1)/1000.0 * 8;
        nw_param[interface]["tx_kbps"] = roundf(tx_speed);
        nw_param[interface]["rx_kbps"] = roundf(rx_speed);
        out.append(nw_param);
    }

    // Append network interface list
    Json::Value nw_param;
    std::ostringstream oss;
    std::copy(list.begin(), list.end() - 1,
    std::ostream_iterator<string>(oss, ","));
    // Now add the last element with no delimiter
    oss << list.back();
    nw_param["interfaceList"] = oss.str();
    out.append(nw_param);
    return out;
}

int stringToInt(const std::string& str, const int default_value)
{
    int converted_value = 0;
    if (str.empty())
    {
        return default_value;
    }

    try
    {
        converted_value = std::stoi(str);
        return converted_value;
    }
    catch (const std::invalid_argument& ia)
    {
        LOG(error) << "Invalid Arguments: str: " << str << " : "  << ia.what() << endl;
    }
    catch (const std::out_of_range& oor)
    {
        LOG(error) << "Out of Range: " << str << " : " << oor.what() << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Undefined error: " << str << " : " << e.what() << endl;
    }
    LOG(error) << boost::stacktrace::stacktrace() << endl;
    return default_value;
}

double stringToDouble(const std::string& str, const double& default_value)
{
    double converted_value = 0.0;
    if (str.empty())
    {
        return default_value;
    }

    try
    {
        converted_value = std::stod(str);
        return converted_value;
    }
    catch (const std::invalid_argument& ia)
    {
        LOG(error) << "Invalid Arguments: str: " << str << " : "  << ia.what() << endl;
    }
    catch (const std::out_of_range& oor)
    {
        LOG(error) << "Out of Range: " << str << " : " << oor.what() << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Undefined error: " << str << " : " << e.what() << endl;
    }
    LOG(error) << boost::stacktrace::stacktrace() << endl;
    return default_value;
}

string vectorToString(vector<int>& vec)
{
    std::string oss;
    for (auto i = vec.begin(); i != vec.end(); ++i)
    {
        oss += to_string(*i) + ",";
    }
    if(oss.empty() == false)
    {
        oss.pop_back();
    }
    return oss;
}

string vectorToString(vector<string>& vec)
{
    std::ostringstream oss;

    if (!vec.empty())
    {
        // Convert all but the last element to avoid a trailing ","
        std::copy(vec.begin(), vec.end()-1,
            std::ostream_iterator<string>(oss, ", "));

        // Now add the last element with no delimiter
        oss << vec.back();
    }
    return oss.str();
}

vector<string> stringToVector(const string& str)
{
    vector<string> result;
    stringstream ss(str);
    string item;

    while (getline(ss, item, ','))
    {
        result.push_back(item);
    }

    return result;
}

string getFilePathFromUrl(const string& url, const string& token)
{
    string file_path;
    if (url.empty() || token.empty())
    {
        return file_path;
    }

    file_path = url.substr(url.find(token) + token.size());
    return file_path;
}

string getStreamIdFromUrl(const string& url, const string& token)
{
    string stream_id;
    if (url.empty() || token.empty())
    {
        return stream_id;
    }

    stream_id = url.substr(url.find(token) + token.size());
    return stream_id;
}

string toLowerCase(string& upper)
{
    boost::algorithm::to_lower(upper);
    return upper;
}

uint32_t getAvailableMemory()
{
    string cmd = string("free -m | grep Mem");
    std::string result;
    int ret = runCMD(cmd, result);
    if (ret != 0)
    {
        LOG(error) << "Command run failed. Command: " << cmd << endl;
    }
    vector<std::string> arr = splitString(result, " ");
    try
    {
        return stringToInt(arr.back(), 0);
    }
    catch(const std::exception& e)
    {
        LOG(error) << "\nCaught Exception " <<  e.what() << endl;
        return 0;
    }
}

Json::Value getSystemStats()
{
    Json::Value value;
    string result;
    string cmd = "";
    int ret = -1;
    int pid = getpid();

    /* Get - Total open files count  */
    boost::format fmt = boost::format{"ls -1 /proc/%1%/fd | wc -l"} % pid;
    result = "";
    ret = runCMD(fmt.str(), result);
    if (ret == 0)
    {
        value["open_files_count"] = stringToInt(result, 0);
    }

    /* Get - rss_MB  */
    fmt = boost::format{"cat /proc/%1%/status"} % pid;
    cmd = fmt.str() + string(" | grep RssAnon:");
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        const char * pattern = "\\d+";
        boost::regex re(pattern);
        boost::sregex_iterator it(result.begin(), result.end(), re);
        boost::sregex_iterator end;
        if (it != end)
        {
            value["rss_MB"] = stringToInt(it->str(), 0)/1000;
        }
    }

    /* Get - Total system memory usage  */
    uint32_t current_available_memory = getAvailableMemory();
    value["system_memory_usage_MB"] = g_init_avaiable_memory > current_available_memory ?
                            g_init_avaiable_memory - current_available_memory : 0;


#ifdef JETSON_PLATFORM
    /* Get - dma_buf_count  */
    cmd = "cat /sys/kernel/debug/nvmap/iovmm/all_allocations | grep -e '\\b100' -e '\\b200' -e '\\b300' -e '\\b400' -e '\\b1100' -e '\\b1200' -e '\\b1400' | wc -l";
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        value["dma_buf_count"] = stringToInt(result, 0);
    }

    /* Get - iovmm_MB  */
    cmd = "cat /sys/kernel/debug/nvmap/iovmm/clients" + string(" | grep ") + to_string(pid);
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        vector<std::string> arr = splitString(result, " ");
        if (arr.size() > 0)
        {
            string iovmm = arr.back();
            string mm = iovmm.substr(0, iovmm.size() - 1);
            if (mm.empty() == false)
            {
                value["iovmm_MB"] = stringToInt(mm, 0) / 1000;
            }
        }
    }

    /* Get - total_iovmm_MB  */
    cmd = "cat /sys/kernel/debug/nvmap/iovmm/clients" + string(" | grep ") + string("total");
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        const char * pattern = "\\d+";
        boost::regex re(pattern);
        boost::sregex_iterator it(result.begin(), result.end(), re);
        boost::sregex_iterator end;
        if (it != end)
        {
            value["total_iovmm_MB"] = stringToInt(it->str(), 0)/1000;
        }
    }

    /* Get - Stats from tegrastats  */
    result = "";
    cmd = "timeout 2 tegrastats";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        Json::Value tegrastats;
        vector<std::string> arr = splitString(result, " ");
        if(arr.size() > 0)
        {
            for (size_t i = 0; i < arr.size(); i++)
            {
                if(arr[i] == "RAM")
                {
                    string mem = arr[i + 1];
                    eraseString(mem, "MB");
                    tegrastats["MEM_MB"] = mem;
                }
                else if(arr[i] == "EMC_FREQ")
                {
                    tegrastats["EMC_FREQ_MHz"] = arr[i + 1];
                }
                else if(arr[i] == "GR3D_FREQ")
                {
                    tegrastats["GR3D_FREQ_MHz"] = arr[i + 1];
                }
                else if(arr[i] == "NVENC")
                {
                    tegrastats["NVENC_MHz"] = arr[i + 1];
                }
                else if(arr[i] == "NVENC1")
                {
                    tegrastats["NVENC1_MHz"] = arr[i + 1];
                }
                else if(arr[i] == "NVDEC")
                {
                    tegrastats["NVDEC_MHz"] = arr[i + 1];
                }
                else if(arr[i] == "NVDEC1")
                {
                    tegrastats["NVDEC1_MHz"] = arr[i + 1];
                }
                else if(arr[i] == "CPU")
                {
                    Json::Value v = tegrastats["CPU"];
                    if(v.empty())
                    {
                        string cpu = arr[i + 1];
                        eraseString(cpu, "[");
                        eraseString(cpu, "]");
                        tegrastats["CPU"] = cpu;
                    }
                }
            }
            value["tegrastats"] = tegrastats;
        }
    }
#else
    /* Get - GPU Utilization in %,
    **     - GPU Memory Usage,
    **     - Total GPU Memory */
    cmd = "nvidia-smi --format=csv,noheader,nounits --query-gpu=utilization.gpu,memory.used,memory.total";
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        vector<std::string> arr = splitString(result, ",");
        for (size_t index = 0; index < arr.size(); ++index)
        {
            int val = stringToInt(arr[index], 0);
            if (index == 0)
            {
                value["gpu_usage"] = val;
            }
            else if (index == 1)
            {
                value["total_gpu_mem_usage_MB"] = val;
            }
            else if (index == 2)
            {
                value["total_gpu_mem_MB"] = val;
            }
        }
    }
    value["vst_gpu_mem_usage_MB"] = 0;
    cmd = "nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits";
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        vector<std::string> arr = splitString(result, ",");
        for (size_t index = 0; index < arr.size(); ++index)
        {
            int val = stringToInt(arr[index], 0);
            if (index == 1)
            {
                value["vst_gpu_mem_usage_MB"] = val;
            }
        }
    }

    /* Get - GPU Encoder Utilization in %,
    **     - GPU Decoder Utilization in % */
    cmd = "nvidia-smi -q -d UTILIZATION | grep 'Decoder\\|Encoder'";
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        std::vector<std::string> utilization_values;
        std::regex digit_regex("[\\d]+");
        auto digit_begin = std::sregex_iterator(result.begin(), result.end(), digit_regex);
        auto digit_end = std::sregex_iterator();

        for (std::sregex_iterator i = digit_begin; i != digit_end; ++i) {
            std::smatch match = *i;
            utilization_values.push_back(match.str());
        }

        for (size_t index = 0; index < utilization_values.size(); ++index)
        {
            int val = stringToInt(utilization_values[index], 0);
            if (index == 0)
            {
                value["enc_usage"] = val;
            }
            else if (index == 1)
            {
                value["dec_usage"] = val;
            }
        }
    }

    cmd = "nvidia-smi --query-gpu=gpu_name --format=csv,noheader,nounits";
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        value["gpu_name"] = result;
    }

    cmd = "nvidia-smi --query-gpu=index --format=csv,noheader,nounits";
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        value["gpu_index"] = result;
    }

    /* Get - Total CPU Utilization in % */
    cmd = "top -bn1  | grep '%Cpu'  | grep -P '(....|...) id,'|awk '{print "" 100-$8}'";
    result = "";
    ret = runCMD(cmd, result);
    if (ret == 0)
    {
        value["cpu_usage"] = stringToInt(result, 0);
    }

#endif
    return value;
}

bool blockSensor(const string ip, string action)
{
    bool response = false;
    if(ip.empty())
    {
        return false;
    }
    string cmd = "iptables -S";
    string result;
    int ret = runCMD(cmd, result, false);
    vector<string> entries;
    if (ret == 0)
    {
        std::string line;
        std::istringstream f(result);
        while (std::getline(f, line))
        {
            if(line.find(ip) != std::string::npos)
            {
                entries.push_back(line);
            }
        }
    }

    if (action == "plug")
    {
        for (size_t i = 0 ; i < entries.size(); i++)
        {
            cmd = entries[i];
            boost::replace_all(cmd, "A", "D");
            cmd = string("iptables ") + cmd;
            response = runCMD(cmd, result) == 0;
        }
    }
    else if (action == "unplug")
    {
        boost::format fmt = boost::format{"iptables -A OUTPUT -s %1% -j DROP "} % ip;
        cmd = fmt.str();
        response = runCMD(cmd, result) == 0;

        fmt = boost::format{"iptables -A INPUT -s %1% -j DROP "} % ip;
        cmd = fmt.str();
        response = response && runCMD(cmd, result) == 0;
    }
    if (action == "status")
    {
        if(entries.size() > 0)
        {
            response = true;
        }
    }
    return response;
}

Json::Value vectorToJson(const std::vector<string>& vec)
{
	Json::Value jsonArray = Json::nullValue;
	for(auto itr : vec)
	{
		jsonArray.append(itr);
	}
	return jsonArray;
}

std::vector<string> jsonToVector(const Json::Value& jsonArray)
{
	std::vector<string> vec;
    for (uint32_t i = 0; i < jsonArray.size(); i++)
    {
        vec.push_back(jsonArray[i].asString());
    }
	return vec;
}

std::vector<int> jsonArrayToVector(const Json::Value& jsonArray)
{
	std::vector<int> vec;
    for (uint32_t i = 0; i < jsonArray.size(); i++)
    {
        vec.push_back(jsonArray[i].asInt());
    }
	return vec;
}

string getRandomCommonName()
{
    string uuid = generate_uuid();
    try
    {
        // Clients will reject certificate with very long common name so truncate it
        uuid = uuid.substr(0,8);
    }
    catch(const std::exception& e)
    {
        LOG(error) << "Failed to generate UUID of valid length" << endl;
    }
    string randomIssuer = SSL_COMMON_NAME + string("-") + uuid;
    return randomIssuer;
}

/* ---------------------------------------------------------------------------
**  Base 64 encoding-decoding
** -------------------------------------------------------------------------*/

/* check if character is base64 */
static inline bool is_base64(unsigned char c)
{
    return (isalnum(c) || (c == '+') || (c == '/'));
}

/* encode bytes data to base64 string */
std::string base64_encode(char const* bytes_to_encode, unsigned int in_len)
{
    std::string ret;
    int i = 0;
    int j = 0;
    unsigned char char_array_3[3];
    unsigned char char_array_4[4];

    while (in_len--)
    {
        char_array_3[i++] = *(bytes_to_encode++);
        if (i == 3)
        {
            char_array_4[0] = (char_array_3[0] & 0xfc) >> 2;
            char_array_4[1] = ((char_array_3[0] & 0x03) << 4) + ((char_array_3[1] & 0xf0) >> 4);
            char_array_4[2] = ((char_array_3[1] & 0x0f) << 2) + ((char_array_3[2] & 0xc0) >> 6);
            char_array_4[3] = char_array_3[2] & 0x3f;

            for(i = 0; (i <4) ; i++)
            {
                ret += base64_chars[char_array_4[i]];
            }
            i = 0;
        }
    }

    if (i)
    {
        for(j = i; j < 3; j++)
        {
            char_array_3[j] = '\0';
        }

        char_array_4[0] = ( char_array_3[0] & 0xfc) >> 2;
        char_array_4[1] = ((char_array_3[0] & 0x03) << 4) + ((char_array_3[1] & 0xf0) >> 4);
        char_array_4[2] = ((char_array_3[1] & 0x0f) << 2) + ((char_array_3[2] & 0xc0) >> 6);

        for (j = 0; (j < i + 1); j++)
        {
            ret += base64_chars[char_array_4[j]];
        }

        while((i++ < 3))
        {
            ret += '=';
        }
    }
    return ret;
}

/* decode base64 string */
std::string base64_decode(std::string const& encoded_string)
{
    int in_len = encoded_string.size();
    int i = 0;
    int j = 0;
    int in_ = 0;
    unsigned char char_array_4[4], char_array_3[3];
    std::vector<unsigned char> vec;

    while (in_len-- && ( encoded_string[in_] != '=') && is_base64(encoded_string[in_]))
    {
        char_array_4[i++] = encoded_string[in_]; in_++;
        if (i == 4)
        {
            for (i = 0; i <4; i++)
            {
                char_array_4[i] = base64_chars.find(char_array_4[i]);
            }

            char_array_3[0] = (char_array_4[0] << 2) + ((char_array_4[1] & 0x30) >> 4);
            char_array_3[1] = ((char_array_4[1] & 0xf) << 4) + ((char_array_4[2] & 0x3c) >> 2);
            char_array_3[2] = ((char_array_4[2] & 0x3) << 6) + char_array_4[3];

            for (i = 0; (i < 3); i++)
            {
                vec.push_back(char_array_3[i]);
            }
            i = 0;
        }
    }

    if (i)
    {
        for (j = i; j <4; j++)
        {
            char_array_4[j] = 0;
        }

        for (j = 0; j <4; j++)
        {
            char_array_4[j] = base64_chars.find(char_array_4[j]);
        }

        char_array_3[0] = (char_array_4[0] << 2) + ((char_array_4[1] & 0x30) >> 4);
        char_array_3[1] = ((char_array_4[1] & 0xf) << 4) + ((char_array_4[2] & 0x3c) >> 2);
        char_array_3[2] = ((char_array_4[2] & 0x3) << 6) + char_array_4[3];

        for (j = 0; (j < i - 1); j++)
        {
            vec.push_back(char_array_3[j]);
        }
    }
	std::string str(reinterpret_cast<char *>(vec.data()), vec.size());
    return str;
}

/* ---------------------------------------------------------------------------
**  AES 256-bit encryption with base64 encoding decoding
** -------------------------------------------------------------------------*/

/* get unqiue key for every VST deployment */
string get_aes_key()
{
    string certFile1 = GET_CONFIG().vst_data_path + string("/") + CA_CERTIFICATE_FILE_NAME;
    string certFile2 = GET_CONFIG().vst_data_path + string("/") + SELF_SIGNED_CERTIFICATE_FILE_NAME;
    if (isFileExist(certFile1))
    {
        return readFileIntoString(certFile1);
    }
    else if (isFileExist(certFile2))
    {
        return readFileIntoString(certFile2);
    }
    else
    {
        /* used in gtest */
        LOG(error) << "Failed to read any certificate file (tried: " << certFile1 << " and " << certFile2 << "), using unsafe key" << endl;
        return ENCRYPTION_AES_KEY;
    }
}

long timevaldiff(struct timeval& starttime, struct timeval& endtime)
{
    long usec;
    if (starttime.tv_sec > endtime.tv_sec ||
           (starttime.tv_sec == endtime.tv_sec && starttime.tv_usec > endtime.tv_usec))
    {
        return 0;
    }
    usec = ((endtime.tv_sec * 1000000) + (endtime.tv_usec)) -
                ((starttime.tv_sec * 1000000) + (starttime.tv_usec));
	return usec;
}

int64_t convertTimeValToEpochMs (struct timeval& time)
{
    return time.tv_sec * 1000 + time.tv_usec / 1000;
}

int64_t convertTimeValToEpochSec(struct timeval& time)
{
    return (time.tv_sec + (time.tv_usec / 1000000.0));
}

int64_t getCurrentUnixTimestamp()
{
    auto secondsUTC = std::chrono::duration_cast<std::chrono::seconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    return secondsUTC;
}

int64_t getCurrentUnixTimestampInMs()
{
    auto millisecondsUTC = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    return millisecondsUTC;
}

string getIpAddrFromDnsName(const string dnsName)
{
    string ip_addr;
    struct addrinfo hints{};
    struct addrinfo *res = nullptr;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    if (int gai_err = getaddrinfo(dnsName.c_str(), nullptr, &hints, &res); gai_err == 0 && res)
    {
        std::string ipBuf(INET_ADDRSTRLEN, '\0');
        const auto *addr = (const struct sockaddr_in *)res->ai_addr;
        inet_ntop(AF_INET, &addr->sin_addr, ipBuf.data(), INET_ADDRSTRLEN);
        ip_addr = ipBuf.c_str();
        freeaddrinfo(res);
    }
    return ip_addr;
}

string getRedisServerEndpoint()
{
    string redis_ip;
    nv_vms::DeviceConfig config =  GET_CONFIG();
    string redis_connect_str = config.redis_server_env_var;
    vector<string> arr = splitString(redis_connect_str, ":");
    if (arr.size() != 2)
    {
        LOG(error) << "Radis env variable in config is not valid" << endl;
        return "";
    }

    if (validateIpAddress(arr[0]) == false)
    {
        /* It is either env variable or dns name */
        string resolved_ip;
        resolveEnvironmentVariable(arr[0], resolved_ip);
        if (resolved_ip.empty())
        {
            resolved_ip = getIpAddrFromDnsName(arr[0]);
            if (resolved_ip.empty())
            {
                resolved_ip = getIpAddrFromDnsName("localhost");
            }
        }
        redis_ip = resolved_ip;
    }
    else
    {
        /* Redis-server ip is provided */
        redis_ip = arr[0];
    }
    redis_connect_str = redis_ip + string(";") + arr[1];
    return redis_connect_str;
}

string getKafkaServerEndpoint()
{
    string kafka_ip;
    nv_vms::DeviceConfig config =  GET_CONFIG();
    string kafka_connect_str = config.kafka_server_address;
    if (kafka_connect_str.empty())
    {
        return kafka_ip;
    }
    vector<string> arr = splitString(kafka_connect_str, ":");
    if (arr.size() != 2)
    {
        LOG(error) << "Kafka endpoint is not valid, Correct format is => hostname:9092 or ip:9092" << endl;
        return "";
    }

    if (validateIpAddress(arr[0]) == false)
    {
        /* This could be a dns name or wrong ipAddr */
        LOG(info) << "This is either hostname or wrong IP: " << arr[0] << endl;
    }
    kafka_ip = arr[0];
    kafka_connect_str = kafka_ip + string(":") + arr[1];
    return kafka_connect_str;
}

bool checkWhiteSpace(const string str)
{
    for (auto ch: str)
    {
        if (isspace(ch))
        {
            return true;
        }
    }
    return false;
}

bool checkFileNameLength(const string str)
{
    if (str.length() > MAX_FILE_NAME_LENGTH)
    {
        return true;
    }
    return false;
}

void removeWhiteSpaces(string& str)
{
    boost::algorithm::trim(str);
}

void detectGPU()
{
    g_isGpuPresent = false;
#ifdef JETSON_PLATFORM
    g_isJetsonGpuMode = isJetsonGpuPresent();
#endif
    for (int gpuIndex = 0 ; gpuIndex < MAX_GPU_COUNT; gpuIndex++)
    {
        string nvidia_node = string("/dev/nvidia") + to_string(gpuIndex);
        if (isFileExist(nvidia_node))
        {
            g_isGpuPresent = true;
            g_gpuNodePath = nvidia_node;
            break;
        }
    }
    if(g_isGpuPresent == false)
    {
        LOG(error) << "############## NO GPU IS DETECTED " << "##############" << endl;
    }
    else
    {
        if (GET_CONFIG().gpu_indices.size() == 0)
        {
            g_gpuIndex = 0;
        }
        LOG(info) << "############## GPU ID DETECTED = " << g_gpuIndex << " ##############" << endl;
        LOG(info) << "############## GPU Device = " << g_gpuNodePath << " ##############" << endl;
    }
}

bool isASCII (const std::string& s)
{
    return !std::any_of(s.begin(), s.end(), [](char c) {
        return static_cast<unsigned char>(c) > 127;
    });
}

/**
 *  Password is valid if all chars are ASCII, has upper case and lower case char and a digit.
 *  Password must have length greater than or equal to 6 and must have no whitespaces
 */
bool validatePassword(const string& password)
{
    if (!isASCII(password))
    {
        return false;
    }
    int n = password.length();
    if (n >= 4)
    {
        return true;
    }
    return false;
}

std::map<std::string, std::string, std::less<>> getStreamOptions(Json::Value in)
{
    std::map<std::string, std::string, std::less<>> opts;
    Json::Value optionsJson;
    optionsJson = in.get("options", EMPTY_STRING);

    Json::ValueIterator itr_json = optionsJson.begin();
    while (itr_json != optionsJson.end())
    {
        if (itr_json->isObject() == false)
        {
            opts[itr_json.name()] = optionsJson.get(itr_json.name(), EMPTY_STRING).asString();
        }
        itr_json++;
    }
    Json::Value overlayJson = optionsJson.get("overlay", EMPTY_STRING);
    if (overlayJson.isObject())
    {
        setOverlayOptsBasedOnJson(opts, overlayJson);

        // Check if overlay is enabled based on the parsed options
        bool needBbox = opts.count("overlayBbox") && opts.at("overlayBbox") == "true";
        bool needTripwire = opts.count("overlayTripwire") && opts.at("overlayTripwire") == "true";
        bool needRoi = opts.count("overlayRoi") && opts.at("overlayRoi") == "true";
        bool needPose = opts.count("overlayPose") && opts.at("overlayPose") == "true";
        bool needHalo = opts.count("overlayHalos") && opts.at("overlayHalos") == "true";
        bool overlayEnabled = needBbox || needTripwire || needRoi || needPose || needHalo;
        opts["overlay"] = overlayEnabled ? "true" : "false";
    }

    Json::Value compositeJson = optionsJson.get("composite", EMPTY_STRING);
    if (compositeJson.isObject())
    {
        if (compositeJson.get("doComposite", false).asBool())
        {
            opts["doComposite"] = "true";
            if (optionsJson.isMember("framerate"))
            {
                opts["framerate"] = optionsJson.get("framerate", EMPTY_STRING).asString();
            }
            LOG(info) << "Composite overlay is enabled, framerate is set to " << opts["framerate"] << endl;
        }
        Json::Value stream_ids_json = compositeJson.get("streamIds", EMPTY_STRING);
        if(!stream_ids_json.empty())
        {
            string streamIds = "";
            for (auto x: stream_ids_json)
            {
                streamIds += "," + x.asString();
            }
            if (!streamIds.empty())
            {
                streamIds.erase(streamIds.begin()); // Erase first "," at position 0
            }
            opts["streamIds"] = streamIds;
        }
        Json::Value compositeoverlayJson = compositeJson.get("showSensorName", EMPTY_STRING);
        if (compositeoverlayJson.isObject())
        {
            bool enableCompositeOverlay = compositeoverlayJson.get("enable", false).asBool();
            if (enableCompositeOverlay)
            {
                opts["showSensorName"] = "true";
                Json::Value overlay_position = compositeoverlayJson.get("position", EMPTY_STRING);
                if (!overlay_position.empty())
                {
                    string position = "";
                    for (auto x: overlay_position)
                    {
                        position += "," + x.asString();
                    }
                    if (!position.empty())
                    {
                        position.erase(position.begin()); // Erase first "," at position 0
                    }
                    opts["showSensorNamePosition"] = position;
                }
            }
        }
        bool includeFloorPlan = compositeJson.get("includeFloorPlan", false).asBool();
        if (includeFloorPlan)
        {
            opts["gods_eye_view"] = "true";
        }
        Json::Value compositeLayout = compositeJson.get("gridLayout", EMPTY_STRING);
        if (compositeLayout.isObject())
        {
            opts["compositeLayout"] = jsonToString(compositeLayout);
        }
    }

    if (in.isMember("tag"))
    {
        opts["tag"] = in.get("tag", EMPTY_STRING).asString();
    }
    return opts;
}

bool isSubstring(const std::string& target, const std::string& substring)
{
    return target.find(substring) != std::string::npos;
}

bool isSubstringCaseInsensitive(const std::string& target, const std::string& substring)
{
    std::string targetLower = target;
    std::string substringLower = substring;

    // Convert both to lowercase
    std::transform(targetLower.begin(), targetLower.end(), targetLower.begin(), ::tolower);
    std::transform(substringLower.begin(), substringLower.end(), substringLower.begin(), ::tolower);

    return targetLower.find(substringLower) != std::string::npos;
}

// Safe digit counting function to replace ceil(log10(pts))
// Handles pts=0 case and avoids potential log10 issues
int countDigits(uint64_t num)
{
    if (num == 0) {
        return 1;  // Special case: 0 has 1 digit
    }
    
    int count = 0;
    while (num > 0) {
        num /= 10;
        count++;
    }
    return count;
}

uint64_t getTimestampInMilliSecond(uint64_t pts)
{
    uint64_t ts = 0;

    /* Convert it into millisecond */
    int num_digits = countDigits(pts);
    if (num_digits == NUM_DIGITS_SECOND)
    {
        /* s */
        ts = pts * 1000;
    }
    else if (num_digits == NUM_DIGITS_MILLI_SECOND)
    {
        /* ms */
        ts = pts;
    }
    else if (num_digits == NUM_DIGITS_MICRO_SECOND)
    {
        /* us */
        ts = pts / 1000;
    }
    else if (num_digits == NUM_DIGITS_NANO_SECOND)
    {
        /* ns */
        ts = pts / 1000000;
    }

    return ts;
}

uint64_t getTimestampInMicroSecond(uint64_t pts)
{
    uint64_t ts = 0;

    /* Convert it into microsecond */
    int num_digits = countDigits(pts);
    if (num_digits == NUM_DIGITS_MILLI_SECOND)
    {
        /* ms */
        ts = pts * 1000;
    }
    else if (num_digits == NUM_DIGITS_NANO_SECOND)
    {
        /* ns */
        ts = pts / 1000;
    }
    else if (num_digits == NUM_DIGITS_MICRO_SECOND)
    {
        return pts;
    }

    return ts;
}

uint64_t getTimestampInNanoSecond(uint64_t pts)
{
    uint64_t ts = 0;

    /* Convert it into nanosecond */
    int num_digits = countDigits(pts);
    if (num_digits == NUM_DIGITS_MILLI_SECOND)
    {
        ts = pts * 1000 * 1000;
    }
    else if (num_digits == NUM_DIGITS_MICRO_SECOND)
    {
        ts = pts * 1000;
    }

    return ts;
}

void extractUrlInfo(const std::string& url, string& protocol, string& ipOrHost, int& port, string& apiPath)
{
    // Remove trailing slashes from the URL
    std::string cleanedUrl = removeTrailingSlashes(url);

    // Regular expression pattern to match URLs with protocol, IP, PORT, and API PATH
    // Pattern captures protocol, IP, PORT, and API PATH in separate groups
    std::regex pattern(R"((https?://)?([^:/]+)(?::(\d+))?(/.*)?)");

    std::smatch matches;
    if (std::regex_match(cleanedUrl, matches, pattern))
    {
        // Group 0: The entire matched string.
        // Group 1: (https?://)? - This captures the protocol (http or https).
        // Group 2: ([^:/]+) - This captures the IP or hostname part of the URL.
        // Group 3: (?::(\d+))? - This captures the optional port part (if present).
        // Group 4: (/.*)? - This captures the optional API path part (if present).
        if (matches[1].matched)
        {
            protocol = matches[1].str();
        }
        else
        {
            protocol = HTTP_PROTOCOL;
        }

        if (matches[2].matched)
        {
            ipOrHost = matches[2].str();
        }

        if (matches[3].matched)
        {
            port = stringToInt(matches[3].str(), 80);
        }
        else
        {
            // If port is not mentioned, assume default ports
            if (protocol == HTTP_PROTOCOL)
            {
                port = 80;
            }
            else if (protocol == HTTPS_PROTOCOL)
            {
                port = 443;
            }
        }

        if (matches[4].matched)
        {
            apiPath = matches[4].str();
        }
    }
    else
    {
        // Handle invalid URLs here
        LOG(error) << "Invalid URL: " << secureUrlForLogging(url) << std::endl;
        // You can set default values or take other appropriate actions here
    }
    return;
}

// Function to remove trailing slashes from a string
std::string removeTrailingSlashes(const std::string& str)
{
    std::size_t found = str.find_last_not_of('/');
    if (found != std::string::npos)
    {
        return str.substr(0, found + 1);
    }
    return str;
}

int getCurrentCoreId()
{
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    // Get the CPU affinity mask for the current thread
    if (sched_getaffinity(0, sizeof(cpu_set_t), &cpuset) == -1)
    {
        LOG(error) << "Failed to get CPU affinity for the current thread" << endl;
        return -1;
    }

    // Find the first set bit in the CPU affinity mask
    for (int i = 0; i < CPU_SETSIZE; ++i)
    {
        if (CPU_ISSET(i, &cpuset))
        {
            return i;
        }
    }
    return -1; // No CPU found
}

double findNearestValue(const std::vector<double>& numbers, double target)
{
    // Sort the numbers
    std::vector<double> sortedNumbers = numbers;
    std::sort(sortedNumbers.begin(), sortedNumbers.end());

    // Find the nearest value
    auto nearest = std::lower_bound(sortedNumbers.begin(), sortedNumbers.end(), target);

    // If the target is not found, check the adjacent elements
    if (nearest == sortedNumbers.begin())
    {
        return *nearest;
    }
    else if (nearest == sortedNumbers.end())
    {
        return *(nearest - 1);
    }
    else
    {
        double val1 = *nearest;
        double val2 = *(nearest - 1);
        return std::abs(val1 - target) < std::abs(val2 - target) ? val1 : val2;
    }
}

std::string removeDecimals(double number)
{
    std::ostringstream oss;
    if (number >= 1.0)
    {
        // Set as integer if the value is greater than 1
        oss << std::fixed << std::setprecision(0) << number;
    }
    else
    {
        // Keep as double if the value is less than or equal to 1
        oss << std::fixed << std::setprecision(6) << number;
    }

    return oss.str();
}

AuthenticationMethods getSecuredAuthMethod(AuthenticationMethods supportedMethods)
{
    if (supportedMethods & AUTH_METHOD_DIGEST)
    {
        return AUTH_METHOD_DIGEST;
    }
    else if (supportedMethods & AUTH_METHOD_USERNAME_TOKEN)
    {
        return AUTH_METHOD_USERNAME_TOKEN;
    }
    else
    {
        return AUTH_METHOD_NONE;
    }
}

long long stringToLong(const std::string& str, const long long default_value)
{
    long long converted_value = 0;
    if (str.empty())
    {
        return default_value;
    }

    try
    {
        converted_value = std::stoll(str);
        return converted_value;
    }
    catch (const std::invalid_argument& ia)
    {
        LOG(error) << "Invalid Arguments: str: " << str << " : "  << ia.what() << endl;
    }
    catch (const std::out_of_range& oor)
    {
        LOG(error) << "Out of Range: " << str << " : " << oor.what() << endl;
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Undefined error: " << str << " : " << e.what() << endl;
    }
    LOG(error) << boost::stacktrace::stacktrace() << endl;
    return default_value;
}

string serialize(vector<string> &filePaths)
{
    /* length of typical path is 83, string::max_size is 4294967291
     * that is equal to 51,746,593 file paths OR 1000 TB of video data
     * OR 51.74 MB of string
     */
    string serializedFilePaths = "";
    uint32_t size = filePaths.size();
    if (size == 0)
        return serializedFilePaths;
    else if (size == 1)
    {
        serializedFilePaths = string("\"") + filePaths[0] + string("\"");
        return serializedFilePaths;
    }
    else
    {
        serializedFilePaths = string("\"") + filePaths[0] + string("\"");
        for (uint32_t i = 1; i < size; i++)
        {
            serializedFilePaths += string(", \"") + filePaths[i] + string("\"");
        }
        return serializedFilePaths;
    }
}

pair<int64_t, int64_t> getEpochTimeRangeFromIsoString(const string& timeRange)
{
    int64_t epochStartTime = 0 , epochEndTime = 0;
    string startTime, endTime;

    // Find the position of the '&' character
    size_t ampPos = timeRange.find('&');
    size_t startEqualPos = timeRange.find("startTs=");
    if (startEqualPos != std::string::npos)
    {
        startEqualPos += std::string("startTs=").length();
        size_t endPos = (ampPos != std::string::npos) ? ampPos : timeRange.length();
        startTime = timeRange.substr(startEqualPos, endPos - startEqualPos);
    }

    size_t endEqualPos = timeRange.find("endTs=");
    if (endEqualPos != std::string::npos)
    {
        endEqualPos += std::string("endTs=").length();
        endTime = timeRange.substr(endEqualPos);
    }
    LOG(info) << "start time: " << startTime << " & end time: " << endTime << endl;

    if (!startTime.empty())
    {
        epochStartTime = getEpocTimeInMS(startTime);
        LOG(info) << "epoch start time: " << epochStartTime << endl;
    }
    if (!endTime.empty())
    {
        epochEndTime = getEpocTimeInMS(endTime);
        LOG(info) << "epoch end time: " << epochEndTime << endl;
    }
    return std::make_pair(epochStartTime, epochEndTime);
}

std::string truncateString(const std::string& str, size_t limit)
{
    if (str.length() > limit)
    {
        return str.substr(0, limit);
    }
    return str;
}

int extractPort(const std::string& rtsp_url)
{
    // Check for empty input
    if (rtsp_url.empty())
    {
        return -1;
    }

    try {
        // More comprehensive regex pattern
        // Handles URLs with or without credentials, IPv4, IPv6, and hostnames
        std::regex regex(R"(rtsp://(?:[^@/]+@)?([^:/]+|\[[^\]]+\]):([0-9]+))");
        std::smatch match;

        if (std::regex_search(rtsp_url, match, regex))
        {
            // Check if we have enough matches (full match + host + port)
            if (match.size() < 3)
            {
                return -1;
            }
            return std::stoi(match[2].str());
        }

        return -1;
    }
    catch (const std::regex_error& e) {
        // Handle regex errors
        return -1;
    }
    catch (const std::invalid_argument& e) {
        // Handle stoi conversion errors
        return -1;
    }
    catch (const std::out_of_range& e) {
        // Handle port number out of range
        return -1;
    }
}

std::string urlDecode(const std::string& encoded)
{
    // Security check: Input length
    if (encoded.length() > MAX_QUERY_PARAM_VALUE_LENGTH * 3) // Allow for worst case encoding
    {
        LOG(warning) << "URL encoded string too long for decoding: " << encoded.length() << " chars" << endl;
        return "";
    }
    
    std::string decoded;
    decoded.reserve(encoded.length());
    
    for (size_t i = 0; i < encoded.length(); ++i)
    {
        if (encoded[i] == '%' && i + 2 < encoded.length())
        {
            // Convert hex digits to character
            std::istringstream hex_stream(encoded.substr(i + 1, 2));
            int value;
            if (hex_stream >> std::hex >> value)
            {
                // Security check: Only allow printable ASCII and common whitespace
                if ((value >= 32 && value <= 126) || value == 9 || value == 10 || value == 13)
                {
                    decoded += static_cast<char>(value);
                }
                else
                {
                    // Replace non-printable characters with underscore
                    decoded += '_';
                }
                i += 2; // Skip the two hex digits
            }
            else
            {
                // Invalid hex sequence, keep the '%' as is
                decoded += encoded[i];
            }
        }
        else if (encoded[i] == '+')
        {
            // '+' represents space in URL encoding
            decoded += ' ';
        }
        else
        {
            decoded += encoded[i];
        }
    }
    
    return decoded;
}

bool isValidQueryParamKey(const std::string& key)
{
    if (key.empty() || key.length() > MAX_QUERY_PARAM_KEY_LENGTH)
    {
        return false;
    }
    
    // Allow alphanumeric characters, underscores, hyphens, and dots
    // This prevents injection attacks and follows common query parameter naming conventions
    for (char c : key)
    {
        if (!std::isalnum(c) && c != '_' && c != '-' && c != '.')
        {
            return false;
        }
    }
    
    // Additional security: Don't allow keys that start with certain patterns
    // that might be used for injection attacks
    if (boost::starts_with(key, "__") || 
        boost::starts_with(key, "..") ||
        boost::icontains(key, "script") ||
        boost::icontains(key, "javascript") ||
        boost::icontains(key, "vbscript"))
    {
        return false;
    }
    
    return true;
}

Json::Value parseQueryStringToJson(const std::string& queryString)
{
    Json::Value result = Json::objectValue;
    
    if (queryString.empty())
    {
        return result;
    }
    
    // Security pre-check
    if (!isQueryStringSafe(queryString))
    {
        LOG(error) << "Query string failed security validation" << endl;
        return Json::nullValue;
    }
    
    // Security check: Query string length
    if (queryString.length() > MAX_QUERY_STRING_LENGTH)
    {
        LOG(error) << "Query string too long: " << queryString.length() 
                   << " bytes (max: " << MAX_QUERY_STRING_LENGTH << ")" << endl;
        return Json::nullValue;
    }
    
    // Split query string by '&' using Boost
    std::vector<std::string> params;
    boost::split(params, queryString, boost::is_any_of("&"));
    
    // Security check: Number of parameters
    if (params.size() > MAX_QUERY_PARAMS)
    {
        LOG(error) << "Too many query parameters: " << params.size() 
                   << " (max: " << MAX_QUERY_PARAMS << ")" << endl;
        return Json::nullValue;
    }
    
    for (const auto& param : params)
    {
        if (param.empty()) continue;
        
        // Split by '=' to get key and value
        std::vector<std::string> keyValue;
        boost::split(keyValue, param, boost::is_any_of("="), boost::token_compress_off);
        
        std::string key, value;
        if (keyValue.size() >= 1)
        {
            key = urlDecode(boost::trim_copy(keyValue[0]));
        }
        if (keyValue.size() >= 2)
        {
            value = urlDecode(boost::trim_copy(keyValue[1]));
        }
        // If no value part (param without =), value remains empty string
        
        if (key.empty()) continue;
        
        // Security check: Key length
        if (key.length() > MAX_QUERY_PARAM_KEY_LENGTH)
        {
            LOG(error) << "Query parameter key too long: '" << key.substr(0, 50) << "...' " 
                       << key.length() << " chars (max: " << MAX_QUERY_PARAM_KEY_LENGTH << ")" << endl;
            return Json::nullValue;
        }
        
        // Security check: Value length
        if (value.length() > MAX_QUERY_PARAM_VALUE_LENGTH)
        {
            LOG(error) << "Query parameter value too long for key '" << key << "': " 
                       << value.length() << " chars (max: " << MAX_QUERY_PARAM_VALUE_LENGTH << ")" << endl;
            return Json::nullValue;
        }
        
        // Security check: Validate key contains only safe characters
        if (!isValidQueryParamKey(key))
        {
            LOG(error) << "Invalid characters in query parameter key: '" << key << "'" << endl;
            return Json::nullValue;
        }
        
        // Handle multiple values for same key
        if (result.isMember(key))
        {
            // Security check: Too many values for same key
            if (result[key].isArray() && result[key].size() >= MAX_QUERY_PARAM_VALUES_PER_KEY)
            {
                LOG(error) << "Too many values for query parameter '" << key << "': " 
                           << result[key].size() << " (max: " << MAX_QUERY_PARAM_VALUES_PER_KEY << ")" << endl;
                return Json::nullValue;
            }
            
            // Convert to array if not already
            if (!result[key].isArray())
            {
                Json::Value oldValue = result[key];
                result[key] = Json::arrayValue;
                result[key].append(oldValue);
            }
            result[key].append(value);
        }
        else
        {
            result[key] = value;
        }
    }
    
    return result;
}

bool isQueryStringSafe(const std::string& queryString)
{
    // Basic length check
    if (queryString.length() > MAX_QUERY_STRING_LENGTH)
    {
        LOG(warning) << "Query string exceeds maximum length: " << queryString.length() << endl;
        return false;
    }
    
    // Check for suspicious patterns that might indicate injection attempts
    std::vector<std::string> suspiciousPatterns = {
        "<script", "</script", "javascript:", "vbscript:", "data:",
        "onload=", "onerror=", "onclick=", "onmouseover=",
        "../", "..\\", "/etc/", "\\windows\\",
        "union select", "drop table", "delete from", "insert into",
        "' or ", "\" or ", "' and ", "\" and ",
        "/*", "*/", "--", "xp_", "sp_"
    };
    
    std::string lowerQuery = queryString;
    boost::to_lower(lowerQuery);
    
    // Check for suspicious patterns that might indicate injection attempts
    for (const auto& pattern : suspiciousPatterns)
    {
        if (lowerQuery.find(pattern) != std::string::npos)
        {
            LOG(warning) << "Suspicious pattern detected in query string: " << pattern << endl;
            return false;
        }
    }
    
    // Check for excessive URL encoding (potential obfuscation)
    size_t percentCount = std::count(queryString.begin(), queryString.end(), '%');
    if (percentCount > queryString.length() / 4) // More than 25% encoded
    {
        LOG(warning) << "Excessive URL encoding detected: " << percentCount << " percent signs" << endl;
        return false;
    }
    
    // Check for control characters (except normal whitespace)
    for (char c : queryString)
    {
        if (c < 32 && c != 9 && c != 10 && c != 13) // Allow tab, LF, CR
        {
            LOG(warning) << "Control character detected in query string: " << (int)c << endl;
            return false;
        }
    }
    
    return true;
}
/**
 * Normalizes a relative path by prepending the base directory path
 * 
 * @param filePath The input file path (relative or absolute)
 * @param basePath The base directory path to prepend for relative paths
 * @return The normalized file path
 */
string normalizeRelativePath(const string& filePath, const string& basePath)
{
    string file = filePath;
    
    if (file.empty() || file[0] != '/') // relative path (not starting with '/')
    {
        // Handle relative paths: ./file, file, ../file, subdir/file, etc.
        string normalizedBasePath = basePath;
        if (!normalizedBasePath.empty() && normalizedBasePath.back() != '/')
        {
            normalizedBasePath += '/';
        }
        // Remove leading ./ if present for cleaner path construction
        if (file.rfind("./", 0) == 0)
        {
            file = file.substr(2);
        }
        file = normalizedBasePath + file;
    }
    
    return file;
}

/**
 * Parse timestamp value from JSON that can be either:
 * 1. Numeric timestamp in milliseconds (e.g., 1758018961537)
 * 2. ISO 8601 UTC format string (e.g., "2025-08-28T14:47:12.999Z")
 */
int64_t parseTimestampValue(const Json::Value& timestampValue)
{
    if (timestampValue.isNull())
    {
        return getCurrentUnixTimestampInMs();
    }
    
    if (timestampValue.isNumeric())
    {
        // Handle numeric timestamp (assume milliseconds)
        return timestampValue.asInt64();
    }
    
    if (timestampValue.isString())
    {
        string timestampStr = timestampValue.asString();
        
        // If it's a string, treat it as UTC ISO format only
        // Convert ISO format to epoch milliseconds
        std::time_t epochResult = isoToEpoch(timestampStr, false); // false means return milliseconds
        
        // Validate basic ISO format (should contain 'T' and be reasonable length)
        if (timestampStr.find('T') == string::npos || timestampStr.length() < 19) {
            // Not a valid ISO format (should contain 'T' and be at least YYYY-MM-DDTHH:MM:SS)
            LOG(warning) << "Invalid ISO timestamp format: " << timestampStr << endl;
            return getCurrentUnixTimestampInMs();
        }
        
        // isoToEpoch returns 0 on parsing failure (when strptime fails)
        // So we treat 0 as parsing failure and fallback to current timestamp
        if (epochResult == 0) {
            LOG(warning) << "Failed to parse ISO timestamp: " << timestampStr << ", using current timestamp" << endl;
            return getCurrentUnixTimestampInMs();
        }
        
        return static_cast<int64_t>(epochResult);
    }
    
    return getCurrentUnixTimestampInMs();
}

string getIngressBaseUrl()
{
    const DeviceConfig& config = GET_CONFIG();
    const string protocol = config.use_https ? "https" : "http";
    const string baseUrl = protocol + "://" + config.ingress_endpoint;
    return baseUrl;
}

// Security utility functions for masking sensitive data in logs
string maskSensitiveData(const string& data, MaskType type)
{
    if (data.empty())
    {
        return "";
    }
    
    switch (type)
    {
        case MaskType::PASSWORD:
        case MaskType::FULL_MASK:
            return string(data.length(), '*');
            
        case MaskType::USERNAME:
        case MaskType::PARTIAL_MASK:
            if (data.length() <= 2)
            {
                return "**";
            }
            return data.substr(0, 1) + string(data.length() - 2, '*') + data.substr(data.length() - 1);
            
        case MaskType::CUSTOM:
            // Default to partial masking for custom
            if (data.length() <= 2)
            {
                return "**";
            }
            return data.substr(0, 1) + string(data.length() - 2, '*') + data.substr(data.length() - 1);
            
        default:
            return string(data.length(), '*');
    }
}

string maskSensitiveData(const string& data, MaskType type, char maskChar, int visibleChars)
{
    if (data.empty())
    {
        return "";
    }
    
    // Ensure visibleChars is reasonable
    if (visibleChars < 0)
    {
        visibleChars = 0;
    }
    if (visibleChars > (int)data.length()) visibleChars = data.length();
    
    int maskLength = data.length() - visibleChars;
    if (maskLength <= 0)
    {
        return string(data.length(), maskChar);
    }
    
    if (visibleChars == 0)
    {
        return string(data.length(), maskChar);
    }
    
    // Distribute visible characters: half at start, half at end
    int startVisible = visibleChars / 2;
    int endVisible = visibleChars - startVisible;
    
    string result;
    result += data.substr(0, startVisible);
    result += string(maskLength, maskChar);
    result += data.substr(data.length() - endVisible);
    
    return result;
}

// Secure URL logging function that avoids credential extraction entirely
string secureUrlForLogging(const string& url)
{
    if (url.empty())
    {
        return "";
    }
    
    // Find the last @ symbol (handles passwords with @ characters)
    size_t atPos = url.rfind('@');
    if (atPos == string::npos)
    {
        // No credentials found, return URL as-is
        return url;
    }
    
    // Find the protocol separator
    size_t protocolPos = url.find("://");
    if (protocolPos == string::npos)
    {
        // Invalid URL format, return as-is
        return url;
    }
    
    // SECURITY FIX: Instead of extracting credentials, completely avoid them
    // Extract only non-sensitive parts for secure logging
    string protocol = url.substr(0, protocolPos + 3); // Include "://"
    string hostAndPath = url.substr(atPos + 1);
    
    // Return structured logging format without any credential processing
    return protocol + "[CREDENTIALS_REDACTED]@" + hostAndPath;
}

/**
 * Mask presigned URLs by hiding sensitive query parameters
 * (e.g., AWS S3 presigned URLs with signatures and credentials)
 */
string maskPresignedUrl(const string& url)
{
    if (url.empty())
    {
        return "";
    }
    
    // Find the query string separator
    size_t queryPos = url.find('?');
    if (queryPos == string::npos)
    {
        // No query parameters, return URL as-is
        return url;
    }
    
    // Extract base URL and query string
    string baseUrl = url.substr(0, queryPos);
    string queryString = url.substr(queryPos + 1);
    
    // List of sensitive parameter keys to mask
    vector<string> sensitiveParams = {
        "X-Amz-Signature",
        "X-Amz-Credential",
        "X-Amz-Security-Token",
        "Signature",
        "AWSAccessKeyId",
        "signature",
        "access_key"
    };
    
    // Parse and mask sensitive parameters
    vector<string> params;
    boost::split(params, queryString, boost::is_any_of("&"));
    
    string maskedQuery = "";
    bool first = true;
    
    for (const auto& param : params)
    {
        if (param.empty()) continue;
        
        if (!first)
        {
            maskedQuery += "&";
        }
        first = false;
        
        // Split by '=' to get key and value
        size_t eqPos = param.find('=');
        if (eqPos != string::npos)
        {
            string key = param.substr(0, eqPos);
            string value = param.substr(eqPos + 1);
            
            // Check if this is a sensitive parameter
            bool isSensitive = false;
            for (const auto& sensitiveKey : sensitiveParams)
            {
                if (key == sensitiveKey)
                {
                    isSensitive = true;
                    break;
                }
            }
            
            if (isSensitive)
            {
                // Mask the value, showing only a few characters
                if (value.length() > 8)
                {
                    maskedQuery += key + "=" + value.substr(0, 4) + "***" + value.substr(value.length() - 4);
                }
                else
                {
                    maskedQuery += key + "=***";
                }
            }
            else
            {
                // Keep non-sensitive parameters as-is
                maskedQuery += param;
            }
        }
        else
        {
            // No value, keep as-is
            maskedQuery += param;
        }
    }
    
    return baseUrl + "?" + maskedQuery;
}

/**
 * Determine media content type from file extension
 */
string getMediaContentType(const string& fileExtension)
{
    if (iequals(fileExtension, ".mp4")) return string("video/mp4");
    if (iequals(fileExtension, ".avi")) return string("video/x-msvideo");
    if (iequals(fileExtension, ".mov")) return string("video/quicktime");
    if (iequals(fileExtension, ".mkv")) return string("video/x-matroska");
    if (iequals(fileExtension, ".webm")) return string("video/webm");
    if (iequals(fileExtension, ".png")) return string("image/png");
    if (iequals(fileExtension, ".jpg") || iequals(fileExtension, ".jpeg")) return string("image/jpeg");
    return string("application/octet-stream"); // Default
}

template<typename Compare>
void setOverlayOptsBasedOnJson(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson)
{
    if (!overlayJson.isObject())
    {
        return;
    }

    // Detect old vs new overlay schema: needBbox, needTripwire, needRoi exist only in old schema
    bool has_old_schema_fields = (overlayJson.isMember("needBbox") || overlayJson.isMember("needTripwire") || overlayJson.isMember("needRoi"));
    
    if (has_old_schema_fields)
    {
        LOG(info) << "setOverlayOptsBasedOnJson: using old overlay schema (needBbox/needTripwire/needRoi present)" << endl;
        parseOldSchema(opts, overlayJson);
    }
    else
    {
        LOG(info) << "setOverlayOptsBasedOnJson: using new overlay schema (bbox/tripwire/roi objects)" << endl;
        parseNewSchema(opts, overlayJson);
    }
}

template<typename Compare>
void parseOldSchema(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson)
{
    // Set default values for overlay options
    opts["overlayBbox"] = "false";
    opts["overlayTripwire"] = "false";
    opts["overlayRoi"] = "false";

    // Parse old schema fields
    string needBbox = overlayJson.get("needBbox", EMPTY_STRING).asString();
    if (!needBbox.empty())
    {
        opts["overlayBbox"] = needBbox;
    }

    string needTripwire = overlayJson.get("needTripwire", EMPTY_STRING).asString();
    if (!needTripwire.empty())
    {
        opts["overlayTripwire"] = needTripwire;
    }

    string needRoi = overlayJson.get("needRoi", EMPTY_STRING).asString();
    if (!needRoi.empty())
    {
        opts["overlayRoi"] = needRoi;
    }

    // Parse global objectId array (for backward compatibility)
    Json::Value overlay_objects_json = overlayJson.get("objectId", EMPTY_STRING);
    if(overlay_objects_json.isArray())
    {
        string overlay_objects = "";
        for (auto x: overlay_objects_json)
        {
            overlay_objects += x.asString() + ",";
        }
        // Remove trailing comma
        if (!overlay_objects.empty() && overlay_objects.back() == ',')
        {
            overlay_objects.pop_back();
        }
        if (!overlay_objects.empty())
        {
            opts["overlayObjectId"] = overlay_objects;
        }
    }

    // Parse global classType array (for backward compatibility)
    Json::Value overlay_class_type_json = overlayJson.get("classType", EMPTY_STRING);
    if(overlay_class_type_json.isArray())
    {
        string overlay_class_type = "";
        for (auto x: overlay_class_type_json)
        {
            overlay_class_type += x.asString() + ",";
        }
        // Remove trailing comma
        if (!overlay_class_type.empty() && overlay_class_type.back() == ',')
        {
            overlay_class_type.pop_back();
        }
        if (!overlay_class_type.empty())
        {
            opts["overlayClassType"] = overlay_class_type;
        }
    }

    // Parse framerate
    string framerate = overlayJson.get("framerate", EMPTY_STRING).asString();
    if (!framerate.empty())
    {
        opts["framerate"] = framerate;
    }

    // Parse common global properties for old schema
    parseGlobalProperties(opts, overlayJson);
}

template<typename Compare>
void parseNewSchema(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson)
{
    // Set default values for overlay options
    opts["overlayBbox"] = "false";
    opts["overlayTripwire"] = "false";
    opts["overlayRoi"] = "false";

    // Parse bbox object for new schema
    Json::Value bbox = overlayJson.get("bbox", EMPTY_STRING);
    if (bbox.isObject())
    {
        string bboxObjects = "";
        string bboxClassType = "";
        string showAll = bbox.get("showAll", EMPTY_STRING).asString();
        if (!showAll.empty())
        {
            opts["bboxShowAll"] = showAll;
            if (showAll == "false")
            {
                Json::Value bboxObjectsArray = bbox.get("objectId", EMPTY_STRING);
                if(!bboxObjectsArray.empty())
                {
                    for (auto x: bboxObjectsArray)
                    {
                        bboxObjects += x.asString() + ",";
                    }
                    // Remove trailing comma
                    if (!bboxObjects.empty() && bboxObjects.back() == ',')
                    {
                        bboxObjects.pop_back();
                    }
                }
                if (!bboxObjects.empty())
                {
                    opts["bboxObjectId"] = bboxObjects;
                }

                Json::Value bboxClassTypeArray = bbox.get("classType", EMPTY_STRING);
                if(!bboxClassTypeArray.empty())
                {
                    for (auto x: bboxClassTypeArray)
                    {
                        bboxClassType += x.asString() + ",";
                    }
                    // Remove trailing comma
                    if (!bboxClassType.empty() && bboxClassType.back() == ',')
                    {
                        bboxClassType.pop_back();
                    }
                }
                if (!bboxClassType.empty())
                {
                    opts["bboxClassType"] = bboxClassType;
                }
            }
        }
        else
        {
            LOG(error) << "Missing parameter showAll from overlay-bbox" << endl;
        }

        // If showAll is false and bboxObjects is empty, set overlayBbox to false
        if (showAll == "false" && bboxObjects.empty() && bboxClassType.empty())
        {
            opts["overlayBbox"] = "false";
        }
        else
        {
            opts["overlayBbox"] = "true";
        }

        // Parse bbox-specific properties for new schema
        string showObjId = bbox.get("showObjId", EMPTY_STRING).asString();
        if (!showObjId.empty())
        {
            LOG(info) << "bboxShowObjId = " << showObjId << endl;
            opts["bboxShowObjId"] = showObjId;
        }
        int objIdPosition = bbox.get("objIdPosition", MIDDLE).asInt();
        if (objIdPosition >= MIDDLE && objIdPosition <= MAX_BBOX_ID_POSITION)
        {
            opts["bboxObjIdPosition"] = std::to_string(objIdPosition);
        }
        string objIdTextColor = bbox.get("objIdTextColor", EMPTY_STRING).asString();
        if (!objIdTextColor.empty())
        {
            opts["bboxObjIdTextColor"] = objIdTextColor;
        }
        string objIdTextBGColor = bbox.get("objIdTextBGColor", EMPTY_STRING).asString();
        if (!objIdTextBGColor.empty())
        {
            opts["bboxObjIdTextBGColor"] = objIdTextBGColor;
        }
    }

    // Parse tripwire object for new schema
    Json::Value tripwire = overlayJson.get("tripwire", EMPTY_STRING);
    if (tripwire.isObject())
    {
        string tripwire_objects = "";
        string showAll = tripwire.get("showAll", EMPTY_STRING).asString();
        if (!showAll.empty())
        {
            opts["tripwireShowAll"] = showAll;
            if (showAll == "false")
            {
                Json::Value tripwireObjectsArray = tripwire.get("id", EMPTY_STRING);
                if(!tripwireObjectsArray.empty())
                {
                    for (auto x: tripwireObjectsArray)
                    {
                        tripwire_objects += x.asString() + ",";
                    }
                    // Remove trailing comma
                    if (!tripwire_objects.empty() && tripwire_objects.back() == ',')
                    {
                        tripwire_objects.pop_back();
                    }
                }
            }
            if (!tripwire_objects.empty())
            {
                opts["tripwireObjectId"] = tripwire_objects;
            }
        }
        else
        {
            LOG(error) << "Missing parameter showAll from overlay-tripwire" << endl;
        }

        // If showAll is false and tripwireObjects is empty, set overlayTripwire to false
        if (showAll == "false" && tripwire_objects.empty())
        {
            opts["overlayTripwire"] = "false";
        }
        else
        {
            opts["overlayTripwire"] = "true";
        }
    }

    // Parse roi object for new schema
    Json::Value roi = overlayJson.get("roi", EMPTY_STRING);
    if (roi.isObject())
    {
        string roi_objects = "";
        string showAll = roi.get("showAll", EMPTY_STRING).asString();
        if (!showAll.empty())
        {
            opts["roiShowAll"] = showAll;
            if (showAll == "false")
            {
                Json::Value roiObjectsArray = roi.get("id", EMPTY_STRING);
                if(!roiObjectsArray.empty())
                {
                    for (auto x: roiObjectsArray)
                    {
                        roi_objects += x.asString() + ",";
                    }
                    // Remove trailing comma
                    if (!roi_objects.empty() && roi_objects.back() == ',')
                    {
                        roi_objects.pop_back();
                    }
                }
            }
            if (!roi_objects.empty())
            {
                opts["roiObjectId"] = roi_objects;
            }
        }
        else
        {
            LOG(error) << "Missing parameter showAll from overlay-roi" << endl;
        }

        // If showAll is false and roiObjects is empty, set overlayRoi to false
        if (showAll == "false" && roi_objects.empty())
        {
            opts["overlayRoi"] = "false";
        }
        else
        {
            opts["overlayRoi"] = "true";
        }
    }

    // Parse common global properties for new schema
    parseGlobalProperties(opts, overlayJson);
}

template<typename Compare>
void parseGlobalProperties(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson)
{

    // Parse global overlay properties (common to both schemas)
    opts["overlayColor"] = overlayJson.get("color", EMPTY_STRING).asString();
    opts["overlayThickness"] = overlayJson.get("thickness", EMPTY_STRING).asString();
    opts["overlayOpacity"] = overlayJson.get("opacity", EMPTY_STRING).asString();
    opts["overlayDebug"] = overlayJson.get("debug", EMPTY_STRING).asString();
    opts["overlayPose"] = overlayJson.get("pose", EMPTY_STRING).asString();

    // Parse proximityClass array
    Json::Value proximity_class_json = overlayJson.get("proximityClass", EMPTY_STRING);
    if(proximity_class_json.isArray())
    {
        string proximity_class = "";
        for (Json::Value::ArrayIndex i = 0; i < proximity_class_json.size(); i++)
        {
            proximity_class += proximity_class_json[i].asString();
            if (i < proximity_class_json.size() - 1)
            {
                proximity_class += ",";
            }
        }
        opts["overlayProximityClass"] = proximity_class;
    }

    // Parse entrantClass array
    Json::Value entrant_class_json = overlayJson.get("entrantClass", EMPTY_STRING);
    if(entrant_class_json.isArray())
    {
        string entrant_class = "";
        for (Json::Value::ArrayIndex i = 0; i < entrant_class_json.size(); i++)
        {
            entrant_class += entrant_class_json[i].asString();
            if (i < entrant_class_json.size() - 1)
            {
                entrant_class += ",";
            }
        }
        opts["overlayEntrantClass"] = entrant_class;
    }

    opts["overlayProximityAreaFactor"] = overlayJson.get("proximityAreaFactor", EMPTY_STRING).asString();
    opts["overlayProximityAnimation"] = overlayJson.get("proximityAnimation", EMPTY_STRING).asString();

    // Parse overlayColorCode array
    Json::Value overlay_color_code = overlayJson.get("overlayColorCode", EMPTY_STRING);
    if (overlay_color_code.isArray())
    {
        string colorCodeStr = "";
        bool firstEntry = true;

        for (const auto& colorEntry : overlay_color_code)
        {
            if (colorEntry.isObject())
            {
                Json::Value::Members members = colorEntry.getMemberNames();
                for (Json::Value::ArrayIndex i = 0; i < members.size(); i++)
                {
                    std::string key = members[i];
                    const Json::Value& colorArray = colorEntry[key];

                    if (colorArray.isArray() && colorArray.size() == 4)
                    {
                        if (!firstEntry)
                        {
                            colorCodeStr += ",";
                        }
                        firstEntry = false;

                        // Convert integers to strings and join with colons
                        std::string r = std::to_string(colorArray[0].asInt());
                        std::string g = std::to_string(colorArray[1].asInt());
                        std::string b = std::to_string(colorArray[2].asInt());
                        std::string a = std::to_string(colorArray[3].asInt());

                        colorCodeStr += key + "=" + r + ":" + g + ":" + b + ":" + a;
                    }
                }
            }
        }

        if (!colorCodeStr.empty())
        {
            opts["overlayColorCode"] = colorCodeStr;
        }
    }

    // Parse needHalo (common property)
    string needHalo = overlayJson.get("needHalo", EMPTY_STRING).asString();
    if (!needHalo.empty())
    {
        opts["overlayHalos"] = needHalo;
    }
}

template void setOverlayOptsBasedOnJson<std::less<std::string>>(
    std::map<std::string, std::string, std::less<std::string>>& opts, const Json::Value& overlayJson);
template void setOverlayOptsBasedOnJson<std::less<void>>(
    std::map<std::string, std::string, std::less<void>>& opts, const Json::Value& overlayJson);

template void parseOldSchema<std::less<std::string>>(
    std::map<std::string, std::string, std::less<std::string>>& opts, const Json::Value& overlayJson);
template void parseOldSchema<std::less<void>>(
    std::map<std::string, std::string, std::less<void>>& opts, const Json::Value& overlayJson);

template void parseNewSchema<std::less<std::string>>(
    std::map<std::string, std::string, std::less<std::string>>& opts, const Json::Value& overlayJson);
template void parseNewSchema<std::less<void>>(
    std::map<std::string, std::string, std::less<void>>& opts, const Json::Value& overlayJson);

template void parseGlobalProperties<std::less<std::string>>(
    std::map<std::string, std::string, std::less<std::string>>& opts, const Json::Value& overlayJson);
template void parseGlobalProperties<std::less<void>>(
    std::map<std::string, std::string, std::less<void>>& opts, const Json::Value& overlayJson);
