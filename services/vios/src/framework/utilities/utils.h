/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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


#define __METHOD_NAME__ methodName(__PRETTY_FUNCTION__)
#define __CLASS_NAME__ className(__PRETTY_FUNCTION__)

#define ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64 "/home/vst/vst_release/prebuilts/x86_64/"
#define ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64 "/home/vst/vst_release/prebuilts/aarch64/"
#define RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64 "prebuilts/aarch64/"

#define CONCATENATE_STRINGS(str1, str2) (str1 str2)

uint32_t getInitAvailableMemory();
void setInitAvailableMemory(uint32_t memory);
bool isGpuPresent();
void setGpuPresent(bool present);
int getGpuIndex();
void setGpuIndex(int index);
const std::string& getGpuNodePath();
void setGpuNodePath(const std::string& path);
std::string getHostIpAddress();
void setHostIpAddress(const std::string& hostIp);
// Set once at startup (detectGPU). When true, NvBufSurface allocations use
// CUDA-device memory (NVBUF_MEM_CUDA_DEVICE) instead of the Tegra default
// (NVBUF_MEM_DEFAULT): true on discrete-GPU aarch64 (Thor/SBSA/Spark), false on
// the integrated Tegra iGPU (Orin), which requires the default NVMM surfaces.
// Always declared so consumers in other libraries (nvsurfacepool, nvbufwrapper)
// compile on every platform.
bool isCudaDeviceMemoryEnabled();
void setCudaDeviceMemoryEnabled(bool enabled);

// Runtime platform detection. Returns true only on Jetson/Orin (integrated GPU
// behind the nvgpu driver). Replaces the former compile-time JETSON_PLATFORM macro
// so a single aarch64 build runs on both Orin and Thor/SBSA. Always false on x86.
// The result is probed once (via NvBufSurfaceGetDeviceInfo) and cached.
bool isJetsonPlatform();

// Security utility functions for masking sensitive data in logs
enum class MaskType {
    PASSWORD,           // Full masking with asterisks
    USERNAME,           // Partial masking (first and last char visible)
    FULL_MASK,          // Full masking with asterisks
    PARTIAL_MASK,       // Partial masking (first and last char visible)
    CUSTOM              // Custom masking pattern
};

bool replaceString(std::string& str, const std::string& from, const std::string& to);
void stripString(std::string& original);
void eraseString(std::string& orignal, const std::string& toErase);
void eraseString(std::string& orignal, const std::string& start, int len);
std::string jsonToString(const Json::Value& json);
std::vector<std::string> splitString(const std::string line, const std::string& search);
void insertString(std::string& orignal, const std::string& afterToken, const std::string& subString);
bool iequals(const std::string& a, const std::string& b);
std::string decimalToHex(const int& number);
bool findStringIgnoreCase(const std::string &str, const std::string &token);
bool isFloat( std::string myString );
bool isNumber(const std::string& s);
bool valueWithinRange(const std::string& value, const std::string& lower, const std::string& upper);
template <class Type>
bool findElement(std::vector<Type> v, Type entry);
std::string getHostIP();
int getPrefixLength(const std::string& netmask);
std::string getNetmaskFromPrefixLen(const int& prefixLength);
Json::Value loadVmsConfig();
Json::Value loadStorageConfig(const std::string& storage_config_file_path);
// parseError, when non-null, receives jsoncpp's message if the file is invalid.
// It cannot be logged here: this runs during VmsConfigManager static init, where
// LOG() would re-enter that same static.
Json::Value loadNotificationConfig(const std::string& notification_config_file_path,
                                   std::string* parseError = nullptr);
