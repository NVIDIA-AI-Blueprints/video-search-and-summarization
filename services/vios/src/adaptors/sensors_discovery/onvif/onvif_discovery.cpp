/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "onvif_discovery.h"
#include "utils.h"
#include "config.h"
#include "macros.h"
#include "logger.h"
#include "network_utils.h"
#include "nvsoap.h"
#include <random>
#include <openssl/sha.h>
#include <openssl/evp.h>
#include <openssl/buffer.h>
#include <curl/curl.h>
#include <iomanip>
#include <sstream>
#include <ctime>

using namespace nv_vms;

#define DEFAULT_CAMERA_NAME "Camera"

// Prepare structures for async CURL operations
struct SensorCurlData {
    CURL* curl;
    std::string url;
    std::string soap_body;
    std::string response;
    struct curl_slist* headers;
    shared_ptr<SensorInfo> sensor;

    SensorCurlData() : curl(nullptr), headers(nullptr) {}

    ~SensorCurlData()
    {
        if (headers)
        {
            curl_slist_free_all(headers);
        }
        if (curl)
        {
            curl_easy_cleanup(curl);
        }
    }
};

extern "C" ISensorDiscoveryInterface* createObject()
{
    return new OnvifDiscovery;
}

extern "C" void destroyObject( OnvifDiscovery* object )
{
    delete object;
}

OnvifDiscovery::OnvifDiscovery()
{
    // Initialize CURL multi handle for sensor synchronization
    m_sensorSyncMultiHandle = curl_multi_init();
    if (!m_sensorSyncMultiHandle)
    {
        LOG(error) << "Failed to initialize reusable CURL multi handle" << endl;
    }
}

OnvifDiscovery::~OnvifDiscovery()
{
    LOG(info) << "Destroying onvif disclovery" << endl;
    stop();

    // Cleanup CURL multi handle
    if (m_sensorSyncMultiHandle)
    {
        curl_multi_cleanup(m_sensorSyncMultiHandle);
        m_sensorSyncMultiHandle = nullptr;
    }
}

void OnvifDiscovery::start()
{
    std::lock_guard<std::mutex> sensorsLock(m_monitorMutex);
    LOG(info) << "Starting Sensor discovery tasks" << endl;
    std::vector<shared_ptr<SensorInfo>> cache_list = getCacheSensorList();
    m_exit = false;
    m_onvifListnerThread = std::thread([this] { this->onvifListnerTask(); });
    m_monitorThread = std::thread([this] { this->onvifSensorMonitorTask(); });
}
void  OnvifDiscovery::stop()
{
    std::lock_guard<std::mutex> sensorsLock(m_monitorMutex);
    m_exit = true;
    stopOnvifDiscovery();
    m_sleeperWait.notify_all();
    m_monitorThread.join();
    m_onvifListnerThread.join();
}

int OnvifDiscovery::searchSensor(SensorInfo& sensor)
{
    int ret = -1;
    stop();
    ret = sendProbeToSensor(sensor);
    if(ret == 0)
    {
        sensor.updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
        sensor.type = SENSOR_TYPE_ONVIF;
        sensor.updateHttpErrorStatus(std::make_pair(200, "No Error"));
        sensor.isAutoDiscovered = false;
    }
    else
    {
        LOG(error) << "Sensor not found: " << sensor.ip << endl;
    }
    start();
    return ret;
}

int OnvifDiscovery::addNewSensor(SensorInfo& sensor)
{
    sensor.name = sensor.name.empty() ? DEFAULT_CAMERA_NAME : sensor.name;
    sensor.type = SENSOR_TYPE_ONVIF;
    sensor.isAutoDiscovered = true;
    sensor.updateSensorStatus(SensorStatusEvent::SensorStatusOnline);
    return publishOnSensorFound(sensor);
}

