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

#include "elasticSearch.h"

string elasticSearch::getExactQueryString(const SearchParams& inData, Json::Value source, bool use_id /*false*/)
{
    nv_vms::DeviceConfig config =  GET_CONFIG();
    bool use_protobuf = config.use_video_metadata_protobuf;
    Json::Value query;
    Json::Value match_sensor, match_ts;
    string timestamp_format = use_protobuf ? "timestamp" : "@timestamp";
    string search_key = use_id ? "id" : timestamp_format;

    match_sensor["sensorId"] = inData.m_sensor_id;
    match_ts[search_key] = inData.m_start_time;

    Json::Value must_sensor, must_ts, must;
    must_sensor["match"] = match_sensor;
    must_ts["match"] = match_ts;
    must.append(must_sensor);
    must.append(must_ts);

    Json::Value bool_content, query_content;
    bool_content["must"] = must;
    query_content["bool"] = bool_content;

    Json::Value sort, sort_content, timestamp_sort;
    // We need the latest result, hence using "desc"
    timestamp_sort["order"] = "desc";
    sort_content[timestamp_format] = timestamp_sort;
    sort.append(sort_content);

    query["query"] = query_content;
    query["sort"] = sort;
    query["_source"] = source;

    LOG(verbose) << "query\n" << query.toStyledString() << endl;
    return jsonToString(query);
}

void elasticSearch::getBboxPositionStreamer(BBoxMetaData& outData)
{
    string start_time = "", end_time = "";
    outData.m_searching = true;
    SearchParams& inData = outData.m_searchParams;
    nv_vms::DeviceConfig config =  GET_CONFIG();
    string elasticsearch_url = config.video_metadata_server;
    if (elasticsearch_url.empty())
    {
        LOG(warning) << "Elasticsearch URL is empty" << endl;
        outData.m_searching = false;
        return;
    }
    bool use_protobuf = config.use_video_metadata_protobuf;

    string timestamp_format = use_protobuf ? "timestamp" : "@timestamp";
    Json::Value source_field;
    source_field.append(timestamp_format);
    if (inData.m_start_time.empty())
    {
        inData.m_start_time = "1";
    }
    std::string string_query = getExactQueryString(inData, source_field, true);

    LOG(info) << "Querying elastic server for start time for camera: " << inData.m_sensor_id
                << " with Start frameid: " << inData.m_start_time << endl;
    std::string url_start = elasticsearch_url + "/_search?size=2";
    Json::Value json_get = queryESMetadata(url_start, string_query);

    Json::Value& hits = json_get["hits"]["hits"];
    if (hits.size() != 0)
    {
        Json::Value& source = hits[0]["_source"];
        inData.m_start_time = source[timestamp_format].asString();
        inData.m_useId = false;
    }
    else
    {
        LOG(error) << "No result for start frameid" << endl;
        inData.m_start_time = "";
        inData.m_end_time = "";
    }

    if (inData.m_start_time != "" && inData.m_end_time != "")
    {
        SearchParams inData2 = inData;
        inData2.m_start_time = inData2.m_end_time;
        string_query = getExactQueryString(inData2, source_field, true);

        LOG(info) << "Querying elastic server for end time for camera: " << inData2.m_sensor_id
                    << " with frameid: " << inData2.m_start_time << endl;
        std::string url_end = elasticsearch_url + "/_search?size=1";
        Json::Value json_get2 = queryESMetadata(url_end, string_query);

        Json::Value& hits2 = json_get2["hits"]["hits"];
        if (hits2.size() != 0)
        {
            Json::Value& source = hits2[0]["_source"];
            inData.m_end_time = source[timestamp_format].asString();
        }
        else
        {
            LOG(error) << "No result for end frameid" << endl;
            inData.m_end_time = "";
        }

        LOG(info) << " Found start: " << inData.m_start_time << " and end: " << inData.m_end_time << endl;
        int64_t epochs_start = getEpocTimeInMS(inData.m_start_time);
        int64_t epochs_end = getEpocTimeInMS(inData.m_end_time);
        if (epochs_start > epochs_end && inData.m_end_time != "")
        {
            // Start is greater than end. Use previous start
            if (hits.size() > 1)
            {
                Json::Value& source = hits[1]["_source"];
                start_time = source[timestamp_format].asString();
                LOG(info) << "new start: " << start_time << endl;
            }
            else
            {
                LOG(error) << "No other start time available, using id for search" << endl;
                inData.m_start_time = "";
                inData.m_end_time = "";
                inData.m_useId = true;
            }
        }
    }
    getBboxPosition(outData);

    outData.m_searching = false;
}

void elasticSearch::getBboxPosition(BBoxMetaData& outData)
{
    outData.m_searching = true;
    SearchParams& inData = outData.m_searchParams;
    nv_vms::DeviceConfig config =  GET_CONFIG();
    string elasticsearch_url = config.video_metadata_server;
    if (elasticsearch_url.empty())
    {
        LOG(warning) << "Elasticsearch URL is empty" << endl;
        outData.m_searching = false;
        return;
    }
    int size_to_fetch = config.video_metadata_query_batch_size_num_frames;
    std::string url = elasticsearch_url + "/_search?size="
                        + std::to_string(size_to_fetch);
    std::string string_query = "";
    if (inData.m_useId == true)
    {
        string_query = getQueryStringForId(inData);
    }
    else
    {
        string_query = getQueryString(inData);
    }

    LOG(info) << "Querying elastic server for camera: " << inData.m_sensor_id
                << " with Start: " << inData.m_start_time
                << "  End: " << inData.m_end_time
                << "  And search after: " << inData.m_search_after << endl;
    Json::Value json_get = queryESMetadata(url, string_query);
    Json::Value& hits = json_get["hits"]["hits"];
    const bool is3dSensor = !GET_CONFIG().overlay_3d_sensor_name.empty();
    for (uint32_t i = 0; i < hits.size(); i++)
    {
        Json::Value& source = hits[i]["_source"];
        Json::Value current_hit;
        current_hit["objects"] = source["objects"];
        current_hit["id"] = source["id"];

        if (is3dSensor && source.isMember("info") && source["info"].isObject() &&
            source["info"].isMember(inData.m_sensor_id) &&
            source["info"][inData.m_sensor_id].isString())
        {
            std::time_t epochMs = isoToEpoch(source["info"][inData.m_sensor_id].asString());
            if (epochMs != 0)
            {
                current_hit["epocTime"] = static_cast<Json::UInt64>(epochMs);
            }
            else
            {
                current_hit["epocTime"] = hits[i]["sort"][0].asUInt64();
            }
        }
        else
        {
            current_hit["epocTime"] = hits[i]["sort"][0].asUInt64();
        }

        std::lock_guard<std::mutex> guard(outData.m_hitsLock);
        outData.m_qHits.push(current_hit);
    }

    LOG(info) << "Data received from ElasticSearch server, size: "
                << hits.size() << endl;
    if (hits.size() > 0)
    {
        outData.m_searchParams.m_search_after = hits[hits.size()-1]["sort"][0].asUInt64();
    }
    outData.m_dataSize = hits.size();
    outData.m_searching = false;
}