Json::Value scanCameraBackList();
Json::Value  getAdaptorInfo();
std::string getMediaAdaptorLibPath();
Json::Value  getOnvifInfo();
Json::Value readDeviceDetails();
std::string className(const std::string& prettyFunction);
std::string methodName(const std::string& prettyFunction);
std::time_t getEpocTime(const std::string time);
std::time_t getEpocTimeInMS(const std::string time, bool isISOTime = true);
int64_t parseTimeToEpochMs(const std::string& timeStr);
const std::string convertEpocToISO8601 (int64_t epoch64);
const std::string convertEpocToISO8601_2 (int64_t epoch64);
const std::string convertEpocNsToISO8601(uint64_t epoch64);
int64_t getDuration(const std::string startTime, const std::string endTime);
const std::string getCurrentTime();
const std::string getCurrentTimeMS();
std::tuple<std::string, std::string, std::string> getCurrentTimeInHHMMSS();
std::tuple<std::string, std::string, std::string> getCurrentDateInDDMMYYYY();
const std::string getCurrentUtcTime();
const std::string getOffsetUtcTime(int milliseconds);
const std::string convertEpocToHumanTime(int64_t epoc);
int64_t convertStringToSeconds(const std::string& str_time);
std::string getRelativeTimeUsingFrameId(const int64_t& frameId, const double& framerate);
int64_t getFrameIdUsingRelativeTime(const std::string& str_time, const double& framerate);
boost::posix_time::ptime stringToPosixTime(const std::string& str_time, float msScale);
const std::string posixTimeToString(const boost::posix_time::ptime& pt);
void posixTimeResolutionScale(float& secScale, float& msScale, float& usScale, float& nsScale);
std::string generate_uuid();
std::string convertUTCToHumanReadableFormat(const std::string& utcTimeStr);
std::string getUniqueIdFromUTCTime(const std::string& utcTimeStr, const std::string& prefix = "");
std::string sanitizeTimestampForFilename(const std::string& timeStr);
std::string sanitizePrefix(const std::string& raw);
std::pair<std::string, std::string> getCameraErrorCodeString(nv_vms::VmsErrorCode code);
nv_vms::VmsErrorCode getCameraErrorCode(const std::string& error);
std::pair<int, std::string> translateVmsErrorCodeToCameraHttpErrorCode(nv_vms::VmsErrorCode code);
nv_vms::VmsErrorCode translateCameraHttpErrorCodeToVmsErrorCode(int code);
nv_vms::StreamStatus stringToStreamStatus(const std::string& event);
std::string translateStreamStatusToString(nv_vms::StreamStatus value);
bool validateIpAddress(const std::string &ipAddress);
bool validateAndStripRtspUrl(std::string& url, std::string& ip, std::string& username, std::string& password);
bool ping(const std::string& ip);
bool pingHostname(const std::string& dnsName);
std::string getIPaddress(const std::string& url);
int getHostInfo(Json::Value& info);
void resolveEnvironmentVariable(const std::string env, std::string& out);
std::string getRedisServerEndpoint();
std::string getKafkaServerEndpoint();
std::string maskSensitiveData(const std::string& data, MaskType type = MaskType::PASSWORD);
std::string maskSensitiveData(const std::string& data, MaskType type, char maskChar, int visibleChars = 1);

// URL masking function to hide credentials in URLs
std::string secureUrlForLogging(const std::string& url);

// Mask presigned URLs by hiding sensitive query parameters
std::string maskPresignedUrl(const std::string& url);

