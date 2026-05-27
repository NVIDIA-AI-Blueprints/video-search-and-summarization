/*
 * SPDX-FileCopyrightText: Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <regex>
#include "hlsmanager.h"
#include "rtspserver.h"

static void process_hls_message(std::shared_ptr<EventLoopData> data, void* parent)
{
    shared_ptr<HlsData> in_data = std::static_pointer_cast<HlsData>(data);
    shared_ptr<HlsOutData> out_data = std::static_pointer_cast<HlsOutData>(data->m_outResult);
    HLSManager* hls = static_cast <HLSManager*>(parent);
    std::map<std::string, std::string, std::less<>> opts = in_data->m_opts;
    Json::Value out;
    VmsErrorCode error_code = VmsErrorCode::NoError;
    string peerid = opts["peerid"];
    if (in_data == nullptr || hls == nullptr)
    {
        LOG(error) << "Received null in data" << endl;
        return;
    }
    if(in_data->m_expectResult)
    {
        if(out_data.get() == nullptr)
        {
            LOG(error) << "Received null out data" << endl;
            return;
        }
    }

    if(in_data->m_taskName == "start")
    {
        error_code = hls->start(opts, out);
    }
    else if(in_data->m_taskName == "stop")
    {
        error_code = hls->stop(peerid, out);
    }
    else
    {
        LOG(warning) << "Invalid message received " << endl;
    }

    if(in_data->m_expectResult)
    {
        out_data->m_error = error_code;
        out_data->m_response = out;
    }
}

HLSManager::HLSManager() : m_eventLoop("hls_event_loop", process_hls_message)
{
    
}

HLSManager::~HLSManager()
{
    LOG(info) << "Destroy HLS stream map" << endl;
    std::map<std::string, RTSPVideoCapturer*>::iterator it;
    for (it = m_streamMap.begin();  it != m_streamMap.end();)
    {
        std::map<std::string, RTSPVideoCapturer*>::iterator it_del = it;
        delete it_del->second;
        it = m_streamMap.erase(it_del);
    }
}

void HLSManager::start()
{
    m_eventLoop.setParent(this);
}

VmsErrorCode HLSManager::startStream(std::map<std::string, std::string, std::less<>> opts, Json::Value &response)
{
    return start(opts, response);
}

VmsErrorCode HLSManager::stopStream(const string& peerid, Json::Value &response)
{
    return stop(peerid, response);
}

static bool getInternalRtspUrl(const std::string& url, std::string& out_url) {
    // Define a regex pattern to match the RTSP URL and extract components
    std::regex urlRegex(R"(rtsp://(([^:@]+):([^:@]+)@)?([^:/]+)(:(\d+))?(/(.*))?)");
    std::smatch match;

    // Match the URL against the regex
    if (!std::regex_match(url, match, urlRegex)) {
        return false; // URL does not match the expected format
    }

    // Extract components from the regex match
    std::string username = match[2].str();  // Username (optional)
    std::string password = match[3].str();  // Password (optional)
    std::string ip = match[4].str();        // IP or hostname
    std::string port = match[6].str();      // Port (optional)
    std::string path = match[8].str();      // Path (optional)

    //string url_prefix = RtspServer::OriginalPrefix();
    //out_url = url_prefix + path;
    return true;
}

VmsErrorCode HLSManager::start(std::map<std::string, std::string, std::less<>> opts, Json::Value &response)
{
    LOG(info) << "Starting HLS streamimg, peerid " << opts["peerid"] << " url: " << opts["url"] << endl;
    std::pair <std::string, std::string> url_path;
    const string peerid = opts["peerid"];
    const string url = opts["url"];
    string out_url;

    getInternalRtspUrl(url, out_url);
    LOG(info) << "Converted internal url:" << out_url << endl;

    RTSPVideoCapturer* capture = nullptr;
    {
        std::lock_guard<std::mutex> peerlock(m_streamLock);
        std::map<std::string, RTSPVideoCapturer*>::iterator it = m_streamMap.find(peerid);
        if(it == m_streamMap.end())
        {
            capture = RTSPVideoCapturer::Create(out_url, opts);
            if (capture)
            {
                url_path = capture->getUrlsPath();
                m_streamMap[peerid] = capture;
            }
        }
        else
        {
            LOG(warning) << "Ignoring start stream requst for same peerid " << peerid << endl;
            response = false;
            return VmsErrorCode::NoError;
        }
    }

    string playlist_file = getCurrentDirPath() + string("/")+ url_path.first + getFileName(url_path.second);
    int attempts = 30;
    while(!isFileExist(playlist_file) && attempts)
    {
        if (!checkIfPeerPresent(peerid))
        {
            LOG(error) << "Peer got removed peerid:" << peerid << endl;
            break;
        }
        LOG(verbose) << "Waiting for playlist to be created" << endl;
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        --attempts;
    }
    if (isFileExist(playlist_file) == false)
    {
        LOG(error) << "Failed to create playlist: url: " << url << " peerid: " << peerid << ", playlist_file:" << playlist_file << endl;
        if (capture)
        {
            std::lock_guard<std::mutex> peerlock(m_streamLock);
            std::map<std::string, RTSPVideoCapturer*>::iterator it = m_streamMap.find(peerid);
            if(it != m_streamMap.end())
            {
                delete it->second;
                m_streamMap.erase(it);
            }
        }
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, "Failed to create playlist")
        return VmsErrorCode::VMSInternalError;
    }
    response["url_path"] = url_path.second;
    response["url"] = VmsConfigManager::getInstance()->getWebServerUrl() + url_path.second;
    LOG(info) << "HLS playlist url: " << response.toStyledString() << endl;
    return VmsErrorCode::NoError;
}

VmsErrorCode HLSManager::stop(const string peerid, Json::Value &response)
{
    std::lock_guard<std::mutex> peerlock(m_streamLock);
    LOG(info) << "Stoping HLS streamimg " << peerid << endl;
    std::map<std::string, RTSPVideoCapturer*>::iterator it = m_streamMap.find(peerid);
    if(it != m_streamMap.end())
    {
        delete it->second;
        m_streamMap.erase(it);
    }
    else
    {
        LOG(warning) << "Peerid not found " << peerid << endl;
    }
    return VmsErrorCode::NoError;
}

bool HLSManager::checkIfPeerPresent(const string& peerid)
{
    std::lock_guard<std::mutex> peerlock(m_streamLock);
    bool peer_present = false;
    std::map<std::string, RTSPVideoCapturer*>::iterator it = m_streamMap.find(peerid);
    if(it != m_streamMap.end())
    {
        peer_present = true;
    }
    return peer_present;
}

VmsErrorCode HLSManager::postToEventLoop(const string& task_name, std::map<std::string, std::string, std::less<>>& opts,
                                    Json::Value& response, bool is_async, uint32_t timeout)
{
    std::shared_ptr<HlsData> in_data(new HlsData);
    in_data->m_taskName = task_name;
    in_data->m_msgId = opts["peerid"];
    in_data->m_opts = opts;
    VmsErrorCode error_code = VmsErrorCode::NoError;
    std::shared_ptr<HlsOutData> out_data;
    if(is_async)
    {
        out_data.reset(new HlsOutData);
        if (timeout)
        {
            out_data->m_timeout = timeout;
        }
        in_data->m_outResult = out_data;
        in_data->m_expectResult = is_async;
    }
    bool ret = m_eventLoop.postMsg(in_data);
    if(is_async && ret)
    {
        if (out_data.get())
        {
            response = out_data->m_response;
            error_code =  out_data->m_error;
        }
    }
    else
    {
        if (ret)
        {
            response = true;
            error_code = VmsErrorCode::NoError;
        }
        else
        {
            response = false;
            error_code = VmsErrorCode::VMSInternalError;
        }
    }
    return error_code;
}
