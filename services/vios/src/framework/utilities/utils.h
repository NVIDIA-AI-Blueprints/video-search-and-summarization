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

#pragma once

#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <vector>
#include <jsoncpp/json/json.h>
#include <ctime>
#include <openssl/evp.h>
#include <openssl/aes.h>
#include <boost/date_time/posix_time/posix_time.hpp>

#include "error_code.h"
#include "sensor_info.h"

using namespace std;
using namespace nv_vms;

#define __METHOD_NAME__ methodName(__PRETTY_FUNCTION__)
#define __CLASS_NAME__ className(__PRETTY_FUNCTION__)

#define ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64 "/home/vst/vst_release/prebuilts/x86_64/"
#define ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64 "/home/vst/vst_release/prebuilts/aarch64/"
#define RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64 "prebuilts/aarch64/"

#define CONCATENATE_STRINGS(str1, str2) (str1 str2)

extern uint32_t g_init_avaiable_memory;
extern bool g_isGpuPresent;
extern int g_gpuIndex;
extern string g_gpuNodePath;
extern string g_hostIp;
#ifdef JETSON_PLATFORM
extern bool g_isJetsonGpuMode;
#endif

// Security utility functions for masking sensitive data in logs
enum class MaskType {
    PASSWORD,           // Full masking with asterisks
    USERNAME,           // Partial masking (first and last char visible)
    FULL_MASK,          // Full masking with asterisks
    PARTIAL_MASK,       // Partial masking (first and last char visible)
    CUSTOM              // Custom masking pattern
};

bool replaceString(std::string& str, const std::string& from, const std::string& to);
void stripString(string& original);
void eraseString(string& orignal, const string& toErase);
void eraseString(string& orignal, const string& start, int len);
string jsonToString(const Json::Value& json);
vector<string> splitString(const string line, const string& search);
void insertString(string& orignal, const string& afterToken, const string& subString);
bool iequals(const string& a, const string& b);
string decimalToHex(const int& number);
bool findStringIgnoreCase(const std::string &str, const std::string &token);
bool isFloat( string myString );
bool isNumber(const std::string& s);
bool valueWithinRange(const string& value, const string& lower, const string& upper);
template <class Type>
bool findElement(vector<Type> v, Type entry);
string getHostIP();
int getPrefixLength(const string& netmask);
string getNetmaskFromPrefixLen(const int& prefixLength);
Json::Value loadVmsConfig();
Json::Value loadStorageConfig(const string& storage_config_file_path);
Json::Value scanCameraBackList();
Json::Value  getAdaptorInfo();
string getMediaAdaptorLibPath();
Json::Value  getOnvifInfo();
Json::Value readDeviceDetails();
string className(const string& prettyFunction);
string methodName(const string& prettyFunction);
std::time_t getEpocTime(const string time);
std::time_t getEpocTimeInMS(const string time, bool isISOTime = true);
int64_t parseTimeToEpochMs(const string& timeStr);
const std::string convertEpocToISO8601 (int64_t epoch64);
const std::string convertEpocToISO8601_2 (int64_t epoch64);
const std::string convertEpocNsToISO8601(uint64_t epoch64);
int64_t getDuration(const string startTime, const string endTime);
const std::string getCurrentTime();
const std::string getCurrentTimeMS();
std::tuple<std::string, std::string, std::string> getCurrentTimeInHHMMSS();
std::tuple<std::string, std::string, std::string> getCurrentDateInDDMMYYYY();
const std::string getCurrentUtcTime();
const std::string getOffsetUtcTime(int milliseconds);
const std::string convertEpocToHumanTime(int64_t epoc);
int64_t convertStringToSeconds(const string& str_time);
string getRelativeTimeUsingFrameId(const int64_t& frameId, const double& framerate);
int64_t getFrameIdUsingRelativeTime(const string& str_time, const double& framerate);
boost::posix_time::ptime stringToPosixTime(const string& str_time, float msScale);
const std::string posixTimeToString(const boost::posix_time::ptime& pt);
void posixTimeResolutionScale(float& secScale, float& msScale, float& usScale, float& nsScale);
string generate_uuid();
string convertUTCToHumanReadableFormat(const string& utcTimeStr);
string getUniqueIdFromUTCTime(const string& utcTimeStr, const string& prefix = "");
string sanitizeTimestampForFilename(const string& timeStr);
string sanitizePrefix(const string& raw);
std::pair<string, string> getCameraErrorCodeString(VmsErrorCode code);
VmsErrorCode getCameraErrorCode(const string& error);
std::pair<int, string> translateVmsErrorCodeToCameraHttpErrorCode(VmsErrorCode code);
VmsErrorCode translateCameraHttpErrorCodeToVmsErrorCode(int code);
StreamStatus stringToStreamStatus(const string& event);
string translateStreamStatusToString(StreamStatus value);
bool validateIpAddress(const string &ipAddress);
bool validateAndStripRtspUrl(string& url, string& ip, string& username, string& password);
bool ping(const string& ip);
bool pingHostname(const string& dnsName);
string getIPaddress(const string& url);
int getHostInfo(Json::Value& info);
void resolveEnvironmentVariable(const string env, string& out);
string getRedisServerEndpoint();
string getKafkaServerEndpoint();
string maskSensitiveData(const string& data, MaskType type = MaskType::PASSWORD);
string maskSensitiveData(const string& data, MaskType type, char maskChar, int visibleChars = 1);