// Media content type utilities
std::string getMediaContentType(const std::string& fileExtension);
std::time_t isoToEpoch(const std::string s, bool nanosec = false);
std::string getAbsolutePath(std::string rel_path);
Json::Value stringToJson(std::string in);
int runCMD(const std::string& cmd, std::string& result, bool strip_newline = true);
void setRecvMaxSocketBufferSize (uint32_t socket_buffer_size);
void setSendMaxSocketBufferSize (uint32_t socket_buffer_size);
std::string getUTCtoLocalTime(std::string utcTime);
std::string getUTCtoLocalISOTime(std::time_t utcTime);
std::vector<std::string> getNwInterfaceList();
Json::Value getBandwidth(std::string interface = "eth0");
int stringToInt(const std::string& str, const int value = 0);
std::string stringToHex(const std::string& str, bool conver_to_upper_case = false);
std::vector<uint8_t> toBytes(const std::string& input);
std::string hexToString(const std::string& in);
double stringToDouble(const std::string& str, const double& default_value = 0.0);
uint64_t getFileTimestamp(const std::string& filepath);
std::string vectorToString(std::vector<std::string>& vec);
std::string vectorToString(std::vector<int>& vec);
std::vector<std::string> stringToVector(const std::string& str);
Json::Value parseQueryStringToJson(const std::string& queryString);
std::string urlDecode(const std::string& encoded);
bool isValidQueryParamKey(const std::string& key);
bool isQueryStringSafe(const std::string& queryString);
std::string getFilePathFromUrl(const std::string& url, const std::string& token);
std::string getStreamIdFromUrl(const std::string& url, const std::string& token);
std::string toLowerCase(std::string& upper);
uint32_t getAvailableMemory();
Json::Value getSystemStats();
bool blockSensor(const std::string ip, std::string action);
Json::Value vectorToJson(const std::vector<std::string>& vec);
std::vector<std::string> jsonToVector(const Json::Value& jsonArray);
std::vector<int> jsonArrayToVector(const Json::Value& jsonArray);
std::string getRandomCommonName();
bool isJetsonGpuPresent();

std::string base64_decode(std::string const& encoded_string);
std::string base64_encode(char const* bytes_to_encode, unsigned int in_len);

std::string get_aes_key();
bool compareISOTime(const std::string& st, const std::string& et);
long timevaldiff(struct timeval& starttime, struct timeval& endtime);
int64_t convertTimeValToEpochMs (struct timeval& time);
int64_t convertTimeValToEpochSec(struct timeval& time);
int64_t getCurrentUnixTimestamp();
int64_t getCurrentUnixTimestampInMs();
std::string getIpAddrFromDnsName(const std::string dnsName);
bool checkWhiteSpace(const std::string str);
void removeWhiteSpaces(std::string& str);
void detectGPU();
bool validatePassword(const std::string& password);
bool isASCII (const std::string& s);
std::map<std::string, std::string, std::less<>> getStreamOptions(Json::Value in);
bool isSubstring(const std::string& target, const std::string& substring);
bool isSubstringCaseInsensitive(const std::string& target, const std::string& substring);
bool validateISOTime(const std::string& time);
uint64_t getTimestampInMilliSecond(uint64_t pts);
uint64_t getTimestampInMicroSecond(uint64_t pts);
uint64_t getTimestampInNanoSecond(uint64_t pts);
void extractUrlInfo(const std::string& url, std::string& protocol, std::string& ipOrHost, int& port, std::string& apiPath);
std::string removeTrailingSlashes(const std::string& str);
int getCurrentCoreId();
double findNearestValue(const std::vector<double>& numbers, double target);
std::string removeDecimals(double number);
nv_vms::AuthenticationMethods getSecuredAuthMethod(nv_vms::AuthenticationMethods supportedMethods);
long long stringToLong(const std::string& str, const long long value = 0);
std::string serialize(std::vector<std::string> &filePaths);
std::pair<int64_t, int64_t> getEpochTimeRangeFromIsoString(const std::string& timeRange);
std::string truncateString(const std::string& str, size_t limit);
int extractPort(const std::string& rtsp_url);
bool checkFileNameLength(const std::string str);
std::string normalizeRelativePath(const std::string& filePath, const std::string& basePath);
int64_t parseTimestampValue(const Json::Value& timestampValue);
std::string getIngressBaseUrl();
template<typename Compare>
void setOverlayOptsBasedOnJson(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson);
template<typename Compare>
void setCompositeOptsBasedOnJson(std::map<std::string, std::string, Compare>& opts,
                                 const Json::Value& compositeJson, const std::string& frameRate);
template<typename Compare>
void parseOldSchema(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson);
template<typename Compare>
void parseNewSchema(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson);
template<typename Compare>
void parseGlobalProperties(std::map<std::string, std::string, Compare>& opts, const Json::Value& overlayJson);
