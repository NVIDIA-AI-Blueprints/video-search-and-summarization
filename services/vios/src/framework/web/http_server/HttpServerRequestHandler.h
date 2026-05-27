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

#include <map>
#include <vector>
#include <memory>
#include <functional>
#include <jsoncpp/json/json.h>

#include "CivetServer.h"
#include "error_code.h"

using namespace nv_vms;

// Function to get CivetCallbacks for debugging
const struct CivetCallbacks* getCivetCallbacks();

/* ---------------------------------------------------------------------------
**  http callback
** -------------------------------------------------------------------------*/
class HttpServerRequestHandler
{
    std::shared_ptr<CivetServer> m_civet;
    std::vector<std::unique_ptr<CivetHandler>> m_handlers;  // Store handlers to manage lifetime
    public:
        typedef std::function<VmsErrorCode(const Json::Value &, const Json::Value &, Json::Value &, struct mg_connection *conn)> httpFunction;

        HttpServerRequestHandler(std::shared_ptr<CivetServer> m_civetServer);

        void addRequestHandler(std::map<std::string,httpFunction, std::less<>>& func);
        
        static void initializeTracing(const std::string& otlp_endpoint = "http://localhost:4318/v1/traces",
                                     const std::string& service_name = "vms-http-server");
        
        static void shutdownTracing();
};

struct FileData
{
    struct mg_connection *m_conn;
    bool m_isFileReceived = false;
    bool m_isFileSaved = false;
    bool m_isLastChunk = false;
    bool m_isChunkedUpload = false;
    size_t m_fileSize = 0;
    std::string m_chunkIdentifier = EMPTY_STRING;
    std::string m_absoluteFilePath = EMPTY_STRING;
    std::string m_tempDirectory = EMPTY_STRING;
    std::string m_fileName = EMPTY_STRING;
    std::string m_mediaFilePath = EMPTY_STRING;
    std::string m_metaDataFilePath = EMPTY_STRING;
    
    // JSON metadata specific fields
    std::string m_jsonMetadata = EMPTY_STRING;
    Json::Value m_parsedMetadata = Json::nullValue;
    bool m_hasMetadata = false;
    
    // Error handling fields
    bool m_hasError = false;
    VmsErrorCode m_errorCode = VmsErrorCode::NoError;
    std::string m_errorMessage = EMPTY_STRING;

    // True iff this upload created a new sensor in addFile(); false on the
    // merge path. Used by post-addFile rollback to avoid deleting a sensor
    // it did not create. Bug 5757067.
    bool m_sensorCreatedByUpload = false;

    // Stream id added by this upload on the merge path. Used by rollback to
    // drop just the new stream so a pre-existing sensor isn't left with a
    // record pointing at a deleted file. Empty in the create path.
    std::string m_mergedStreamId = EMPTY_STRING;
};


