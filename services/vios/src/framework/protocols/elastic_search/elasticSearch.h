/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <glib.h>
#include <memory>
#include <mutex>
#include <queue>
#include <tuple>

#include "logger.h"
#include "network_utils.h"
#include "config.h"
#include "mm_utils.h"
#include "OverlayDataTypes.h"

class elasticSearch
{
private:
    static Json::Value queryESMetadata(string url, string query)
    {
        std::string jsonData;
        CurlRequestFields curlFields = {};
        curlFields.m_url = url;
        curlFields.m_method = "GET";
        curlFields.m_jsonData = query;
        curlFields.m_timeout = 20;

        int ret = curlSendRequest(curlFields, jsonData);
        LOG(verbose) << "ret: " << ret << endl;

        return stringToJson(jsonData);
    }

    static string getExactQueryString(const SearchParams& inData, Json::Value source, bool use_id = false);

public:
    static string getQueryString(SearchParams& inData)
    {
        nv_vms::DeviceConfig config =  GET_CONFIG();
        bool use_protobuf = config.use_video_metadata_protobuf;

        Json::Value query;
        Json::Value term, range, timestamp_range;
        if (GET_CONFIG().overlay_3d_sensor_name.empty())
        {
            term["sensorId.keyword"] = inData.m_sensor_id;
        }
        else
        {
            term["sensorId.keyword"] = GET_CONFIG().overlay_3d_sensor_name;
        }
        timestamp_range["gte"] = inData.m_start_time;
        if (inData.m_end_time != "")
        {
            timestamp_range["lte"] = inData.m_end_time;
        }
        string search_key = use_protobuf ? "timestamp" : "@timestamp";
        range[search_key] = timestamp_range;

        Json::Value must_term, must_range, must;
        must_term["term"] = term;
        must_range["range"] = range;
        must.append(must_term);
        must.append(must_range);

        Json::Value bool_content, query_content;
        bool_content["must"] = must;
        query_content["bool"] = bool_content;

        Json::Value sort, sort_content, timestamp_sort;
        timestamp_sort["order"] = "asc";
        sort_content[search_key] = timestamp_sort;
        sort.append(sort_content);

        Json::Value search_after;
        search_after.append(inData.m_search_after);

        query["query"] = query_content;
        query["sort"] = sort;
        query["search_after"] = search_after;
        LOG(verbose) << "query:\n" << query.toStyledString() << endl;
        return jsonToString(query);
    }

    static string getQueryStringForId(SearchParams& inData)
    {
        nv_vms::DeviceConfig config =  GET_CONFIG();
        bool use_protobuf = config.use_video_metadata_protobuf;

        Json::Value query;
        Json::Value match;
        if (GET_CONFIG().overlay_3d_sensor_name.empty())
        {
            match["sensorId.keyword"] = inData.m_sensor_id;
        }
        else
        {
            match["sensorId.keyword"] = GET_CONFIG().overlay_3d_sensor_name;
        }

        Json::Value must_match, must;
        must_match["match"] = match;
        must.append(must_match);

        Json::Value bool_content, query_content;
        bool_content["must"] = must;
        query_content["bool"] = bool_content;

        Json::Value sort, sort_content, timestamp_sort;
        timestamp_sort["order"] = "asc";
        string timestamp_key = use_protobuf ? "timestamp" : "@timestamp";
        sort_content[timestamp_key] = timestamp_sort;
        sort.append(sort_content);

        Json::Value search_after;
        search_after.append(inData.m_search_after);

        query["query"] = query_content;
        query["sort"] = sort;
        query["search_after"] = search_after;
        return jsonToString(query);
    }

    static Json::Value getMetadata(const SearchParams& inData, bool use_id = false)
    {
        Json::Value metadata;
        nv_vms::DeviceConfig config =  GET_CONFIG();
        string elasticsearch_url = config.video_metadata_server;
        bool use_protobuf = config.use_video_metadata_protobuf;
        std::string url = elasticsearch_url + "/_search?size=1";

        string timestamp_format = use_protobuf ? "timestamp" : "@timestamp";
        string search_key = use_id ? "id" : timestamp_format;
        Json::Value source_field;
        source_field.append("sensorId");
        source_field.append("objects");
        source_field.append(search_key);
        std::string string_query = getExactQueryString(inData, source_field, use_id);

        LOG(info) << "Querying elastic server for camera: " << inData.m_sensor_id
                    << " and timestamp: " << inData.m_start_time << endl;
        Json::Value json_get = queryESMetadata(url, string_query);
        Json::Value& hits = json_get["hits"]["hits"];
        LOG(info) << "Elastic Search returned number of hits: " << hits.size() << endl;
        if (hits.size() != 0)
        {
            Json::Value& source = hits[0]["_source"];
            metadata[search_key] = source[search_key];
            metadata["objects"] = source["objects"];
            metadata["sensorId"] = source["sensorId"];
        }

        return metadata;
    }

    static void getBboxPositionStreamer(BBoxMetaData& outData);
    static void getBboxPosition(BBoxMetaData& outData);
};