void OnvifDiscovery::onvifListnerTask()
{
    vector<shared_ptr<SensorInfo>> backlist = VmsConfigManager::getInstance()->getCameraBackList();

    int retryCount = 3;
    do
    {
        int retValue = openProbe();
        if (retValue == 0)
        {
            LOG(info) << "Opening of probe port success" << endl;
            break;
        }
        else
        {
            closeProbe();
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            if (retryCount == 0)
            {
                LOG(info) << "Opening of probe port failed" << endl;
                m_exit = true;
            }
        }
    } while (--retryCount >= 0);

    while (m_exit == false)
    {
        SensorInfo sensor;
        if (getProbeMatch(sensor) == 0)
        {
            LOG(verbose2) << "sensor url: " << sensor.url << endl;
            std::lock_guard<std::mutex> sensorsLock(m_queuemutex);
            bool process_sensor = true;
            for (uint32_t i = 0; i < backlist.size(); i++)
            {
                if(sensor.ip.find(backlist[i]->ip) != std::string::npos)
                {
                    LOG(verbose) << "Sensor: " << sensor.ip << " is black listed" << endl;
                    process_sensor = false;
                    break;
                }
            }
            if (process_sensor)
            {
                m_queue.push(sensor);
            }
        }
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    closeProbe();
    LOG(info) << "Exiting from onvifListnerTask thread.." << endl;
}

void OnvifDiscovery::onvifSensorMonitorTask()
{
    while (m_exit == false)
    {
        m_freshList.clear();
        sendProbe();
        {
            std::lock_guard<std::mutex> sensorsLock(m_queuemutex);
            do
            {
                if (m_queue.empty() == false)
                {
                    bool duplicateSensorFound;
                    SensorInfo sensorNew = m_queue.front();
                    m_queue.pop();

                    duplicateSensorFound = false;
                    for (auto it : m_freshList)
                    {
                        SensorInfo& sensorOld = it.second;
                        if (sensorOld.ip == sensorNew.ip)
                        {
                            duplicateSensorFound = true;
                            break;
                        }
                    }
                    if (!duplicateSensorFound)
                    {
                        m_freshList[sensorNew.sensorId] = sensorNew;
                    }
                }
            } while(m_queue.size());
        }
        refreshCacheSensorList();
        std::vector<shared_ptr<SensorInfo>> cache_list = getCacheSensorList();
        LOG(verbose) << "*********[Fresh List]************* " << "[" << m_freshList.size()  << "]" << endl;
        for (auto it : m_freshList) // Camera detected from network
        {
            SensorInfo& sensor = it.second;
            LOG(verbose) << "Fresh Sensor URL: " << sensor.url << " id:" << sensor.sensorId << endl;
        }
        LOG(verbose) << "**********[END]************" << endl;

        LOG(verbose) << "*********[Cache List]************* " << "[" << cache_list.size()  << "]" << endl;
        for (auto sensor : cache_list) // Camera detected from network
        {
            LOG(verbose) << "Cache Sensor URL: " << sensor->url << " id:" << sensor->sensorId << endl;
        }
        LOG(verbose) << "**********[END]************" << endl;

        /* Check and synchronize date & time of the sensors as per current time */
        synchronizeDateAndTime(cache_list);

        /* Check for removal of camera */
        for (auto cache : cache_list)
        {
            const string& cache_sensor_id = cache->sensorId;
            auto cache_sensor = cache;
            bool is_sensor_offline = false;

            if (cache_sensor->type != SENSOR_TYPE_ONVIF)
            {
                continue;
            }

            is_sensor_offline = (isCameraOnline(*cache_sensor) == false);
            if (is_sensor_offline && cache_sensor->getSensorStatus() == SensorStatusOnline)
            {
                LOG(error) << "Removing sensor: " << cache_sensor->ip << endl;
                publishOnSensorRemoved(cache_sensor_id);
            }
        }

        /* Check for new camera */
        for (auto fresh : m_freshList)
        {
            const string& sensor_id = fresh.first;
            SensorInfo& new_sensor = fresh.second;
            shared_ptr<SensorInfo> cache_sensor;
            if (new_sensor.url.empty() || new_sensor.ip.empty())
            {
                continue;
            }
            bool isSensorExistInCache = false;
            for (auto cache : cache_list)
            {

                if (cache->type != SENSOR_TYPE_ONVIF)
                {
                    continue;
                }

                if (cache && (cache->sensorId == sensor_id || new_sensor.ip == cache->ip))
                {
                    isSensorExistInCache = true;
                    cache_sensor = cache;
                    break;
                }
            }

            if (isSensorExistInCache == false)
            {
                if (isCameraOnline(new_sensor))
                {
                    if (addNewSensor(new_sensor) == 0)
                    {
                        LOG(info) << "Added new sensor: " << new_sensor.ip << endl;
                    }
                }
            }
            else
            {
                if (cache_sensor)
                {
                    // Check if change in ipAddr/url, update db in that case.
                    if (cache_sensor->ip != new_sensor.ip ||
                        cache_sensor->url != new_sensor.url)
                    {
                        LOG(info) << "IpAddress/url change detected for camera:" << cache_sensor->sensorId << "\t" <<
                                "old ipAddr:" << cache_sensor->ip << ", new ipAddr:" << new_sensor.ip << endl <<
                                "old url:" << cache_sensor->url << ", new url:" << new_sensor.url << endl;

                        if (isCameraOnline(new_sensor))
                        {
                            cache_sensor->ip = new_sensor.ip;
                            cache_sensor->url = new_sensor.url;
                            publishOnSensorFound(*cache_sensor);
                        }
                    }
                    else if ( cache_sensor->getSensorStatus() == SensorStatusOffline &&
                            isCameraOnline(new_sensor))
                    {
                        publishOnSensorFound(new_sensor);
                    }
                    else
                    {
                        // Nothing to be done, cache list is updated.
                    }
                }
            }
        }
        // Sleep for sensor_discovery_freq_secs or untill get notified.
        {
            std::unique_lock<std::mutex> lck(m_sleeperLock);
            /* Check if any milliseconds offsets, this is to synchronize cameras at millisecond level */
            struct timeval timeNow;
            gettimeofday(&timeNow, nullptr);
            int millisecond_offset = (timeNow.tv_usec / 1000) + GET_CONFIG().onvif_sensor_time_sync_compensation_ms; // compensation time is the time taken to process + network latency.
            int time_to_wait = GET_CONFIG().sensor_discovery_freq_secs * 1000 + (1000 - millisecond_offset);

            m_sleeperWait.wait_for(lck,std::chrono::milliseconds(time_to_wait));
        }
    }
    LOG(info) << "Exiting from onvifSensorMonitorTask thread.." << endl;
}

// Base64 encoding using EVP
std::string base64_encode(const unsigned char* input, int length)
{
    int base64_len = 4 * ((length + 2) / 3) + 1;
    std::vector<unsigned char> output(base64_len);

    int encoded_len = EVP_EncodeBlock(output.data(), input, length);
    if (encoded_len < 0) return "";

    return std::string(reinterpret_cast<char*>(output.data()), encoded_len);
}

// Generate cryptographically secure random bytes
std::vector<unsigned char> generate_nonce(size_t length)
{
    std::vector<unsigned char> nonce(length);
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<unsigned char> dis(0, 255);
    for (size_t i = 0; i < length; ++i)
    {
        nonce[i] = dis(gen);
    }
    return nonce;
}

// libcurl write callback
static size_t WriteCallback(void* contents, size_t size, size_t nmemb, void* userp)
{
    ((std::string*)userp)->append((char*)contents, size * nmemb);
    return size * nmemb;
}

// Generate WSSE header
std::string generate_wsse_header(const std::string& username,
    const std::string& password,
    const std::vector<unsigned char>& nonce = {},
    const std::string& created = "")
{
    // Generate nonce if not provided
    std::vector<unsigned char> nonce_vec = nonce.empty() ? generate_nonce(16) : nonce;

    // Generate created timestamp if not provided
    auto now = std::chrono::system_clock::now();
    auto now_c = std::chrono::system_clock::to_time_t(now);
    auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()) % 1000;
    std::tm tm_buf{};
    gmtime_r(&now_c, &tm_buf);

    std::ostringstream created_ss;
    created_ss << std::put_time(&tm_buf, "%Y-%m-%dT%H:%M:%S.")
               << std::setfill('0') << std::setw(3) << now_ms.count() << 'Z';
    std::string created_str = created.empty() ? created_ss.str() : created;

    if (!created_str.empty() && created_str.back() != 'Z')
    {
        created_str += 'Z';
    }

    // Base64 encode nonce
    std::string nonce_b64 = base64_encode(nonce_vec.data(), nonce_vec.size());

    // Compute password digest (SHA1(nonce + created + password))
    unsigned char digest[SHA_DIGEST_LENGTH];
    EVP_MD_CTX* md_ctx = EVP_MD_CTX_new();
    if (!md_ctx)
    {
        LOG(error) << "Failed to create EVP_MD_CTX" << endl;
        return "";
    }

    if (EVP_DigestInit_ex(md_ctx, EVP_sha1(), nullptr) != 1 ||
        EVP_DigestUpdate(md_ctx, nonce_vec.data(), nonce_vec.size()) != 1 ||
        EVP_DigestUpdate(md_ctx, created_str.data(), created_str.size()) != 1 ||
        EVP_DigestUpdate(md_ctx, password.data(), password.size()) != 1 ||
        EVP_DigestFinal_ex(md_ctx, digest, nullptr) != 1)
    {
        EVP_MD_CTX_free(md_ctx);
        LOG(error) << "Failed to compute SHA1 digest" << endl;
        return "";
    }

    EVP_MD_CTX_free(md_ctx);

    std::string password_digest = base64_encode(digest, SHA_DIGEST_LENGTH);

    // Construct XML
    std::ostringstream xml;
    xml << R"(<Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">)"
        << R"(<UsernameToken>)"
        << "<Username>" << username << "</Username>"
        << R"(<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">)"
        << password_digest << "</Password>"
        << R"(<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">)"
        << nonce_b64 << "</Nonce>"
        << R"(<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">)"
        << created_str << "</Created>"
        << "</UsernameToken></Security>";

    return xml.str();
}