// URL masking function to hide credentials in URLs
string secureUrlForLogging(const string& url);

// Mask presigned URLs by hiding sensitive query parameters
string maskPresignedUrl(const string& url);

// Media content type utilities
string getMediaContentType(const string& fileExtension);
std::time_t isoToEpoch(const string s, bool nanosec = false);
string getAbsolutePath(string rel_path);
Json::Value stringToJson(string in);
int runCMD(const string& cmd, string& result, bool strip_newline = true);
void setRecvMaxSocketBufferSize (uint32_t socket_buffer_size);
void setSendMaxSocketBufferSize (uint32_t socket_buffer_size);
std::string getUTCtoLocalTime(string utcTime);
std::string getUTCtoLocalISOTime(std::time_t utcTime);
vector<string> getNwInterfaceList();
Json::Value getBandwidth(string interface = "eth0");
int stringToInt(const std::string& str, const int value = 0);
string stringToHex(const string& str, bool conver_to_upper_case = false);
std::vector<uint8_t> toBytes(const std::string& input);
std::string hexToString(const std::string& in);
double stringToDouble(const std::string& str, const double& default_value = 0.0);
uint64_t getFileTimestamp(const string& filepath);
string vectorToString(vector<string>& vec);
string vectorToString(vector<int>& vec);
vector<string> stringToVector(const string& str);
Json::Value parseQueryStringToJson(const std::string& queryString);
std::string urlDecode(const std::string& encoded);
bool isValidQueryParamKey(const std::string& key);
bool isQueryStringSafe(const std::string& queryString);
string getFilePathFromUrl(const string& url, const string& token);
string getStreamIdFromUrl(const string& url, const string& token);
string toLowerCase(string& upper);
uint32_t getAvailableMemory();
Json::Value getSystemStats();
bool blockSensor(const string ip, string action);
Json::Value vectorToJson(const std::vector<string>& vec);
std::vector<string> jsonToVector(const Json::Value& jsonArray);
std::vector<int> jsonArrayToVector(const Json::Value& jsonArray);
string getRandomCommonName();
#ifdef JETSON_PLATFORM
bool isJetsonGpuPresent();
#endif

std::string base64_decode(std::string const& encoded_string);
std::string base64_encode(char const* bytes_to_encode, unsigned int in_len);

string get_aes_key();
bool compareISOTime(const string& st, const string& et);
long timevaldiff(struct timeval& starttime, struct timeval& endtime);
int64_t convertTimeValToEpochMs (struct timeval& time);
int64_t convertTimeValToEpochSec(struct timeval& time);
int64_t getCurrentUnixTimestamp();
int64_t getCurrentUnixTimestampInMs();
string getIpAddrFromDnsName(const string dnsName);
bool checkWhiteSpace(const string str);
void removeWhiteSpaces(string& str);
void detectGPU();
bool validatePassword(const string& password);
bool isASCII (const string& s);
std::map<std::string, std::string, std::less<>> getStreamOptions(Json::Value in);
bool isSubstring(const std::string& target, const std::string& substring);
bool isSubstringCaseInsensitive(const std::string& target, const std::string& substring);
bool validateISOTime(const string& time);
uint64_t getTimestampInMilliSecond(uint64_t pts);
uint64_t getTimestampInMicroSecond(uint64_t pts);
uint64_t getTimestampInNanoSecond(uint64_t pts);
void extractUrlInfo(const std::string& url, string& protocol, string& ipOrHost, int& port, string& apiPath);
std::string removeTrailingSlashes(const std::string& str);
int getCurrentCoreId();
double findNearestValue(const std::vector<double>& numbers, double target);
std::string removeDecimals(double number);
AuthenticationMethods getSecuredAuthMethod(AuthenticationMethods supportedMethods);
long long stringToLong(const std::string& str, const long long value = 0);
string serialize(vector<string> &filePaths);
pair<int64_t, int64_t> getEpochTimeRangeFromIsoString(const string& timeRange);
std::string truncateString(const std::string& str, size_t limit);
int extractPort(const std::string& rtsp_url);
bool checkFileNameLength(const string str);
string normalizeRelativePath(const string& filePath, const string& basePath);
int64_t parseTimestampValue(const Json::Value& timestampValue);
string getIngressBaseUrl();
template<typename Compare>
void setOverlayOptsBasedOnJson(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson);
template<typename Compare>
void parseOldSchema(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson);
template<typename Compare>
void parseNewSchema(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson);
template<typename Compare>
void parseGlobalProperties(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson);