// Generate SOAP body
std::string generate_soap_body(const std::string& username,
    const std::string& password,
    const std::tuple<string, string, string>& utcTime,
    const std::tuple<string, string, string>& date,
    const std::string& timezone_str = "GMT+00:00")
{
    // Generate WSSE header
    std::string wsse_header = generate_wsse_header(username, password);

    // Construct SOAP body
    std::ostringstream soap;
    soap << R"(<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">)"
         << "<s:Header>" << wsse_header << "</s:Header>"
         << R"(<s:Body>)"
         << R"(<SetSystemDateAndTime xmlns="http://www.onvif.org/ver10/device/wsdl">)"
         << "<DateTimeType>Manual</DateTimeType>"
         << "<DaylightSavings>false</DaylightSavings>"
         << "<TimeZone><TZ xmlns=\"http://www.onvif.org/ver10/schema\">"
         << timezone_str << "</TZ></TimeZone>"
         << "<UTCDateTime>"
         << "<Time xmlns=\"http://www.onvif.org/ver10/schema\">"
         << "<Hour>" << std::get<0>(utcTime) << "</Hour>"
         << "<Minute>" << std::get<1>(utcTime) << "</Minute>"
         << "<Second>" << std::get<2>(utcTime) << "</Second>"
         << "</Time>"
         << "<Date xmlns=\"http://www.onvif.org/ver10/schema\">"
         << "<Year>" << std::get<2>(date) << "</Year>"
         << "<Month>" << std::get<1>(date) << "</Month>"
         << "<Day>" << std::get<0>(date) << "</Day>"
         << "</Date></UTCDateTime>"
         << "</SetSystemDateAndTime></s:Body></s:Envelope>";

    return soap.str();
}

void OnvifDiscovery::synchronizeDateAndTime(std::vector<shared_ptr<SensorInfo>>& sensor_list)
{
    if (sensor_list.empty())
    {
        // No online sensors found for time synchronization
        return;
    }

    // Check if the time interval has passed
    if (m_timePrev != std::chrono::steady_clock::time_point::min())
    {
        std::chrono::steady_clock::time_point time_now = std::chrono::steady_clock::now();
        auto elapsed_seconds = std::chrono::duration_cast<std::chrono::seconds>(time_now - m_timePrev);
        if (elapsed_seconds.count() < GET_CONFIG().onvif_sensor_time_sync_interval_secs)
        {
            return;
        }
    }
    m_timePrev = std::chrono::steady_clock::now();

    // Calculate the time to be set for all cameras (same timestamp for all)
    std::tuple<string, string, string> utcTime = getCurrentTimeInHHMMSS();
    std::tuple<string, string, string> date = getCurrentDateInDDMMYYYY();
    int seconds = stringToInt(std::get<2>(utcTime), 0) + 1;
    if (seconds > 59)
    {
        seconds = 0;
        std::get<1>(utcTime) = std::to_string(stringToInt(std::get<1>(utcTime), 0) + 1);
    }
    std::get<2>(utcTime) = std::to_string(seconds);
    std::vector<std::unique_ptr<SensorCurlData>> curl_data_list;

    // Check if multi handle is available
    if (!m_sensorSyncMultiHandle)
    {
        LOG(error) << "CURL multi handle not available" << endl;
        return;
    }

    // Prepare CURL handles for all online sensors
    for (auto sensor : sensor_list)
    {
        if (sensor && sensor->getSensorStatus() == SensorStatusOnline
            && !sensor->user.empty() && !sensor->password.empty())
        {
            auto curl_data = std::make_unique<SensorCurlData>();
            curl_data->sensor = sensor;
            curl_data->url = "http://" + sensor->ip + "/onvif/device_service";
            curl_data->soap_body = generate_soap_body(sensor->user, sensor->password, utcTime, date);

            curl_data->curl = curl_easy_init();
            if (!curl_data->curl)
            {
                LOG(error) << "Failed to initialize CURL handle for sensor: " << sensor->name << endl;
                continue;
            }

            curl_data->headers = curl_slist_append(curl_data->headers, "Content-Type: application/soap+xml");

            curl_easy_setopt(curl_data->curl, CURLOPT_URL, curl_data->url.c_str());
            curl_easy_setopt(curl_data->curl, CURLOPT_POSTFIELDS, curl_data->soap_body.c_str());
            curl_easy_setopt(curl_data->curl, CURLOPT_HTTPHEADER, curl_data->headers);
            curl_easy_setopt(curl_data->curl, CURLOPT_WRITEFUNCTION, WriteCallback);
            curl_easy_setopt(curl_data->curl, CURLOPT_WRITEDATA, &curl_data->response);
            curl_easy_setopt(curl_data->curl, CURLOPT_TIMEOUT, 5L);
            curl_easy_setopt(curl_data->curl, CURLOPT_CONNECTTIMEOUT, 5L);
            // Add to multi handle
            CURLMcode multi_code = curl_multi_add_handle(m_sensorSyncMultiHandle, curl_data->curl);
            if (multi_code != CURLM_OK)
            {
                LOG(error) << "Failed to add CURL handle to multi handle for sensor: " << sensor->name << endl;
                continue;
            }
            curl_data_list.push_back(std::move(curl_data));
        }
    }

    if (curl_data_list.empty())
    {
        // No authorized sensors found for time synchronization
        return;
    }

    LOG(warning) << "Starting simultaneous time synchronization for " << curl_data_list.size()
        << " sensors" << ", with UTC time: " << std::get<0>(utcTime) << ":" << std::get<1>(utcTime) << ":"
        << std::get<2>(utcTime) << endl;

    // Execute all requests simultaneously
    int running_handles = 0;
    CURLMcode multi_code;

    // Start all transfers
    multi_code = curl_multi_perform(m_sensorSyncMultiHandle, &running_handles);
    if (multi_code != CURLM_OK)
    {
        LOG(error) << "Failed to start multi perform: " << curl_multi_strerror(multi_code) << endl;
        // Cleanup CURL handles
        for (auto& curl_data : curl_data_list)
        {
            curl_multi_remove_handle(m_sensorSyncMultiHandle, curl_data->curl);
        }
        return;
    }

    // Wait for all transfers to complete
    while (running_handles > 0)
    {
        int numfds = 0;
        // Wait for activity on any handle (much simpler than select)
        CURLMcode wait_code = curl_multi_wait(m_sensorSyncMultiHandle, nullptr, 0, 1000, &numfds);
        if (wait_code != CURLM_OK)
        {
            LOG(error) << "curl_multi_wait failed: " << curl_multi_strerror(wait_code) << endl;
            break;
        }

        // Perform transfers
        multi_code = curl_multi_perform(m_sensorSyncMultiHandle, &running_handles);
        if (multi_code != CURLM_OK)
        {
            LOG(error) << "curl_multi_perform failed: " << curl_multi_strerror(multi_code) << endl;
            break;
        }
    }

    // Process results
    CURLMsg* msg;
    int msgs_left = 0;
    while ((msg = curl_multi_info_read(m_sensorSyncMultiHandle, &msgs_left)))
    {
        if (msg->msg == CURLMSG_DONE)
        {
            CURL* curl_handle = msg->easy_handle;
            CURLcode result = msg->data.result;

            // Find corresponding sensor data
            for (auto& curl_data : curl_data_list)
            {
                if (curl_data->curl == curl_handle)
                {
                    long http_code = 0;
                    curl_easy_getinfo(curl_handle, CURLINFO_RESPONSE_CODE, &http_code);
                    if (result == CURLE_OK)
                    {
                        LOG(verbose) << "Time sync completed for sensor: " << curl_data->sensor->name
                                  << " HTTP code: " << http_code << endl;
                        if (http_code != 200)
                        {
                            LOG(error) << "Time sync failed for sensor: " << curl_data->sensor->name
                                  << " HTTP code: " << http_code << endl;
                            LOG(error) << "Time sync response: " << curl_data->response << endl;
                        }
                    }
                    else
                    {
                        LOG(error) << "Time sync failed for sensor: " << curl_data->sensor->name
                                  << " Error: " << curl_easy_strerror(result) << endl;
                    }
                    break;
                }
            }
        }
    }

    // Cleanup CURL handles
    for (auto& curl_data : curl_data_list)
    {
        if (curl_data && curl_data->curl)
        {
            curl_multi_remove_handle(m_sensorSyncMultiHandle, curl_data->curl);
            curl_easy_cleanup(curl_data->curl);
            curl_data->curl = nullptr;
        }
    }
    curl_data_list.clear();
}
