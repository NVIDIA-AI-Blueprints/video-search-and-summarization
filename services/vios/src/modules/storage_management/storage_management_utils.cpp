/*
 * SPDX-FileCopyrightText: Copyright (c) 2023-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "storage_management_utils.h"
#include "logger.h"
#include "storage_management.h"
#include "vst_common.h"
#include "nvhwdetection.h"
#include "storage_management.h"
#include "database.h"
#include "modules_apis.h"
#include "fs_utils.h"
#include <filesystem>
#include "utils.h"

// VideoSegmentExtractor now loaded dynamically - no direct include
#include <cmath>
#include <dlfcn.h>
#include <unistd.h>
#include <limits.h>
#include <array>
#include <chrono>
#include <stdlib.h>
#include <cstring>
#include <fcntl.h>
#include <cerrno>
#include <mutex>

constexpr int64_t DEFAULT_START_TIME_EPOCH = 1735689600000;

// Temp video storage path constants are now defined in storage_management.h

// Forward declarations for dynamic VideoSegmentExtractor interface
namespace nv_vms {
    class VideoSegmentExtractor;
}

static constexpr size_t NUM_SENSOR_LOCKS = 256;
static_assert(NUM_SENSOR_LOCKS > 0, "NUM_SENSOR_LOCKS must be greater than zero to prevent division by zero");
static std::array<std::mutex, NUM_SENSOR_LOCKS> g_sensorLocks;

static std::mutex& getSensorMutex(const std::string& sensorId)
{
    std::hash<std::string> hasher;
    size_t index = hasher(sensorId) % NUM_SENSOR_LOCKS;
    return g_sensorLocks[index];
}

// Forward declaration of helper function for stream merging
static VmsErrorCode mergeExistingStreams(shared_ptr<SensorInfo> sensor, shared_ptr<DeviceManager> deviceMngr, Json::Value& response);

// Forward declarations for stream creation helper functions
static void createMainStreamForFirstUpload(shared_ptr<StreamInfo> stream, shared_ptr<SensorInfo> sensor, Json::Value& response);
static void createSubsequentStream(shared_ptr<StreamInfo> stream, shared_ptr<SensorInfo> sensor, const vector<SensorStreamsDBColumns>& existingStreams, Json::Value& response);
static void updateStreamUrls(shared_ptr<StreamInfo> stream, shared_ptr<SensorInfo> sensor, const string& rtsp_url, shared_ptr<DeviceManager> deviceMngr, const vector<SensorStreamsDBColumns>& existingStreams);

// Function pointer types for dynamic loading
typedef nv_vms::VideoSegmentExtractor* (*CreateVideoSegmentExtractor_t)();
typedef void (*DestroyVideoSegmentExtractor_t)(nv_vms::VideoSegmentExtractor*);
typedef bool (*ExtractSegmentStreamCopy_t)(nv_vms::VideoSegmentExtractor*, const std::string&, const std::string&, int64_t, int64_t);
typedef std::string (*GetLastError_t)(nv_vms::VideoSegmentExtractor*);
typedef bool (*IsExtractorAvailable_t)(nv_vms::VideoSegmentExtractor*);

// Dynamic VideoSegmentExtractor loader class
class DynamicVideoSegmentExtractor {
private:
    void* m_handle;
    CreateVideoSegmentExtractor_t m_createFunc;
    DestroyVideoSegmentExtractor_t m_destroyFunc;
    ExtractSegmentStreamCopy_t m_extractFunc;
    GetLastError_t m_getErrorFunc;
    IsExtractorAvailable_t m_isAvailableFunc;
    nv_vms::VideoSegmentExtractor* m_instance;

    // Secure library loading function with path validation
    void* tryLoadLibrary(const char* lib_path)
    {
        if (!lib_path || lib_path[0] == '\0')
        {
            LOG(warning) << "Invalid library path provided" << endl;
            return nullptr;
        }

        // Check if file exists and is readable
        if (access(lib_path, R_OK) != 0)
        {
            LOG(error) << "Library not accessible: " << lib_path << endl;
            return nullptr;
        }

        // Resolve any symbolic links to get the real path
        char resolved_path[PATH_MAX];
        if (realpath(lib_path, resolved_path) == nullptr)
        {
            LOG(warning) << "Cannot resolve library path: " << lib_path << endl;
            return nullptr;
        }

        // Validate that the resolved path is in a trusted directory
        // Use safe string-based prefix checking to avoid dangerous strlen()
        std::string resolved_path_str(resolved_path);
        constexpr std::array<const char*, 4> trusted_prefixes = {
            "/home/vst/vst_release/prebuilts/",
            "/root/vst_release/prebuilts/",
            "/opt/vst_release/prebuilts/",
            "./prebuilts/"
        };

        bool is_trusted = false;
        for (const auto& prefix : trusted_prefixes)
        {
            if (resolved_path_str.find(prefix) == 0)
            {
                is_trusted = true;
                break;
            }
        }

        if (!is_trusted)
        {
            LOG(error) << "Library path not in trusted directory: " << resolved_path << endl;
            return nullptr;
        }

        // Load the library with RTLD_NOW for immediate symbol resolution
        void* handle = dlopen(resolved_path, RTLD_NOW | RTLD_LOCAL);
        if (!handle)
        {
            LOG(error) << "Failed to load library " << resolved_path << ": " << dlerror() << endl;
        }

        return handle;
    }

public:
    DynamicVideoSegmentExtractor() : m_handle(nullptr), m_createFunc(nullptr),
                                   m_destroyFunc(nullptr), m_extractFunc(nullptr),
                                   m_getErrorFunc(nullptr), m_isAvailableFunc(nullptr),
                                   m_instance(nullptr) {}

    ~DynamicVideoSegmentExtractor() {
        cleanup();
    }

    bool initialize() {
        // Try to load the VideoSegmentExtractor library following vstmodule.cpp pattern
        const char* lib_path;

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libvideosegmentextractor.so");
        m_handle = tryLoadLibrary(lib_path);
        if (!m_handle) {
            lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libvideosegmentextractor.so");
            m_handle = tryLoadLibrary(lib_path);
        }
#else
        lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libvideosegmentextractor.so");
        m_handle = tryLoadLibrary(lib_path);
#endif

        if (m_handle) {
            LOG(info) << "Loaded VideoSegmentExtractor from: " << lib_path << endl;
        } else {
            LOG(warning) << "VideoSegmentExtractor library not found in trusted paths" << endl;
        }

        if (!m_handle) {
            LOG(info) << "VideoSegmentExtractor library not found - video segment optimization disabled" << endl;
            LOG(info) << "Run user_additional_install.sh to enable optimized video processing" << endl;
            return false;
        }

        // Load function symbols
        m_createFunc = (CreateVideoSegmentExtractor_t)dlsym(m_handle, "createVideoSegmentExtractor");
        m_destroyFunc = (DestroyVideoSegmentExtractor_t)dlsym(m_handle, "destroyVideoSegmentExtractor");
        m_extractFunc = (ExtractSegmentStreamCopy_t)dlsym(m_handle, "extractSegmentStreamCopy");
        m_getErrorFunc = (GetLastError_t)dlsym(m_handle, "getLastError");
        m_isAvailableFunc = (IsExtractorAvailable_t)dlsym(m_handle, "isExtractorAvailable");

        if (!m_createFunc || !m_destroyFunc || !m_extractFunc || !m_getErrorFunc || !m_isAvailableFunc) {
            LOG(error) << "Failed to load VideoSegmentExtractor symbols" << endl;
            cleanup();
            return false;
        }

        // Create instance
        m_instance = m_createFunc();
        if (!m_instance) {
            LOG(error) << "Failed to create VideoSegmentExtractor instance" << endl;
            cleanup();
            return false;
        }

        LOG(info) << "VideoSegmentExtractor initialized successfully" << endl;
        return true;
    }

    bool extractSegmentStreamCopy(const std::string& inputFile, const std::string& outputFile, int64_t startPts, int64_t endPts) {
        if (!m_instance || !m_extractFunc || !m_isAvailableFunc) {
            return false;
        }

        // Check if the extractor instance is available before attempting extraction
        if (!m_isAvailableFunc(m_instance)) {
            LOG(info) << "VideoSegmentExtractor instance not available (LibavWrapper failed), recreating" << endl;

            // Destroy current instance and create new one
            if (m_destroyFunc) {
                m_destroyFunc(m_instance);
                m_instance = nullptr;
            }

            if (m_createFunc) {
                m_instance = m_createFunc();
                if (!m_instance) {
                    LOG(error) << "Failed to recreate VideoSegmentExtractor instance" << endl;
                    return false;
                }

                // Check if the new instance is available
                if (!m_isAvailableFunc(m_instance)) {
                    LOG(error) << "New VideoSegmentExtractor instance is still not available" << endl;
                    return false;
                }
            }
        }

        bool result = m_extractFunc(m_instance, inputFile, outputFile, startPts, endPts);

        // If extraction failed, recreate the instance to ensure clean state for next call
        if (!result) {
            LOG(info) << "VideoSegmentExtractor failed, recreating instance for next call" << endl;

            // Destroy current instance
            if (m_instance && m_destroyFunc) {
                m_destroyFunc(m_instance);
                m_instance = nullptr;
            }

            // Create new instance
            if (m_createFunc) {
                m_instance = m_createFunc();
                if (!m_instance) {
                    LOG(error) << "Failed to recreate VideoSegmentExtractor instance" << endl;
                }
            }
        }

        return result;
    }

    std::string getLastError() {
        if (!m_instance || !m_getErrorFunc) {
            return "VideoSegmentExtractor not available";
        }
        return m_getErrorFunc(m_instance);
    }

    bool isAvailable() const {
        return m_instance != nullptr;
    }

private:
    void cleanup() {
        if (m_instance && m_destroyFunc) {
            m_destroyFunc(m_instance);
            m_instance = nullptr;
        }
        if (m_handle) {
            dlclose(m_handle);
            m_handle = nullptr;
        }
        m_createFunc = nullptr;
        m_destroyFunc = nullptr;
        m_extractFunc = nullptr;
        m_getErrorFunc = nullptr;
        m_isAvailableFunc = nullptr;
    }
};

extern "C" {
#include <libavutil/avutil.h>
}

using namespace std;

// Single helper function to extract and process all common video file logic
nv_vms::VmsErrorCode prepareVideoFileProcessing(
    const string& user_start_time,
    const string& user_end_time,
    const string& sensor_id,
    const string& id,
    const string& device_name,
    const string& full_length,
    string& output_file,
    VideoFileProcessingParams& params);

// Modified function signatures to use preprocessed parameters
nv_vms::VmsErrorCode extractVideoSegmentWithLibAV_Internal(
    const VideoFileProcessingParams& params,
    string& output_file,
    string& video_codec,
    const string& user_start_time = "",
    const string& user_end_time = "");


constexpr const char* CAM_DEFAULT_PREFIX = "CAMERA";
constexpr const char* DEFAULT_CHUNK_NAME = "filepart";
constexpr const char* METADATA_FIELD_KEY = "metadata";
constexpr const char* MEDIA_FILE_PATH_KEY = "mediaFilePath";
constexpr const char* METADATA_FILE_PATH_KEY = "metaDataFilePath";

/* Macro defined in splitmuxsrc plugin */
const auto FIXED_TS_OFFSET = (1000*GST_SECOND);

typedef struct {
    OverlayBBoxParams m_overlayBBoxParams;
    NvLLOverlayInternal::OverlayParams m_overlayParams;
    std::unique_ptr<NvLLOverlayInternal> m_overlayInst;
} OverlayFields;

/* ---------------------------------------------------------------------------
**  Civet mg_form_data_handler callback
** -------------------------------------------------------------------------*/
int field_found(const char *key, const char *filename, char *path, size_t pathlen, void *user_data)
{
    struct FileData* data = (FileData *) user_data;
    if(!data)
    {
        LOG(error) << "FileData is NULL" << endl;
        return MG_FORM_FIELD_STORAGE_ABORT;
    }

    LOG(verbose) << "Form field found - key: " << (key ? key : "NULL") << endl;
    LOG(verbose) << "Form field found - filename: " << (filename ? filename : "NULL") << endl;

    if (filename && *filename)
    {
        // This is a file field
        DeviceConfig config =  GET_CONFIG();
        std::string fileLocation = config.nv_streamer_directory_path;
        data->m_isFileReceived = true;

        // Store filename if not already set from header
        if (data->m_fileName.empty())
        {
            data->m_fileName = string(filename);
            LOG(info) << "Extracted fileName from form data: " << data->m_fileName << endl;

            // Validate filename extracted from form data
            if(checkWhiteSpace(data->m_fileName.c_str()))
            {
                data->m_hasError = true;
                data->m_errorCode = VmsErrorCode::InvalidParameterError;
                data->m_errorMessage = "Whitespaces not allowed in file name" ": " + data->m_fileName;
                LOG(error) << data->m_errorMessage << endl;
                return MG_FORM_FIELD_STORAGE_ABORT;
            }
            if (checkFileNameLength(data->m_fileName.c_str()))
            {
                data->m_hasError = true;
                data->m_errorCode = VmsErrorCode::InvalidParameterError;
                data->m_errorMessage = "File name is too long" ": " + data->m_fileName;
                LOG(error) << data->m_errorMessage << endl;
                return MG_FORM_FIELD_STORAGE_ABORT;
            }
        }

        if (data->m_isChunkedUpload)
        {
            // Chunked upload: store in temporary directory
            std::string tempDirectory = appendDirectory(fileLocation, data->m_chunkIdentifier);
            if(data->m_tempDirectory == EMPTY_STRING)
            {
                data->m_tempDirectory = tempDirectory;
            }
            // Use default chunk name for chunks
            std::string fname = DEFAULT_CHUNK_NAME;
            std::string uniqueFilePath = getUniqueFilePath(fname, tempDirectory);
            snprintf(path, uniqueFilePath.length() + 1, "%s", uniqueFilePath.c_str());
        }
        else
        {
            // Single file upload: store directly in final location
            std::string uniqueFilePath = getUniqueFilePath(data->m_fileName, fileLocation);
            data->m_absoluteFilePath = uniqueFilePath;
            snprintf(path, uniqueFilePath.length() + 1, "%s", uniqueFilePath.c_str());
        }

		return MG_FORM_FIELD_STORAGE_STORE;
    }
    else if (key && std::string_view(key) == METADATA_FIELD_KEY)
    {
        // This is our JSON metadata field - tell CivetWeb to call field_get for this
        LOG(info) << "Found metadata field, will capture in field_get" << endl;
        return MG_FORM_FIELD_STORAGE_GET;
    }

	return MG_FORM_FIELD_STORAGE_GET;
}

int field_get(const char *key, const char *value, size_t valuelen, void *user_data)
{
	if ((key != nullptr) && (key[0] == '\0'))
    {
		/* Incorrect form data detected */
        struct FileData* data = (FileData *) user_data;
        if (data)
        {
            data->m_hasError = true;
            data->m_errorCode = VmsErrorCode::InvalidParameterError;
            data->m_errorMessage = "Incorrect form data detected - empty key";
        }
		return MG_FORM_FIELD_HANDLE_ABORT;
	}
	if ((valuelen > 0) && (value == nullptr))
    {
		/* Unreachable, since this call will not be generated by civetweb. */
        struct FileData* data = (FileData *) user_data;
        if (data)
        {
            data->m_hasError = true;
            data->m_errorCode = VmsErrorCode::InvalidParameterError;
            data->m_errorMessage = "Invalid form data - null value with non-zero length";
        }
		return MG_FORM_FIELD_HANDLE_ABORT;
	}

	struct FileData* data = (FileData *) user_data;
	if(!data)
    {
        LOG(error) << "FileData is NULL in field_get" << endl;
        return MG_FORM_FIELD_HANDLE_ABORT;
    }

	if (key)
    {
        LOG(verbose) << "field_get - key: " << key << ", value: " << (value ? string(value, valuelen) : "NULL") << endl;

        // Check if this is our metadata field
        if (strcmp(key, METADATA_FIELD_KEY) == 0 && value && valuelen > 0)
        {
            // Capture JSON metadata
            data->m_jsonMetadata = string(value, valuelen);
            data->m_hasMetadata = true;

            LOG(info) << "Captured JSON metadata: " << data->m_jsonMetadata << endl;

            // Try to parse JSON to validate it
            Json::Reader reader;
            if (reader.parse(data->m_jsonMetadata, data->m_parsedMetadata))
            {
                LOG(verbose) << "Successfully Parsed JSON metadata: " << data->m_parsedMetadata.toStyledString() << endl;
            }
            else
            {
                data->m_hasError = true;
                data->m_errorCode = VmsErrorCode::InvalidParameterError;
                data->m_errorMessage = "Failed to parse JSON metadata: " + reader.getFormattedErrorMessages();
                LOG(error) << data->m_errorMessage << endl;
            }
        }
        if (strcmp(key, MEDIA_FILE_PATH_KEY) == 0 && value && valuelen > 0)
        {
            data->m_mediaFilePath = string(value, valuelen);
        }
        if (strcmp(key, METADATA_FILE_PATH_KEY) == 0 && value && valuelen > 0)
        {
            data->m_metaDataFilePath = string(value, valuelen);
        }
	}
	return 0;
}

int field_stored(const char *path, long long file_size, void *user_data)
{
    struct FileData* data = (FileData *) user_data;
    if(!data)
    {
        LOG(error) << "FileData is NULL" << endl;
        return MG_FORM_FIELD_STORAGE_ABORT;
    }

    // Validate the stored file
    if (!path || file_size <= 0)
    {
        data->m_hasError = true;
        data->m_errorCode = VmsErrorCode::VMSInternalError;
        data->m_errorMessage = "File storage failed - invalid path or file size";
        LOG(error) << data->m_errorMessage << endl;
        return MG_FORM_FIELD_STORAGE_ABORT;
    }

    // Check if file actually exists and has expected size
    if (!isFileExist(path))
    {
        data->m_hasError = true;
        data->m_errorCode = VmsErrorCode::VMSInternalError;
        data->m_errorMessage = "File storage failed - file not found after storage: " + std::string(path);
        LOG(error) << data->m_errorMessage << endl;
        return MG_FORM_FIELD_STORAGE_ABORT;
    }

    data->m_isFileSaved = true;
    data->m_fileSize = static_cast<size_t>(file_size);
    LOG(info) << "File Uploaded: " << path << " (size: " << file_size << " bytes)" << endl;
    return 0;
}

void addOrRemoveInProtectList(std::vector<VideoFileInfo>& files, bool removeOrAdd)
{
    for (uint index = 0; index < files.size(); index++)
    {
        string file_name = files[index].m_filePath.c_str();
        vst_storage::addOrRemoveFileInProtectList(file_name, removeOrAdd);
    }
}

VmsErrorCode checkMaxSensorsLimit(std::shared_ptr<DeviceManager> deviceMngr, Json::Value& response)
{
    size_t max_sensors_supported = GET_CONFIG().max_sensors_supported;
    if (deviceMngr == nullptr)
    {
        string error_message = "Device Manager not found";
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, error_message.c_str());
        return VmsErrorCode::VMSInternalError;
    }

    if (deviceMngr->getSensorList(true).size() >= max_sensors_supported)
    {
        string error_message = "Maximum number of sensors limit reached";
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, error_message.c_str());
        return VmsErrorCode::VMSInternalError;
    }

    return VmsErrorCode::NoError;
}

/**
 * Helper function to merge existing streams from database with current sensor streams
 * @param sensor The sensor to merge streams for
 * @param deviceMngr The device manager instance
 * @param response JSON response object to set streamId for the new stream
 * @return VmsErrorCode indicating success or failure
 */
static VmsErrorCode mergeExistingStreams(shared_ptr<SensorInfo> sensor, shared_ptr<DeviceManager> deviceMngr, Json::Value& response)
{
    LOG(warning) << "Updating sensor in DB - merging with existing streams" << endl;

    auto dbHelper = GET_DB_INSTANCE();

    // Get existing streams for this sensor from database
    vector<SensorStreamsDBColumns> existingStreams = dbHelper->readAllStreamsForGivenSensorID(sensor->id);
    LOG(warning) << "Found " << existingStreams.size() << " existing streams for sensor: " << sensor->id << endl;

    // Convert existing database streams to StreamInfo objects and add to sensor
    for (const auto& streamRow : existingStreams)
    {
        // Check if this stream already exists in the sensor (avoid duplicates)
        bool streamExists = false;
        for (const auto& existingStream : sensor->streams)
        {
            if (existingStream->id == streamRow.stream_id_value)
            {
                streamExists = true;
                break;
            }
        }

        if (!streamExists)
        {
            shared_ptr<StreamInfo> existingStream(new StreamInfo);
            existingStream->id = streamRow.stream_id_value;
            existingStream->sensorId = streamRow.sensor_id_value;
            existingStream->live_url = streamRow.live_url_value;
            existingStream->live_proxy_url = streamRow.proxy_url_value;
            existingStream->replay_url = streamRow.replay_url_value;
            // Set as non-main stream by default, will be corrected later
            existingStream->isMainStream = false;
            // Use streamName_value from DB if available, otherwise fall back to stream_id
            existingStream->name = streamRow.streamName_value.empty() ? streamRow.stream_id_value : streamRow.streamName_value;
            existingStream->duration = stringToInt(streamRow.duration_value, 0);
            existingStream->stream_type = static_cast<StreamType>(streamRow.streamType_value);

            // Set video encoder values
            SensorVideoEncoderSettingsValues &enc_values = existingStream->getvideoEncoderValues();
            enc_values.resolution = streamRow.resolution_value;
            enc_values.encoding = streamRow.encoding_value;
            enc_values.encodingInterval = streamRow.encodingInterval_value;
            enc_values.frameRate = streamRow.frameRate_value;
            enc_values.bitrate = streamRow.bitrate_value;
            enc_values.numFrames = streamRow.numFrames_value;

            sensor->streams.push_back(existingStream);
            LOG(warning) << "Added existing stream to sensor: " << existingStream->id << endl;
        }
    }

    // Ensure proper main stream assignment after merging
    LOG(warning) << "Setting main stream assignment for merged streams" << endl;

    // Find the stream with sensor ID (main stream) and ensure it's first
    vector<shared_ptr<StreamInfo>> reorderedStreams;
    shared_ptr<StreamInfo> mainStream = nullptr;

    // Look for the stream with ID equal to sensor ID
    for (const auto& stream : sensor->streams)
    {
        if (stream->id == sensor->id)
        {
            mainStream = stream;
            mainStream->isMainStream = true;
            break;
        }
    }

    // Add main stream first (if found)
    if (mainStream)
    {
        reorderedStreams.push_back(mainStream);
        LOG(warning) << "Found main stream: " << mainStream->id << " isMainStream: true" << endl;
    }

    // Add all other streams as non-main
    for (const auto& stream : sensor->streams)
    {
        if (stream->id != sensor->id)
        {
            stream->isMainStream = false;
            reorderedStreams.push_back(stream);
            LOG(warning) << "Added sub stream: " << stream->id << " isMainStream: false" << endl;
        }
    }

    // Update with reordered streams
    sensor->streams = reorderedStreams;

    LOG(warning) << "Total streams in sensor after merge: " << sensor->streams.size() << endl;

    // Get the returned sensor from addOrUpdateSensor and update its streams
    shared_ptr<SensorInfo> updatedSensor = deviceMngr->addOrUpdateSensor(*sensor);
    if (updatedSensor && updatedSensor->getStreams().size() != sensor->streams.size())
    {
        LOG(warning) << "addOrUpdateSensor returned sensor with " << updatedSensor->getStreams().size() << " streams, updating with merged streams" << endl;
        vector<shared_ptr<StreamInfo>> mergedStreams = sensor->getStreams();
        updatedSensor->updateStreams(mergedStreams);
        LOG(warning) << "Updated sensor now has " << updatedSensor->getStreams().size() << " streams" << endl;

        // Manually trigger the callback to update the database with all streams
        LOG(warning) << "Manually triggering updateSensorDetailsToDB callback with merged streams" << endl;
        vst_common::updateSensorDetailsToDB(deviceMngr->getDeviceId(), updatedSensor, false);
    }

    return VmsErrorCode::NoError;
}

/**
 * Helper function to generate a unique stream name within a sensor's streams
 * If the proposed name already exists, appends _1, _2, etc. until unique
 * @param proposedName The desired stream name
 * @param existingStreams Vector of existing streams for the sensor
 * @return A unique stream name (case-sensitive comparison)
 */
static string generateUniqueStreamName(const string& proposedName, const vector<SensorStreamsDBColumns>& existingStreams)
{
    // Check if the proposed name is already unique (case-sensitive comparison)
    bool nameExists = false;
    for (const auto& stream : existingStreams)
    {
        if (stream.streamName_value == proposedName)
        {
            nameExists = true;
            break;
        }
    }

    if (!nameExists)
    {
        LOG(info) << "Stream name '" << proposedName << "' is unique, using as-is" << endl;
        return proposedName;
    }

    // Name exists, find a unique suffix (_1, _2, etc.)
    int suffix = 1;
    string uniqueName;
    while (true)
    {
        uniqueName = proposedName + "_" + to_string(suffix);
        bool suffixedNameExists = false;
        for (const auto& stream : existingStreams)
        {
            if (stream.streamName_value == uniqueName)
            {
                suffixedNameExists = true;
                break;
            }
        }

        if (!suffixedNameExists)
        {
            LOG(info) << "Stream name '" << proposedName << "' already exists, using unique name: " << uniqueName << endl;
            break;
        }
        suffix++;
    }

    return uniqueName;
}

/**
 * Helper function to configure a stream as the main stream for first uploads
 * @param stream The stream to configure
 * @param sensor The sensor information
 * @param response JSON response object to set streamId
 */
static void createMainStreamForFirstUpload(shared_ptr<StreamInfo> stream, shared_ptr<SensorInfo> sensor, Json::Value& response)
{
    // First upload: use sensor ID as stream ID and set as main stream
    stream->id = sensor->id;
    response["streamId"] = stream->id;
    stream->name = sensor->name;  // Main stream gets the sensor name
    stream->isMainStream = true;
    LOG(warning) << "First upload for sensor " << sensor->id << " - using sensor ID as stream ID with name: " << stream->name << endl;
}

/**
 * Helper function to configure a stream for subsequent uploads
 * @param stream The stream to configure
 * @param sensor The sensor information
 * @param existingStreams Vector of existing streams from database
 * @param response JSON response object to set streamId
 */
static void createSubsequentStream(shared_ptr<StreamInfo> stream, shared_ptr<SensorInfo> sensor, const vector<SensorStreamsDBColumns>& existingStreams, Json::Value& response)
{
    // Subsequent upload: use appended format and set as non-main stream
    string appendedPart = sensor->name;  // This contains the unique filename part
    stream->id = sensor->id + "_" + appendedPart;
    response["streamId"] = stream->id;
    stream->name = sensor->name;  // Will be updated after URLs are set
    stream->isMainStream = false;
    LOG(warning) << "Subsequent upload for sensor " << sensor->id << " - using appended format: " << stream->id << endl;
}

/**
 * Helper function to update stream URLs based on sensor type and conditions
 * @param stream The stream to update
 * @param sensor The sensor information
 * @param rtsp_url The RTSP URL
 * @param deviceMngr The device manager instance
 * @param existingStreams Vector of existing streams from database
 */
static void updateStreamUrls(shared_ptr<StreamInfo> stream, shared_ptr<SensorInfo> sensor, const string& rtsp_url, shared_ptr<DeviceManager> deviceMngr, const vector<SensorStreamsDBColumns>& existingStreams)
{
    stream->live_url = rtsp_url;

    // Set URLs based on device type and sensor type
    if (deviceMngr->getDeviceType() == TYPE_STREAMER || sensor->type == std::string(SENSOR_TYPE_FILE))
    {
        stream->live_url = stream->live_proxy_url = stream->replay_url = stream->live_url;
    }

    // For file-based sensors, ensure live_proxy_url is set for event notifications
    if (stream->live_proxy_url.empty() && !sensor->location.empty())
    {
        // Use file path as live_proxy_url for file-based sensors to enable event notifications
        stream->live_proxy_url = sensor->location;
        stream->replay_url = sensor->location;
        LOG(info) << "Set live_proxy_url for file-based sensor: " << stream->live_proxy_url << endl;
    }

    // Now extract unique name for subsequent uploads using the actual file path
    if (!existingStreams.empty() && !stream->live_proxy_url.empty())
    {
        // Extract filename from the actual stream URL path
        string filePath = stream->live_proxy_url;
        size_t lastSlash = filePath.find_last_of("/\\");
        if (lastSlash != string::npos && lastSlash < filePath.length() - 1)
        {
            string filename = filePath.substr(lastSlash + 1);
            // Remove extension to get clean name
            size_t lastDot = filename.find_last_of(".");
            if (lastDot != string::npos)
            {
                stream->name = filename.substr(0, lastDot);
            }
            else
            {
                stream->name = filename;
            }
            LOG(warning) << "Updated stream name to: " << stream->name << " (extracted from: " << filePath << ")" << endl;
        }
    }
}


VmsErrorCode addFile(std::shared_ptr<DeviceManager> deviceMngr,
                        const Json::Value& req_info, const Json::Value &data, Json::Value &response)
{
    // Get storage location from req_info, default to Local
    StreamStorageType storageLocation = StreamStorageTypeLocal;
    if (req_info.isMember("storageLocation"))
    {
        int storageLocValue = req_info.get("storageLocation", StreamStorageTypeLocal).asInt();
        // Validate enum range before casting
        if (storageLocValue >= StreamStorageTypeLocal && storageLocValue <= StreamStorageTypeUnknown)
        {
            storageLocation = static_cast<StreamStorageType>(storageLocValue);
        }
        else
        {
            LOG(warning) << "Invalid storageLocation value: " << storageLocValue
                        << ", defaulting to StreamStorageTypeLocal" << endl;
            storageLocation = StreamStorageTypeLocal;
        }
    }

    VmsErrorCode maxSensorsLimitResult = checkMaxSensorsLimit(deviceMngr, response);
    if (maxSensorsLimitResult != VmsErrorCode::NoError)
    {
        return maxSensorsLimitResult;
    }

    CHECK_JSON_OBJECT_IF_ERROR_RETURN(data)
#ifndef RELEASE
        LOG(info) << "Parameters: " << data.toStyledString() << endl;
#endif // !RELEASE
    string result;
    SensorManagement* sensorMgmt = GET_SENSOR_MNGT();
    if (sensorMgmt == nullptr && deviceMngr->getDeviceType() == TYPE_STREAMER)
    {
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, result.c_str())
        LOG(error) << result << endl;
        return VmsErrorCode::VMSInternalError;
    }
    shared_ptr<SensorInfo> sensor (new SensorInfo);
    string rtsp_url = data.get("url", "").asString();
    string device_ip = data.get("ip", "").asString();
    std::string codec;
    std::string frame_rate;
    std::string width;
    std::string height;

    // Parse resolution if provided as "widthxheight" format (e.g., "3840x2400")
    std::string resolution_str = data.get("resolution", "").asString();
    if (!resolution_str.empty() && resolution_str.find("x") != std::string::npos) {
        Resolution temp_resolution;
        temp_resolution = resolution_str;  // Uses operator= to parse "3840x2400"
        width = temp_resolution.width;
        height = temp_resolution.height;
        LOG(info) << "Parsed resolution from '" << resolution_str << "' -> width: " << width << ", height: " << height << endl;
    }

    sensor->user = data.get("username", EMPTY_STRING).asString();
    sensor->password = data.get("password", EMPTY_STRING).asString();
    sensor->name = data.get("name", EMPTY_STRING).asString();
    sensor->location = data.get("file_path", EMPTY_STRING).asString();
    sensor->id = data.get("sensorId", sensor->name).asString();
    sensor->m_notify = true;  // Enable Redis event notifications for this sensor
    sensor->type = deviceMngr->getDeviceType() != TYPE_STREAMER ? std::string(SENSOR_TYPE_FILE) : std::string(SENSOR_TYPE_NVSTREAM);

    // Set sensor URL - use RTSP URL if available, otherwise use file path
    if (!rtsp_url.empty()) {
        sensor->url = rtsp_url;
    } else if (!sensor->location.empty()) {
        sensor->url = sensor->location;  // Use file path as config for file-based sensors
    } else {
        sensor->url = data.get("url", "").asString();  // Fallback to any URL in data
    }

    // Check if RTSP validation is needed and perform it
    bool isRtspUrlValid = (rtsp_url.find("rtsp") != std::string::npos);
    bool isFileSensor = (sensor->type == std::string(SENSOR_TYPE_FILE));

    if (isRtspUrlValid && !isFileSensor)
    {
        bool validationFailed = !validateAndStripRtspUrl(rtsp_url, sensor->ip, sensor->user, sensor->password);
        if (validationFailed)
        {
            string error_message = "Invalid RTSP URL";
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
    }

    shared_ptr<StreamInfo> stream(new StreamInfo);
    
    // Continue with sensor configuration if validation passed
    if (isRtspUrlValid || isFileSensor)
    {
        sensor->sensorId = sensor->id;
        sensor->hardware = data.get("hardware", UNKNOWN_STRING).asString();
        sensor->manufacturer = data.get("manufacturer", UNKNOWN_STRING).asString();
        sensor->serial_number = data.get("serial_number", UNKNOWN_STRING).asString();
        sensor->firmware_version = data.get("firmware_version", UNKNOWN_STRING).asString();
        sensor->hardware_id = data.get("hardware_id", UNKNOWN_STRING).asString();
        sensor->updateSensorStatus(SensorStatusOnline);
        sensor->updateHttpErrorStatus(std::make_pair(200, "No Error"));
        if (sensor->name.empty())
        {
            sensor->name = CAM_DEFAULT_PREFIX;
        }
        stream->sensorId = sensor->id;

        stream->settings.encoderValues.frameRate = frame_rate.empty() ? data.get("framerate", "").asString() : frame_rate;
        stream->settings.encoderValues.resolution.width = width.empty() ? data.get("width", "").asString() : width;
        stream->settings.encoderValues.resolution.height = height.empty() ?  data.get("height", "").asString() : height;
        if (codec.empty() == false)
        {
            toLowerCase(codec);
        }
        stream->settings.encoderValues.encoding = codec.empty() ?  data.get("encoding", "h264").asString() : codec;
        stream->settings.encoderValues.encodingInterval = data.get("keyInt", "0").asString();
        stream->settings.encoderValues.numFrames = data.get("FrameCount", "0").asString();
        stream->duration = stringToInt(data.get("duration", "-1").asString(), -1);
        stream->settings.encoderValues.container = data.get("container", "Quicktime").asString();

        stream->settings.audioEncoderValues.container = data.get("container", EMPTY_STRING).asString();
        stream->settings.audioEncoderValues.encoding = data.get("AudioEncoding", EMPTY_STRING).asString();
        stream->settings.audioEncoderValues.sample_rate = data.get("SampleRate", EMPTY_STRING).asString();
        stream->settings.audioEncoderValues.bits_per_sample = data.get("BitsPerSample", EMPTY_STRING).asString();
        stream->settings.audioEncoderValues.channels = data.get("Channels", EMPTY_STRING).asString();

        // B-frame detection: Check if already provided, otherwise analyze
        if (data.isMember("isBframesPresent"))
        {
            // Use provided value
            bool bframeValue = data.get("isBframesPresent", false).asBool();
            stream->settings.encoderValues.isBframesPresent = bframeValue;
        }
        else
        {
            // Auto-detect B-frames using GstkeyframeParser
            bool hasBframes = false;
            string analyzeSource;

            if (isFileSensor && !sensor->location.empty())
            {
                // For file sensors, analyze the file
                analyzeSource = sensor->location;
                LOG(info) << "Auto-detecting B-frames from file: " << sensor->location << endl;
            }

            if (!analyzeSource.empty())
            {
                GstkeyframeParser keyFrameParser;
                StreamParam param;
                param.m_inFilePath = analyzeSource;
                param.m_inCodec = stream->settings.encoderValues.encoding.empty() ? "h264" : stream->settings.encoderValues.encoding;
                param.m_inContainer = stream->settings.encoderValues.container;
                
                Json::Value parse_result = keyFrameParser.parseKeyframeInterval(param);
                if (parse_result != Json::nullValue)
                {
                    hasBframes = parse_result.get("bFramesPresent", false).asBool();
                    LOG(info) << "B-frame detection result: " << (hasBframes ? "present" : "not present") << endl;
                }
                else
                {
                    LOG(warning) << "B-frame detection failed, defaulting to false (enables low-latency)" << endl;
                    hasBframes = false;
                }
            }
            else
            {
                LOG(verbose) << "No source available for B-frame detection, defaulting to false" << endl;
                hasBframes = false;
            }

            stream->settings.encoderValues.isBframesPresent = hasBframes;
        }

        LOG(info) << "Final B-frame presence flag for stream: " << (stream->settings.encoderValues.isBframesPresent ? "true" : "false") << endl;

        stream->storageLocation = storageLocation;
        stream->stream_type = isFileSensor ? StreamType::FileDownload : StreamType::Rtsp;
    }
    else
    {
        string error_message = string("Invalid Parameters");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
        return VmsErrorCode::InvalidParameterError;
    }

    std::lock_guard<std::mutex> lock(getSensorMutex(sensor->id));
    
    auto dbHelper = GET_DB_INSTANCE();
    vector<SensorStreamsDBColumns> existingStreams = dbHelper->readAllStreamsForGivenSensorID(sensor->id);
    
    if (!existingStreams.empty())
    {
        createSubsequentStream(stream, sensor, existingStreams, response);
    }
    else
    {
        createMainStreamForFirstUpload(stream, sensor, response);
    }
    
    updateStreamUrls(stream, sensor, rtsp_url, deviceMngr, existingStreams);
    
    // If user provided a stream name, override stream->name and ensure it's unique
    string userProvidedStreamName = data.get("userProvidedStreamName", EMPTY_STRING).asString();
    if (!userProvidedStreamName.empty())
    {
        string uniqueStreamName = generateUniqueStreamName(userProvidedStreamName, existingStreams);
        LOG(info) << "Setting stream name to user-provided value: " << uniqueStreamName
                  << " (original: " << stream->name << ")" << endl;
        stream->name = uniqueStreamName;
    }
    
    sensor->addStreams(stream);
    sensor->printInfo();
    
    auto existingSensor = dbHelper->findExistingSensor(sensor, deviceMngr->getDeviceId());
    if (existingSensor)
    {
        LOG(warning) << "Sensor exists already" << endl;
        if (sensor->type == std::string(SENSOR_TYPE_FILE))
        {
            // Internal hand-off to handleFileUpload's rollback logic.
            // mergedExisting marks the merge path; streamId/id let rollback
            // undo the single stream we are about to add. Both keys are
            // stripped from the response before it reaches the API client.
            response["mergedExisting"] = true;
            response["id"] = sensor->id;
            if (!sensor->streams.empty() && sensor->streams[0])
            {
                response["streamId"] = sensor->streams[0]->id;
            }
            return mergeExistingStreams(sensor, deviceMngr, response);
        }
        string error_message = "Sensor exists already, sensorId: " + existingSensor->id + ", sensorName: " + existingSensor->name;
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, response, error_message.c_str())
        return VmsErrorCode::InvalidParameterError;
    }
    
    string sensor_id = "";
#ifdef SENSOR_MODULE
    if (sensorMgmt)
    {
        if (sensorMgmt->addSensorManually(sensor, result) == 0)
        {
            response["id"] = sensor->id;
            if (!response.isMember("streamId") && !sensor->streams.empty() && sensor->streams[0])
            {
                response["streamId"] = sensor->streams[0]->id;
            }
        }
        else
        {
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, response, result.c_str())
            LOG(error) << result << endl;
            return VmsErrorCode::VMSInternalError;
        }
    }
    else
#endif
    {
        string sensor_response_str;
        LOG(info) << "About to add sensor manually - m_notify: " << sensor->m_notify << ", sensor type: " << sensor->type << endl;
        if (vst_common::addSensorManually(sensor, sensor_response_str, deviceMngr) != 0)
        {
            LOG(error) << "Failed to add sensor manually: " << sensor_response_str << endl;
            response["error_code"] = "VMSInternalError";
            response["error_message"] = "Failed to add sensor to sensor management";
            return VmsErrorCode::VMSInternalError;
        }

        LOG(info) << "Sensor added successfully via Redis-based method: " << sensor->id << endl;

        response["id"] = sensor->id;
        if (!response.isMember("streamId") && !sensor->streams.empty() && sensor->streams[0])
        {
            response["streamId"] = sensor->streams[0]->id;
        }
    }
    
   
    if (!sensor->url.empty())
    {
        // TODO: We need to remove this event after we have a proper way to handle file-based sensors
        LOG(info) << "Sending camera_proxy event for file-based sensor: " << sensor->id << endl;
        vst_common::notifySensorStatusEvent(SensorStatusProxy, sensor);

        LOG(info) << "Sending camera_streaming event for file-based sensor: " << sensor->id << endl;
        vst_common::notifySensorStatusEvent(SensorStatusStreaming, sensor);
    }
    
    return VmsErrorCode::NoError;
}

// Forward declarations for helper functions used in handleFileUpload
VmsErrorCode validateUploadParameters(std::shared_ptr<DeviceManager> deviceMngr, const std::string& filename, Json::Value& out);
std::string generateUniqueFileName(const std::string& filename, const std::string& fileLocation);
bool fileExistsWithExtensions(const std::string& path, const std::string& originalFilename);
VmsErrorCode writeUploadedFileToDisk(struct mg_connection* conn, const std::string& filePath, long long contentLength, Json::Value& out, bool exclusiveCreation);

// Post-addFile() rollback: drop the orphan file unconditionally; on the create
// path also remove the just-created sensor; on the merge path remove just the
// stream this upload added so the pre-existing sensor and its prior streams
// are preserved. Bug 5757067.
static void rollbackPostAddFileUpload(FileData& data, const std::shared_ptr<DeviceManager>& deviceMngr)
{
    if (data.m_absoluteFilePath != EMPTY_STRING)
    {
        LOG(info) << "Deleting orphaned upload: " << data.m_absoluteFilePath << endl;
        deleteFile(data.m_absoluteFilePath);
    }
    const std::string sensorId = data.m_parsedMetadata.get("sensorId", EMPTY_STRING).asString();
    if (!data.m_sensorCreatedByUpload)
    {
        if (!data.m_mergedStreamId.empty())
        {
            LOG(info) << "Rolling back merged stream: " << data.m_mergedStreamId
                      << " (sensor " << sensorId << " preserved)" << endl;
            if (GET_DB_INSTANCE()->deleteRowStream(data.m_mergedStreamId) != 0)
            {
                LOG(warning) << "deleteRowStream failed for " << data.m_mergedStreamId
                             << "; stream record may be orphaned" << endl;
            }
            if (deviceMngr && !sensorId.empty())
            {
                deviceMngr->removeStream(data.m_mergedStreamId, sensorId);
            }
        }
        else
        {
            LOG(info) << "Skipping sensor rollback: merged into existing sensor" << endl;
        }
        return;
    }
    if (!sensorId.empty())
    {
        LOG(info) << "Rolling back sensor created by failed upload: " << sensorId << endl;
        vst_sensor::deleteSensor(sensorId);
    }
}

VmsErrorCode handleFileUpload(std::shared_ptr<DeviceManager> deviceMngr, 
        const struct mg_request_info *req_info, struct mg_connection *conn, 
        Json::Value& out, bool isPutUpload, const std::string& putFilename, 
        const std::string& putTimestamp, 
        const std::string& putSensorId, 
        bool isLegacyUpload)
{
    Json::Value req;
    Json::Value in;
    VmsErrorCode result = VmsErrorCode::NoError;
    std::string ans;
    std::string streamId;
    struct FileData data;
    data.m_conn = conn;
    data.m_absoluteFilePath = EMPTY_STRING;
    bool is_transcode = false;
    int transcode_framerate_int = 0;
    int transcode_bitrate_int = 0;
    int transcode_keyframe_interval_int = 0;
    DeviceConfig config =  GET_CONFIG();
    VmsConfigManager* configMngr = VmsConfigManager::getInstance();
    std::string fileLocation = config.nv_streamer_directory_path;
    std::string sensorId;
    Json::Value timestampValue;

    const char *chunkNumber = nullptr;
    const char *chunkIdentifier = nullptr;
    const char *enable_transcode = nullptr;
    
    Json::Value mediaInfo;
    string container, codec, frameRate = "30", duration;
    string ctr_caps, video_caps, audio_caps;
    uint bitrate = 0, framerate_num = 0, framerate_denom = 0;
    bool is_user_provided_timestamp = false;

    // Handle PUT upload (raw binary data)
    if (isPutUpload)
    {
        // Validate filename
        VmsErrorCode validationResult = validateUploadParameters(deviceMngr, putFilename, out);
        if (validationResult != VmsErrorCode::NoError)
        {
            return validationResult;
        }

        // Ensure proper path concatenation
        if (fileLocation.back() == '/')
        {
            fileLocation.pop_back();
        }

        std::string uniqueFileName;
        std::string uniqueFilePath;

        if (isLegacyUpload)
        {
            uniqueFileName = generateUniqueFileName(putFilename, fileLocation);
            uniqueFilePath = fileLocation + "/" + uniqueFileName;
        }
        else
        {
            uniqueFileName = putFilename;
            uniqueFilePath = fileLocation + "/" + uniqueFileName;
            
            if (fileExistsWithExtensions(uniqueFilePath, uniqueFileName))
            {
                out = Json::nullValue;
                string error_message = string("File already exists: ") + uniqueFileName;
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::ResourceConflictError, out, error_message.c_str());
                return VmsErrorCode::ResourceConflictError;
            }
        }

        const char* contentLengthHeader = mg_get_header(conn, "Content-Length");
        if (contentLengthHeader == nullptr)
        {
            out = Json::nullValue;
            string error_message = string("Content-Length header is required for raw upload");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }

        long long contentLength = 0;
        try
        {
            contentLength = std::stoll(contentLengthHeader);
        }
        catch (const std::exception& e)
        {
            out = Json::nullValue;
            string error_message = string("Invalid Content-Length header: ") + e.what();
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }

        if (contentLength <= 0)
        {
            out = Json::nullValue;
            string error_message = string("Content-Length must be greater than 0");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }

        size_t availableSpace = getAvailableSpace(fileLocation);
        if (static_cast<size_t>(contentLength) > availableSpace)
        {
            out = Json::nullValue;
            string error_message = string("Insufficient storage space for upload");
            LOG(error) << error_message << " required: " << contentLength << " available: " << availableSpace << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInsufficientStorage, out, error_message.c_str());
            return VmsErrorCode::VMSInsufficientStorage;
        }

        // Write uploaded file data to disk
        VmsErrorCode writeResult = writeUploadedFileToDisk(conn, uniqueFilePath, contentLength, out, !isLegacyUpload);
        if (writeResult != VmsErrorCode::NoError)
        {
            return writeResult;
        }

        // Setup data structure to reuse existing POST logic
        data.m_absoluteFilePath = uniqueFilePath;
        data.m_fileName = uniqueFileName;
        data.m_fileSize = static_cast<uint64_t>(contentLength);
        data.m_isFileReceived = true;
        data.m_isFileSaved = true;
        data.m_isLastChunk = true;
        data.m_isChunkedUpload = false;
        data.m_hasError = false;
        // Leave data.m_mediaFilePath empty (will use absoluteFilePath)

        // Setup metadata
        std::string effectiveTimestamp = putTimestamp;
        if (effectiveTimestamp.empty())
        {
            effectiveTimestamp = "0";
        }

        std::string generatedSensorId;
        if (isLegacyUpload)
        {
            generatedSensorId = generate_uuid();
        }
        else
        {
            generatedSensorId = !putSensorId.empty() ? putSensorId : generate_uuid();
        }

        data.m_parsedMetadata["sensorId"] = generatedSensorId;
        data.m_parsedMetadata["timestamp"] = static_cast<Json::Int64>(getEpocTimeInMS(effectiveTimestamp));
        data.m_parsedMetadata["streamName"] = getFileName(uniqueFilePath);
        data.m_hasMetadata = true;
        sensorId = generatedSensorId;
        timestampValue = data.m_parsedMetadata["timestamp"];
        is_user_provided_timestamp = true;
        in["sensorId"] = generatedSensorId;
    }
    else
    {
        // POST multipart form handling
        //get chunks information from header (optional)
        chunkNumber = mg_get_header(conn, "nvstreamer-chunk-number");
        const char *totalChunks = mg_get_header(conn, "nvstreamer-total-chunks");
        const char *isLastChunk = mg_get_header(conn, "nvstreamer-is-last-chunk");
        chunkIdentifier = mg_get_header(conn, "nvstreamer-identifier");
        const char *fileName = mg_get_header(conn, "nvstreamer-file-name");
        const char *enable_transcode = mg_get_header(conn, "nvstreamer-enable-transcode");
        const char *transcode_framerate = mg_get_header(conn, "transcode-framerate");
        if (transcode_framerate == nullptr)
            transcode_framerate = mg_get_header(conn, "nvstreamer-transcode-framerate");
        const char *transcode_bitrate = mg_get_header(conn, "transcode-bitrate");
        if (transcode_bitrate == nullptr)
            transcode_bitrate = mg_get_header(conn, "nvstreamer-transcode-bitrate");
        const char *transcode_keyframe_interval = mg_get_header(conn, "transcode-keyframe-interval");
        if (transcode_keyframe_interval == nullptr)
            transcode_keyframe_interval = mg_get_header(conn, "nvstreamer-transcode-keyframe-interval");

        // Determine if this is a chunked upload or single file upload
        bool isChunkedUpload = (chunkNumber != nullptr && totalChunks != nullptr &&
                               isLastChunk != nullptr && chunkIdentifier != nullptr);

    if (enable_transcode != nullptr)
    {
        is_transcode = strcmp(enable_transcode, "true") == 0 ? true : false;
    }
    if (transcode_framerate != nullptr)
    {
        transcode_framerate_int = stringToInt(transcode_framerate, 30);
    }
    if (transcode_bitrate != nullptr)
    {
        transcode_bitrate_int = stringToInt(transcode_bitrate, 0);
    }
    if (transcode_keyframe_interval != nullptr)
    {
        transcode_keyframe_interval_int = stringToInt(transcode_keyframe_interval, 0);
    }

    if (isChunkedUpload)
    {
        // Validate all chunking headers are present for chunked upload
        if(!chunkNumber || !totalChunks || !isLastChunk || !chunkIdentifier || !fileName)
        {
            out = Json::nullValue;
            string error_message = string("Incomplete chunked upload headers - all chunking headers must be provided together");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }

        LOG(info) << "Processing chunked upload - chunkNumber: " << chunkNumber
                 << ", chunkIdentifier: " << chunkIdentifier
                 << ", isLastChunk: " << isLastChunk << endl;
        LOG(verbose) << "totalChunks: " << totalChunks << endl;

        data.m_chunkIdentifier = string(chunkIdentifier);
        data.m_isLastChunk = strcmp(isLastChunk, "true") == 0 ? true : false;
    }
    else
    {
        // Single file upload mode
        LOG(info) << "Processing single file upload (no chunking headers detected)" << endl;

        // Generate a unique identifier for single uploads
        data.m_chunkIdentifier = "single_" + std::to_string(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
        data.m_isLastChunk = true; // Single upload is always the "last chunk"

        // fileName can come from header or will be extracted from form data
        if (fileName)
        {
            LOG(info) << "Using fileName from header: " << fileName << endl;
        }
        else
        {
            LOG(info) << "fileName will be extracted from form data" << endl;
        }
    }
    // Validate fileName if provided in header (required for chunked uploads)
    if (fileName)
    {
        if(checkWhiteSpace(fileName))
        {
            out = Json::nullValue;
            string error_message = string("Whitespaces not allowed in file name");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }

        if (checkFileNameLength(fileName))
        {
            out = Json::nullValue;
            string error_message = string("File name is too long");
            LOG(error) << error_message << " for file: " << fileName << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }
    }
    else if (isChunkedUpload)
    {
        // fileName is required for chunked uploads
        out = Json::nullValue;
        string error_message = string("nvstreamer-file-name header is required for chunked uploads");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }
    // For single uploads, fileName will be extracted from form data if not provided in header

        // Store upload mode and filename in data structure
        data.m_isChunkedUpload = isChunkedUpload;
        if (fileName)
        {
            data.m_fileName = string(fileName);
        }

        //assign form handler callbacks
        struct mg_form_data_handler fdh = {field_found, field_get, field_stored, (void *)&data};

        mg_handle_form_request(conn, &fdh);
    }

    if(data.m_mediaFilePath.empty() == false)
    {
        if (data.m_mediaFilePath[0] == '/')
        {
            out = Json::nullValue;
            string error_message = string("mediaFilePath must be relative (must not start with '/')");
            LOG(error) << error_message << ": " << data.m_mediaFilePath << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }
    }

    // Check for callback errors first
    if (data.m_hasError)
    {
        out = Json::nullValue;
        LOG(error) << "Form processing failed: " << data.m_errorMessage << endl;

        // Clean up any temporary files and directories if error occurred
        if (data.m_isChunkedUpload && !data.m_tempDirectory.empty())
        {
            LOG(info) << "Cleaning up temporary directory due to error: " << data.m_tempDirectory << endl;
            deleteDirectory(data.m_tempDirectory);
        }
        if (!data.m_absoluteFilePath.empty())
        {
            LOG(info) << "Cleaning up file due to error: " << data.m_absoluteFilePath << endl;
            deleteFile(data.m_absoluteFilePath);
        }

        SET_VMS_ERROR2(data.m_errorCode, out, data.m_errorMessage.c_str());
        return data.m_errorCode;
    }

    // Process captured JSON metadata (POST only, PUT already processed metadata)
    if (!isPutUpload && deviceMngr->getDeviceType() != TYPE_STREAMER)
    {
        if (data.m_hasMetadata)
        {
            LOG(info) << "Processing file upload with metadata" << endl;
            LOG(verbose) << "JSON Metadata received: " << data.m_jsonMetadata << endl;
            if (!data.m_parsedMetadata.isNull())
            {
                sensorId = data.m_parsedMetadata.get("sensorId", generate_uuid()).asString();
                if (data.m_parsedMetadata.get("timestamp", Json::Value::null).isString())
                {
                    is_user_provided_timestamp = true;
                }
                int64_t parsedTimestamp = parseTimestampValue(data.m_parsedMetadata.get("timestamp", Json::Value::null));
                if (parsedTimestamp < 0) {
                    LOG(warning) << "Negative timestamp detected: " << parsedTimestamp << ", using current timestamp" << endl;
                    parsedTimestamp = getCurrentUnixTimestampInMs();
                    is_user_provided_timestamp = false;
                }
                timestampValue = Json::Value(static_cast<Json::UInt64>(parsedTimestamp));
                if (timestampValue.asUInt64() == 0)
                {
                    LOG(warning) << "Timestamp is 0, setting to current timestamp" << endl;
                    timestampValue = getCurrentUnixTimestampInMs();
                    is_user_provided_timestamp = false;
                }
                timestampValue = getTimestampInMilliSecond(timestampValue.asUInt64());
                data.m_parsedMetadata["timestamp"] = timestampValue;
                data.m_parsedMetadata["sensorId"] = sensorId;
                in["sensorId"] = sensorId;
            }
        }
        else
        {
            LOG(warning) << "No metadata provided with file upload generating new sensorId and timestamp" << endl;
            sensorId = generate_uuid();
            timestampValue = getCurrentUnixTimestampInMs();
            data.m_parsedMetadata["timestamp"] = timestampValue;
            data.m_parsedMetadata["sensorId"] = sensorId;
            in["sensorId"] = sensorId;
            data.m_hasMetadata = true;
        }
    }

    // Process file assembly (chunked uploads) or validate single upload
    if(data.m_isLastChunk && (data.m_isFileReceived && data.m_isFileSaved))
    {
        if (data.m_isChunkedUpload)
        {
            LOG(info) << "Assembling chunked upload for file: " << data.m_fileName << endl;

            //if this is the last chunk then start appending all chunks
            vector<string> fileChunks = getFilesInDirectory(data.m_tempDirectory);
            if (fileChunks.size() == 0)
            {
                if (data.m_tempDirectory != EMPTY_STRING)
                {
                    LOG(info) << "Deleting chunks: " << data.m_tempDirectory << endl;
                    deleteDirectory(data.m_tempDirectory);
                    deleteFile(data.m_absoluteFilePath);
                }
                out = Json::nullValue;
                string error_message = string("Chunks were not found on disk");
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
                return VmsErrorCode::VMSInternalError;
            }

            string firstChunk = fileChunks[0];
            std::string uniqueFilePath = getUniqueFilePath(data.m_fileName, fileLocation);
            data.m_absoluteFilePath = uniqueFilePath;

            //copy first chunk to nvstreamer directory and rename it to unique filename
            std::ifstream first_file( firstChunk, std::ios::binary );
            std::ofstream output_file( uniqueFilePath, std::ios::app ) ;

            // Check if files opened successfully
            if (!first_file.is_open() || !output_file.is_open())
            {
                if (data.m_tempDirectory != EMPTY_STRING)
                {
                    LOG(info) << "Deleting chunks due to file open error: " << data.m_tempDirectory << endl;
                    deleteDirectory(data.m_tempDirectory);
                }
                out = Json::nullValue;
                string error_message = string("Failed to open files for chunk assembly");
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
                return VmsErrorCode::VMSInternalError;
            }

            output_file << first_file.rdbuf();

            // Check for I/O errors during first chunk copy
            if (first_file.bad() || output_file.bad())
            {
                first_file.close();
                output_file.close();
                if (data.m_tempDirectory != EMPTY_STRING)
                {
                    LOG(error) << "Deleting chunks due to I/O error: " << data.m_tempDirectory << endl;
                    deleteDirectory(data.m_tempDirectory);
                    deleteFile(uniqueFilePath);
                }
                out = Json::nullValue;
                string error_message = string("File I/O error during first chunk assembly");
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
                return VmsErrorCode::VMSInternalError;
            }

            first_file.close();
            output_file.close();
            deleteFile(fileChunks[0]);

            //if more chunks present then append them to first chunk
            for(size_t i = 1; i < fileChunks.size(); i++)
            {
                //append all chunks to the first chunk
                std::ifstream second_file( fileChunks[i], std::ios::binary );
                std::ofstream output_file_append( uniqueFilePath, std::ios::app );

                // Check if files opened successfully
                if (!second_file.is_open() || !output_file_append.is_open())
                {
                    if (data.m_tempDirectory != EMPTY_STRING)
                    {
                        LOG(info) << "Deleting chunks due to file open error: " << data.m_tempDirectory << endl;
                        deleteDirectory(data.m_tempDirectory);
                        deleteFile(uniqueFilePath);
                    }
                    out = Json::nullValue;
                    string error_message = string("Failed to open files for chunk ") + std::to_string(i) + string(" assembly");
                    LOG(error) << error_message << endl;
                    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
                    return VmsErrorCode::VMSInternalError;
                }

                output_file_append << second_file.rdbuf() ;

                // Check for I/O errors during chunk append
                if (second_file.bad() || output_file_append.bad())
                {
                    second_file.close();
                    output_file_append.close();
                    if (data.m_tempDirectory != EMPTY_STRING)
                    {
                        LOG(info) << "Deleting chunks due to I/O error: " << data.m_tempDirectory << endl;
                        deleteDirectory(data.m_tempDirectory);
                        deleteFile(uniqueFilePath);
                    }
                    out = Json::nullValue;
                    string error_message = string("File I/O error during chunk ") + std::to_string(i) + string(" assembly");
                    LOG(error) << error_message << endl;
                    SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
                    return VmsErrorCode::VMSInternalError;
                }

                second_file.close();
                output_file_append.close();
                deleteFile(fileChunks[i]);
            }
            //delete temporary directory
            deleteDirectory(data.m_tempDirectory);
        }
        else
        {
            LOG(info) << "Processing single file upload: " << data.m_fileName << endl;
            // For single uploads, file is already in final location (set in field_found)
            // Just verify it exists
            if (data.m_absoluteFilePath.empty() || !isFileExist(data.m_absoluteFilePath))
            {
                out = Json::nullValue;
                string error_message = string("Single file upload failed - file not found");
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
                return VmsErrorCode::VMSInternalError;
            }
        }
    }
    std::string filePath = data.m_absoluteFilePath;
    if(data.m_isLastChunk)
    {
        if ((filePath.empty() || !isFileExist(filePath)) )
        {
            if (data.m_mediaFilePath.empty() || data.m_parsedMetadata.get("timestamp", 0).asUInt64() == 0 || data.m_parsedMetadata.get("sensorId", EMPTY_STRING).asString().empty())
            {
                out = Json::nullValue;
                string error_message = string("Timestamp is 0 OR sensorId OR mediaFilePath is not present");
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
                return VmsErrorCode::VMSInternalError;
            }
            else
            {
                filePath = data.m_absoluteFilePath = GET_CONFIG().nv_streamer_directory_path + data.m_mediaFilePath;
            }
        }
        else
        {
            data.m_parsedMetadata["mediaFilePath"] = filePath;
        }

        // File is ready - extract media information (common for both POST and PUT)
        if (!isFileExist(filePath))
        {
            LOG(error) << "File path = " << filePath << " not present" << endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, "File not present");
            return VmsErrorCode::InvalidParameterError;
        }
        
        Json::Value mediaInfoResult = isPutUpload ? mediaInfo : out;
        if (getMediaInformation(filePath, mediaInfoResult) == 0)
        {
            container = mediaInfoResult.get("Container", EMPTY_STRING).asString();
            codec = mediaInfoResult.get("Codec", EMPTY_STRING).asString();
            frameRate = mediaInfoResult.get("Framerate", "30").asString();
            duration = mediaInfoResult.get("Duration", "0").asString();
            framerate_num = mediaInfoResult.get("FramerateNum", 0).asUInt();
            framerate_denom = mediaInfoResult.get("FramerateDenom", 0).asUInt();
            bitrate = mediaInfoResult.get("Bitrate", 0).asInt();
            ctr_caps = mediaInfoResult.get("ContainerCaps", EMPTY_STRING).asString();
            video_caps = mediaInfoResult.get("VideoCaps", EMPTY_STRING).asString();
            audio_caps = mediaInfoResult.get("AudioCaps", EMPTY_STRING).asString();
            LOG(info) << "Media Information: " << mediaInfoResult.toStyledString() << endl;
        }
        else
        {
            LOG(error) << "Failed to get media information for file: " << filePath << ", cleaning up uploaded file" << endl;
            if (data.m_tempDirectory != EMPTY_STRING)
            {
                deleteDirectory(data.m_tempDirectory);
            }
            if (data.m_absoluteFilePath != EMPTY_STRING)
            {
                LOG(info) << "Deleting orphaned upload: " << data.m_absoluteFilePath << endl;
                deleteFile(data.m_absoluteFilePath);
            }
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, "Failed to get media information");
            return VmsErrorCode::InvalidParameterError;
        }

        // Add extension if missing (for files uploaded without extension)
        if (getFileExtension(filePath).empty())
        {
            std::string containerExt;
            for (const auto& ext : config.media_containers)
            {
                const std::string synonym = configMngr->getContainerType(ext);
                if (iequals(synonym, container) || container.find(synonym) != std::string::npos)
                {
                    containerExt = ext;
                    break;
                }
            }
            if (containerExt.empty() && !config.media_containers.empty())
            {
                containerExt = config.media_containers.front();
            }

            if (!containerExt.empty())
            {
                if (containerExt.front() != '.')
                {
                    containerExt = "." + containerExt;
                }

                std::string newFilePath = filePath + containerExt;
                if (std::rename(filePath.c_str(), newFilePath.c_str()) == 0)
                {
                    LOG(verbose) << "Added extension to file: " << filePath << " -> " << newFilePath << endl;
                    filePath = data.m_absoluteFilePath = newFilePath;
                }
                else
                {
                    LOG(warning) << "Failed to rename file to add extension, continuing with original path" << endl;
                }
            }
        }

        // Validate container and codec
        if (configMngr->isVideoContainerSupported(container, data.m_absoluteFilePath) == false)
        {
            if (data.m_tempDirectory != EMPTY_STRING)
            {
                deleteDirectory(data.m_tempDirectory);
            }
            if (data.m_absoluteFilePath != EMPTY_STRING)
            {
                LOG(info) << "Deleting orphaned upload: " << data.m_absoluteFilePath << endl;
                deleteFile(data.m_absoluteFilePath);
            }
            out = Json::nullValue;
            string error_message = string("Format not supported");
            LOG(error) << error_message << " for file: " << data.m_absoluteFilePath << endl;
            SET_VMS_ERROR2(VmsErrorCode::UnsupportedMediaTypeError, out, error_message.c_str());
            return VmsErrorCode::UnsupportedMediaTypeError;
        }
        
        if (configMngr->isVideoFormatSupported(codec) == false)
        {
            if (data.m_tempDirectory != EMPTY_STRING)
            {
                deleteDirectory(data.m_tempDirectory);
            }
            if (data.m_absoluteFilePath != EMPTY_STRING)
            {
                LOG(info) << "Deleting orphaned upload: " << data.m_absoluteFilePath << endl;
                deleteFile(data.m_absoluteFilePath);
            }
            out = Json::nullValue;
            string error_message = string("Video encode format not supported: ") + codec;
            LOG(error) << error_message << " for file: " << data.m_absoluteFilePath << endl;
            SET_VMS_ERROR2(VmsErrorCode::UnprocessableEntityError, out, error_message.c_str());
            return VmsErrorCode::UnprocessableEntityError;
        }

        // Populate in object with media info
        string file_name = getFileName(filePath);
        in["name"] = file_name;
        
        if (!sensorId.empty())
        {
            in["sensorId"] = sensorId;
        }

        if (data.m_hasMetadata && !data.m_parsedMetadata.isNull())
        {
            string streamName = data.m_parsedMetadata.get("streamName", EMPTY_STRING).asString();
            if (!streamName.empty())
            {
                LOG(info) << "User provided streamName in metadata: " << streamName << endl;
                in["userProvidedStreamName"] = streamName;
            }
        }
        in["file_path"] = filePath;

        if (deviceMngr->getDeviceType() == TYPE_STREAMER)
        {
            in["url"] = vst_rtsp::rtspUrlPrefix(file_name) + string(NV_STREAMER) + filePath;
        }
        
        in["FrameCount"] = mediaInfoResult.get("FrameCount", EMPTY_STRING).asString();
        in["AudioEncoding"] = mediaInfoResult.get("AudioCodec", EMPTY_STRING).asString();
        in["SampleRate"] = mediaInfoResult.get("SampleRate", EMPTY_STRING).asString();
        in["BitsPerSample"] = mediaInfoResult.get("Depth", EMPTY_STRING).asString();
        in["Channels"] = mediaInfoResult.get("Channels", EMPTY_STRING).asString();
        in["resolution"] = mediaInfoResult.get("Width", EMPTY_STRING).asString() + "x" + mediaInfoResult.get("Height", EMPTY_STRING).asString();
        in["container"] = container;
        in["encoding"] = codec;
        in["framerate"] = frameRate;
        in["duration"] = duration;

        /* Decide whether this file needs to be transcoded based on bframes or large i-frames */
        GstkeyframeParser keyFrameParser;
        StreamParam param;
        param.m_inFilePath = filePath;
        param.m_inCodec = codec;
        param.m_inContainer = container;

        Json::Value parse_result = keyFrameParser.parseKeyframeInterval(param);
        if (parse_result == Json::nullValue)
        {
            string error_message = string("Key frame interval parsing failed");
            LOG(error) << error_message << " for file: " << data.m_absoluteFilePath << endl;
        }

        int original_framerate = static_cast<int>(std::round(stringToDouble(frameRate, DEFAULT_FRAMERATE)));
        int original_keyframe_interval = parse_result.get("keyInt", original_framerate).asInt();
        bool is_bframesPresent = parse_result.get("bFramesPresent", false).asBool();
        bool is_largeIdrPresent = parse_result.get("largeIdrFramesPresent", false).asBool();
        bool is_largeBitratePresent = (bitrate > DEFAULT_NVSTREAMER_MAX_BITRATE) ? true : false;
        if (is_largeBitratePresent)
        {
            bitrate = DEFAULT_NVSTREAMER_MAX_BITRATE;
        }

        LOG(warning) << "keyFrameParseResult: bFramesPresent:" << is_bframesPresent <<
            ", largeIdrFramesPresent:" << is_largeIdrPresent <<
            ", largeBitratePresent:" << is_largeBitratePresent <<
            ", original_framerate:" << original_framerate <<
            ", original_keyframe_interval:" << original_keyframe_interval <<
            ", deviceType:" << deviceMngr->getDeviceType() << endl;

        // Add B-frame presence flag to stream data for database storage
        if (enable_transcode != NULL && strcmp(enable_transcode, "true") == 0)
        {
            is_bframesPresent = false;
            LOG(info) << "B-frames presence flag disabled due to transcoding" << endl;
        }
        in["isBframesPresent"] = is_bframesPresent;

        if (transcode_keyframe_interval_int > 0 && original_keyframe_interval != transcode_keyframe_interval_int)
        {
            LOG(warning) << "---###--- Transcoding the video file. keyframe interval is changed from "
                << original_keyframe_interval << " to " << transcode_keyframe_interval_int << endl;
            is_transcode = true;
        }
        else if (original_keyframe_interval > (MAX_KEYFRAME_INTERVAL_SEC * original_framerate))
        {
            transcode_keyframe_interval_int = original_framerate;
            LOG(warning) << "---###--- Transcoding the video file. keyframe interval is too large, transcoding to "
                << transcode_keyframe_interval_int << endl;
            is_transcode = true;
        }

        // Check device type - force transcoding for B-frames if NOT VST
        bool is_vst_device = (deviceMngr->getDeviceType() == TYPE_VST);
        if (is_bframesPresent && (!is_vst_device || is_transcode))
        {
            LOG(warning) << "---###--- Transcoding required: B-frames detected from non-VST adaptor (device type: " 
                         << deviceMngr->getDeviceType() << ") or transcode is true" << endl;
            // After transcoding, B-frames will be removed
            in["isBframesPresent"] = false;
            is_transcode = true;
        }

        if (is_transcode)
        {
            GstTranscode::TranscodeParam enc_params;
            enc_params.m_inCodec = codec;
            enc_params.m_inContainer = container;
            enc_params.m_inFilePath = filePath;
            if (transcode_framerate_int > 0)
            {
                enc_params.m_isUserFrameRate = true;
                enc_params.m_outframeRate = transcode_framerate_int;
                enc_params.m_framerateNum = transcode_framerate_int;
                enc_params.m_framerateDenom = 1; // to declare fraction
            }
            else
            {
                enc_params.m_outframeRate = static_cast<int>(std::round(stringToDouble(frameRate, DEFAULT_FRAMERATE)));
                enc_params.m_framerateNum = framerate_num;
                enc_params.m_framerateDenom = framerate_denom;
            }
            enc_params.m_fileFrameRate = static_cast<int>(std::round(stringToDouble(frameRate, DEFAULT_FRAMERATE)));
            enc_params.m_fileFramerateNum = framerate_num;
            enc_params.m_fileFramerateDenom = framerate_denom;
            enc_params.m_outBitrate = transcode_bitrate_int > 0 ? transcode_bitrate_int : bitrate;
            if (transcode_keyframe_interval_int > 0)
            {
                enc_params.m_outKeyFrameInterval = transcode_keyframe_interval_int;
            }
            else
            {
                enc_params.m_outKeyFrameInterval = enc_params.m_outframeRate;
            }
            enc_params.m_allIframes = false;
            enc_params.m_outFilePath = fileLocation + string("/transcoded_") + getFileName(filePath);
            enc_params.m_inCtrCaps = ctr_caps;
            enc_params.m_inVideoCaps = video_caps;
            enc_params.m_inAudioCaps = audio_caps;

            LOG(info) << "Transcode parameters: " << enc_params.m_outFilePath
                << " " << enc_params.m_outframeRate << " " << enc_params.m_outKeyFrameInterval
                << " " << enc_params.m_outBitrate << endl;

            if (TranscodeTaskManager::getInstace()->addTask(enc_params))
            {
                replaceFile(enc_params.m_outFilePath, enc_params.m_inFilePath);

                /* The transcoded file has replaced the upload at filePath.
                 * Re-read media info so the fields persisted below reflect
                 * the post-transcode bitstream rather than the pre-transcode
                 * upload. Without this, codec / container / audio / frame
                 * count are recorded against the original file and the
                 * playback pipeline (which selects h264parse vs h265parse
                 * from the persisted encoding) can fail caps negotiation.
                 *
                 * If the re-read fails, we keep the pre-transcode values
                 * and rely on the conditional encoding patch below as a
                 * fallback. */
                Json::Value postMediaInfo;
                if (getMediaInformation(filePath, postMediaInfo) == 0)
                {
                    mediaInfoResult = postMediaInfo;
                    container       = mediaInfoResult.get("Container", container).asString();
                    codec           = mediaInfoResult.get("Codec", codec).asString();
                    frameRate       = mediaInfoResult.get("Framerate", frameRate).asString();
                    duration        = mediaInfoResult.get("Duration", duration).asString();
                    framerate_num   = mediaInfoResult.get("FramerateNum", framerate_num).asUInt();
                    framerate_denom = mediaInfoResult.get("FramerateDenom", framerate_denom).asUInt();
                    bitrate         = mediaInfoResult.get("Bitrate", bitrate).asInt();

                    in["FrameCount"]    = mediaInfoResult.get("FrameCount", EMPTY_STRING).asString();
                    in["AudioEncoding"] = mediaInfoResult.get("AudioCodec", EMPTY_STRING).asString();
                    in["SampleRate"]    = mediaInfoResult.get("SampleRate", EMPTY_STRING).asString();
                    in["BitsPerSample"] = mediaInfoResult.get("Depth", EMPTY_STRING).asString();
                    in["Channels"]      = mediaInfoResult.get("Channels", EMPTY_STRING).asString();
                    in["resolution"]    = mediaInfoResult.get("Width", EMPTY_STRING).asString() + "x"
                                        + mediaInfoResult.get("Height", EMPTY_STRING).asString();
                    in["container"]     = container;
                    in["encoding"]      = codec;
                    in["duration"]      = duration;
                }
                else
                {
                    LOG(error) << "Failed to re-read media info after transcode of "
                               << filePath << "; persisted metadata may be stale" << endl;
                }
            }
            else
            {
                if (data.m_tempDirectory != EMPTY_STRING)
                {
                    LOG(info) << "Deleting chunks: " << data.m_tempDirectory << endl;
                    deleteDirectory(data.m_tempDirectory);
                }
                if (data.m_absoluteFilePath != EMPTY_STRING)
                {
                    LOG(info) << "Deleting orphaned upload: " << data.m_absoluteFilePath << endl;
                    deleteFile(data.m_absoluteFilePath);
                }
                out = Json::nullValue;
                string error_message = string("Transcoding failed");
                LOG(error) << error_message << endl;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
                return VmsErrorCode::InvalidParameterError;
            }
            deleteFile(enc_params.m_outFilePath);
            in["keyInt"] = enc_params.m_outKeyFrameInterval;
            in["framerate"] = enc_params.m_outframeRate;
        }

        result = addFile(deviceMngr, req, in, out);
        // addFile sets out["mergedExisting"] iff it took the merge path, in
        // which case out["streamId"] identifies the just-merged stream.
        // Reading both here keeps the create-vs-merge decision atomic with
        // addFile's own findExistingSensor() check (no TOCTOU window).
        data.m_sensorCreatedByUpload = !out.get("mergedExisting", false).asBool();
        if (!data.m_sensorCreatedByUpload)
        {
            data.m_mergedStreamId = out.get("streamId", EMPTY_STRING).asString();
        }
        out.removeMember("mergedExisting");
        if(result != VmsErrorCode::NoError)
        {
            LOG(error) << getCameraErrorCodeString(result).second << endl;
            if (data.m_tempDirectory != EMPTY_STRING)
            {
                deleteDirectory(data.m_tempDirectory);
            }
            if (data.m_absoluteFilePath != EMPTY_STRING)
            {
                LOG(info) << "Deleting orphaned upload: " << data.m_absoluteFilePath << endl;
                deleteFile(data.m_absoluteFilePath);
            }
            return result;
        }
        streamId = out.get("id", EMPTY_STRING).asString();

        // Process metadata if we have it
        if (data.m_hasMetadata && !data.m_parsedMetadata.isNull() && deviceMngr->getDeviceType() != TYPE_STREAMER)
        {
            Json::Value metadataResponse;
            StorageManagement* storageMgmt = GET_STORAGE_MNGT();
            if (storageMgmt)
            {
                VmsErrorCode metadataResult = storageMgmt->processUploadMetadata(data.m_parsedMetadata, filePath, metadataResponse);
                if (metadataResult == VmsErrorCode::NoError)
                {
                    // Merge metadata response into main response
                    VideoRecordDBColumns row;

                    row.sensor_id_value = data.m_parsedMetadata.get("sensorId", EMPTY_STRING).asString();
                    row.sensor_name_value = data.m_parsedMetadata.get("streamName", EMPTY_STRING).asString();
                    if (row.sensor_name_value.empty())
                    {
                        auto sensorInfo = deviceMngr->getSensorInfo(row.sensor_id_value);
                        if (sensorInfo)
                        {
                            row.sensor_name_value = sensorInfo->name;
                        }
                    }
                    row.stream_id_value = out.get("streamId", EMPTY_STRING).asString();

                    // Validate timestamp format - must be epoch milliseconds (integer)
                    if (timestampValue.isNull())
                    {
                        out = Json::nullValue;
                        string error_message = "Missing required 'timestamp' field in metadata setting current time";
                        LOG(warning) << error_message << endl;
                        timestampValue = getCurrentUnixTimestampInMs();
                        data.m_parsedMetadata["timestamp"] = timestampValue;
                    }

                    if (!timestampValue.isNumeric() || !timestampValue.isUInt64())
                    {
                        string error_message = "Invalid timestamp format. Expected epoch time in milliseconds (integer), got: " + timestampValue.asString();
                        LOG(error) << error_message << endl;
                        rollbackPostAddFileUpload(data, deviceMngr);
                        out = Json::nullValue;
                        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
                        return VmsErrorCode::InvalidParameterError;
                    }

                    uint64_t end_time_value = 0;
                    uint64_t timestamp_ms = 0;
                    double duration_sec = stringToDouble(in["duration"].asString(), 0.0);
                    row.duration_value = duration_sec > 0.0 ? static_cast<uint64_t>(duration_sec * 1000.0 + 0.5) : 0;
                    uint64_t duration_ms = static_cast<uint64_t>(row.duration_value);
                    try
                    {
                        timestamp_ms = timestampValue.asUInt64();
                    }
                    catch (const Json::LogicError& e)
                    {
                        string error_message = "Failed to parse timestamp as integer: " + string(e.what()) + ". Expected epoch time in milliseconds (integer), got: " + timestampValue.asString();
                        LOG(error) << error_message << endl;
                        rollbackPostAddFileUpload(data, deviceMngr);
                        out = Json::nullValue;
                        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
                        return VmsErrorCode::InvalidParameterError;
                    }

                    // If timestamp is user provided, use it as start time
                    if (is_user_provided_timestamp)
                    {
                        row.start_time_value = timestamp_ms;
                    }
                    else
                    {
                        end_time_value = timestamp_ms;
                        if (duration_ms > 0 && end_time_value > duration_ms)
                        {
                            row.start_time_value = end_time_value - duration_ms;
                        }
                        else
                        {
                            // Set 01-Jan-2025 as start time for unknown duration
                            row.start_time_value = DEFAULT_START_TIME_EPOCH;
                            LOG(warning) << "Duration is 0, setting start time to " << convertEpocToISO8601_2(DEFAULT_START_TIME_EPOCH * 1000) << endl;
                        }
                    }

                    row.filepath_value = data.m_mediaFilePath == EMPTY_STRING ? filePath : data.m_mediaFilePath;
                    row.resolution_value = in["resolution"].asString();
                    string framerateStr = in["framerate"].asString();
                    row.filefps_value = framerateStr.empty() ? 0 : static_cast<uint64_t>(std::round(std::stof(framerateStr)));
                    row.codec_value = in["encoding"].asString();
                    row.object_id_value = streamId = generate_uuid();
                    row.filesize_value = data.m_fileSize = getFileSizeInBytes(filePath);
                    row.record_config_value = "User"; // User refers to user recorded clip
                    row.file_protection_value = to_string(false);
                    row.metadata_file_path_value = data.m_metaDataFilePath;
                    row.metadata_json_value = data.m_parsedMetadata.toStyledString();
                    row.storage_location_value = StreamStorageTypeLocal; // User uploaded files go to local storage

                    if (GET_DB_INSTANCE()->readVideoRecordExactMatchFilePath(row.sensor_id_value, row.filepath_value, row.start_time_value).filepath_value.empty() == false)
                    {
                        LOG(error) << "File already exists in db" << endl;
                        rollbackPostAddFileUpload(data, deviceMngr);
                        out = Json::nullValue;
                        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, "File already exists in db");
                        return VmsErrorCode::VMSInternalError;
                    }

                    if(GET_DB_INSTANCE()->insertRowVideoRecord(row) == 0)
                    {
                        LOG(info) << "Insert into Database for file " << file_name
                                  << " with timestamp : " << convertEpocToHumanTime(row.start_time_value) << endl;
                    }
                    else
                    {
                        LOG(error) << "Error occured while inserting SQL row for file = " << file_name << endl;
                    }
                    out["metadata"] = metadataResponse;
                }
                else
                {
                    LOG(warning) << "Failed to process upload metadata, but continuing with file upload" << endl;
                }
            }
        }
    }

    if(result != VmsErrorCode::NoError)
    {
        LOG(warning) << result << endl;
        SET_VMS_ERROR(result, out)
        return result;
    }
    if(data.m_isFileReceived && !data.m_isFileSaved)
    {
        DeviceConfig config =  GET_CONFIG();
        std::string nvStreamerDirectory = config.nv_streamer_directory_path;
        size_t availableSpace = getAvailableSpace(nvStreamerDirectory);
        if(data.m_fileSize != 0 && (availableSpace < data.m_fileSize))
        {
            out = Json::nullValue;
            string error_message = string("insufficient space available");
            LOG(error) << error_message << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInsufficientStorage, out, error_message.c_str());
            return VmsErrorCode::VMSInsufficientStorage;
        }
        else
        {
            //case when user reloads browser in between of upload or file wasn't saved properly
            out = Json::nullValue;
            string error_message = string("Internal error - file not saved properly");
            LOG(error) << error_message << endl;

            // Clean up based on upload type
            if (data.m_isChunkedUpload && data.m_tempDirectory != EMPTY_STRING)
            {
                LOG(info) << "Deleting chunks: " << data.m_tempDirectory << endl;
                deleteDirectory(data.m_tempDirectory);
            }

            if (!data.m_absoluteFilePath.empty())
            {
                deleteFile(data.m_absoluteFilePath);
            }

            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
            return VmsErrorCode::VMSInternalError;
        }
    }
    
    // Build final response
    if(data.m_isLastChunk)
    {
        string path = data.m_mediaFilePath == EMPTY_STRING ? filePath : data.m_mediaFilePath;
        string savedStreamId = out.get("streamId", EMPTY_STRING).asString();
        out = Json::nullValue;
        out["filename"] = getFileName(path);
        out["id"] = streamId;
        out["bytes"] = data.m_fileSize;
        out["created_at"] = getCurrentTimeMS();
        out["sensorId"] = data.m_parsedMetadata.get("sensorId", EMPTY_STRING).asString();
        out["filePath"] = path;
        out["streamId"] = savedStreamId;
    }

    if (data.m_isChunkedUpload)
    {
        out["chunkCount"] = string(chunkNumber);
        out["chunkIdentifier"] = string(chunkIdentifier);
    }

    // Add timestamp for PUT uploads
    if (isPutUpload)
    {
        out["timestamp"] = putTimestamp.empty() ? "0" : putTimestamp;
    }

    return VmsErrorCode::NoError;
}


// Write uploaded file data from HTTP connection to disk
VmsErrorCode writeUploadedFileToDisk(struct mg_connection* conn,
                                   const std::string& filePath,
                                   long long contentLength,
                                   Json::Value& out,
                                   bool exclusiveCreation)
{
    int fd = -1;
    
    // Open file with appropriate flags
    if (exclusiveCreation)
    {
        // Atomic file creation: fails if file already exists
        // This eliminates the TOCTOU race condition
        // Permissions: 0644 (rw-r--r--) - standard for uploaded media files
        fd = open(filePath.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0644);
        if (fd == -1)
        {
            if (errno == EEXIST)
            {
                // File already exists - must drain connection to avoid 502
                out = Json::nullValue;
                string error_message = string("File already exists (concurrent upload detected): ") + filePath;
                LOG(error) << error_message << endl;
                
                // Drain the connection to maintain HTTP protocol correctness
                char dummyBuffer[8192];
                long long totalDrained = 0;
                while (totalDrained < contentLength)
                {
                    int toRead = static_cast<int>(std::min(contentLength - totalDrained, 
                                                           static_cast<long long>(sizeof(dummyBuffer))));
                    int readBytes = mg_read(conn, dummyBuffer, toRead);
                    if (readBytes <= 0) break;
                    totalDrained += readBytes;
                }
                
                SET_VMS_ERROR2(VmsErrorCode::ResourceConflictError, out, error_message.c_str());
                return VmsErrorCode::ResourceConflictError;
            }
            
            // Other open errors
            out = Json::nullValue;
            string error_message = string("Failed to open output file: ") + strerror(errno);
            LOG(error) << error_message << ": " << filePath << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
            return VmsErrorCode::VMSInternalError;
        }
    }
    else
    {
        // Legacy behavior: overwrite if exists
        // Permissions: 0644 (rw-r--r--) - standard for uploaded media files
        fd = open(filePath.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd == -1)
        {
            out = Json::nullValue;
            string error_message = string("Failed to open output file: ") + strerror(errno);
            LOG(error) << error_message << ": " << filePath << endl;
            SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
            return VmsErrorCode::VMSInternalError;
        }
    }

    // Read and write data using low-level I/O for consistency
    long long totalRead = 0;
    char buffer[8192];
    
    while (totalRead < contentLength)
    {
        int toRead = static_cast<int>(std::min(contentLength - totalRead, static_cast<long long>(sizeof(buffer))));
        int readBytes = mg_read(conn, buffer, toRead);
        if (readBytes <= 0)
        {
            break;
        }
        
        // Handle partial writes
        const char* ptr = buffer;
        int remaining = readBytes;
        while (remaining > 0)
        {
            ssize_t written = write(fd, ptr, remaining);
            if (written < 0)
            {
                if (errno == EINTR) continue;
                
                out = Json::nullValue;
                string error_message = string("Failed writing to disk: ") + strerror(errno);
                LOG(error) << error_message << ": " << filePath << endl;
                close(fd);
                deleteFile(filePath);
                SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
                return VmsErrorCode::VMSInternalError;
            }
            ptr += written;
            remaining -= written;
        }
        totalRead += readBytes;
    }
    
    // Always close FD before error handling (prevents double close and FD leak)
    close(fd);

    if (totalRead != contentLength)
    {
        out = Json::nullValue;
        string error_message = string("Incomplete request body received for upload");
        LOG(error) << error_message << " expected: " << contentLength << " received: " << totalRead << endl;
        deleteFile(filePath);
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    if (!isFileExist(filePath))
    {
        out = Json::nullValue;
        string error_message = string("Uploaded file not found after write");
        LOG(error) << error_message << ": " << filePath << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
        return VmsErrorCode::VMSInternalError;
    }

    return VmsErrorCode::NoError;
}

/**
 * Check if file exists with extension awareness
 * For files without extensions, also checks for configured media container extensions
 */
bool fileExistsWithExtensions(const std::string& path, const std::string& originalFilename)
{
    if (isFileExist(path))
    {
        return true;
    }
    // If base filename has no extension, also check for files with configured extensions
    if (getFileExtension(originalFilename).empty())
    {
        DeviceConfig config = GET_CONFIG();
        for (const auto& ext : config.media_containers)
        {
            std::string extWithDot = ext;
            if (extWithDot.front() != '.')
            {
                extWithDot = "." + extWithDot;
            }
            if (isFileExist(path + extWithDot))
            {
                return true;
            }
        }
    }
    return false;
}

/**
 * Generate unique filename with counter if needed
 * Handles both files with and without extensions properly
 */
std::string generateUniqueFileName(const std::string& filename, const std::string& fileLocation)
{
    std::string uniqueFilename = filename;
    std::string testPath = fileLocation + "/" + uniqueFilename;
    int counter = 1;
    while (fileExistsWithExtensions(testPath, filename))
    {
        if (getFileExtension(filename).empty())
        {
            // For files without extension, append counter to base name
            uniqueFilename = filename + "_" + std::to_string(counter);
        }
        else
        {
            // For files with extension, insert counter before extension
            std::string stem = getFileName(filename);  // filename without extension
            std::string ext = getFileExtension(filename);  // extension with dot
            uniqueFilename = stem + "_" + std::to_string(counter) + ext;
        }
        testPath = fileLocation + "/" + uniqueFilename;
        counter++;
    }
    return uniqueFilename;
}

// Validate common upload parameters
VmsErrorCode validateUploadParameters(std::shared_ptr<DeviceManager> deviceMngr,
                                    const std::string& filename,
                                    Json::Value& out)
{
    if (!deviceMngr)
    {
        out = Json::nullValue;
        string error_message = string("Device manager is not available");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, error_message.c_str());
        return VmsErrorCode::VMSInternalError;
    }

    if (filename.empty())
    {
        out = Json::nullValue;
        string error_message = string("Filename is not provided in URL");
        LOG(error) << error_message << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    // Reject control characters (tab, newline, CR, etc.) but allow the
    // ordinary space character. Civetweb decodes '+' in URL paths to space,
    // so filenames such as 'a+b.mp4' arrive here as 'a b.mp4' — that's a
    // legitimate filename on every supported filesystem and should not be
    // rejected. We still want to block characters that can confuse the
    // request-parsing or logging pipeline (\t, \n, \r, etc.).
    auto hasControlChar = [](const std::string& s) {
        for (unsigned char ch : s)
        {
            // Reject all ASCII control chars below SPACE and DEL.
            if (ch < 0x20 || ch == 0x7F)
            {
                return true;
            }
        }
        return false;
    };
    if (hasControlChar(filename))
    {
        out = Json::nullValue;
        string error_message = string("Control characters not allowed in file name");
        LOG(error) << error_message << " for file: " << filename << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    // Reject filenames that look like a path rather than a single component.
    // The storage layer never wants to interpret a slash, backslash, or a
    // parent-directory traversal segment from the URL path. Reject these
    // explicitly so a payload like 'a/../b', '../../etc/passwd', or
    // '/etc/hosts' cannot escape the storage root regardless of how
    // downstream code normalises paths.
    if (filename.find('/') != string::npos ||
        filename.find('\\') != string::npos ||
        filename.find("..") != string::npos ||
        (!filename.empty() && filename.front() == '.'))
    {
        out = Json::nullValue;
        string error_message =
            string("Invalid characters in filename — '/', '\\', '..' and "
                   "leading '.' are not allowed");
        LOG(error) << error_message << " for file: " << filename << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    if (checkFileNameLength(filename.c_str()))
    {
        out = Json::nullValue;
        string error_message = string("File name is too long");
        LOG(error) << error_message << " for file: " << filename << endl;
        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
        return VmsErrorCode::InvalidParameterError;
    }

    return VmsErrorCode::NoError;
}

VmsErrorCode deleteFile(std::shared_ptr<DeviceManager> deviceMngr, const Json::Value &req_info, const Json::Value &in, Json::Value &out)
{
    VmsErrorCode ret = VmsErrorCode::NoError;
    string error_message;
    const string queryString = req_info.get("query", EMPTY_STRING).asString();
    const string requestAPI = req_info.get("url", EMPTY_STRING).asString();
    const string requestMethod = req_info.get("method", UNKNOWN_STRING).asString();
    string path = EMPTY_STRING;
    string streamId = EMPTY_STRING;

    string result;
    SensorManagement* sensorMgmt = GET_SENSOR_MNGT();
    if (sensorMgmt == nullptr)
    {
        SET_VMS_ERROR2(VmsErrorCode::VMSInternalError, out, result.c_str())
        LOG(error) << result << endl;
        return VmsErrorCode::VMSInternalError;
    }

    if (requestAPI.empty() || requestMethod == UNKNOWN_STRING)
    {
        LOG(error) << "Malformed HTTP request" << endl;
        SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, out)
        return VmsErrorCode::InvalidParameterError;
    }
    // backward compatibility
    const string fileAPI(STORAGE_FILE_API);
    if (requestAPI.size() >= fileAPI.size())
    {
        path = requestAPI.substr(fileAPI.size() - 1);
        LOG(verbose) <<"Storage management API path: " << path << std::endl;
        vector<string> pathArray = splitString(path, "/");
        streamId = pathArray[0];
    }
    // if streamId is not present in path, try getting it from query parameter
    if (queryString.empty() == false && streamId.empty())
    {
        LOG(info) << "StreamId not found, trying to get from query param" << endl;
        if (CivetServer::getParam(queryString, "streamId", streamId) == false)
        {
            error_message = string("File Id is not found");
            ret = VmsErrorCode::VMSInternalError;
        }
    }
    LOG(info) << "File Delete for streamId: " << streamId << endl;

    auto dbHelper = GET_DB_INSTANCE();
    shared_ptr<SensorInfo> sensor  = dbHelper->searchSensorAndGetSensorInfo(streamId, deviceMngr->getDeviceId());
    if (sensor)
    {
        if(sensor->streams.size() > 0)
        {
            shared_ptr<StreamInfo> stream = sensor->streams[0];
            string path = getFilePathFromUrl(stream->live_url, NV_STREAMER);
            int ret = 0;
#ifdef SENSOR_MODULE
            ret = sensorMgmt->deleteSensor(streamId);
#endif
            if (ret == 0)
            {
                if (deleteFile(path) == false)
                {
                    error_message = string("Failed to delete file");
                    ret = VmsErrorCode::VMSInternalError;
                }
            }
            else
            {
                error_message = string("File Id is not found");
                ret = VmsErrorCode::VMSInternalError;
            }
        }
        else
        {
            error_message = string("Failed find the stream object of the file");
            ret = VmsErrorCode::VMSInternalError;
        }
    }
    else
    {
        error_message = string("Failed find the stream object of the file");
        ret = VmsErrorCode::VMSInternalError;
    }
    if(ret != VmsErrorCode::NoError)
    {
        out = Json::nullValue;
        LOG(error) << error_message << ": " << streamId << endl;
        SET_VMS_ERROR2(ret, out, error_message.c_str());
    }
    return ret;
}

// Single comprehensive helper function for all common video file processing
nv_vms::VmsErrorCode prepareVideoFileProcessing(
    const string& user_start_time,
    const string& user_end_time,
    const string& sensor_id,
    const string& id,
    const string& sensor_type,
    const string& full_length,
    string& output_file,
    VideoFileProcessingParams& params)
{
    LOG(info) << "prepareVideoFileProcessing - Start Time: " << user_start_time << " End Time: " << user_end_time << endl;

    // 1. Convert string times to epoch timestamps in milliseconds or handle relative offsets
    bool use_relative_ms_offsets = false;
    if (!user_start_time.empty())
    {
        // Check if this is a millisecond value (prefixed with "ms:")
        if (user_start_time.length() > 3 && user_start_time.substr(0, 3) == "ms:")
        {
            // These are relative millisecond offsets from file start, not absolute timestamps
            params.relative_start_ms = std::stoll(user_start_time.substr(3));
            params.use_millisecond_precision = true;
            use_relative_ms_offsets = true;
            LOG(info) << "Relative millisecond offset Start Time: " << params.relative_start_ms << "ms from file start" << endl;
        }
        else
        {
            params.epoch_user_start_time = getEpocTimeInMS(user_start_time);
            LOG(info) << "Absolute Epoch Start Time: " << params.epoch_user_start_time << endl;
        }
    }
    if (!user_end_time.empty())
    {
        // Check if this is a millisecond value (prefixed with "ms:")
        if (user_end_time.length() > 3 && user_end_time.substr(0, 3) == "ms:")
        {
            // These are relative millisecond offsets from file start, not absolute timestamps
            params.relative_end_ms = std::stoll(user_end_time.substr(3));
            params.use_millisecond_precision = true;
            use_relative_ms_offsets = true;
            LOG(info) << "Relative millisecond offset End Time: " << params.relative_end_ms << "ms from file start" << endl;
        }
        else
        {
            params.epoch_user_end_time = getEpocTimeInMS(user_end_time);
            LOG(info) << "Absolute Epoch End Time: " << params.epoch_user_end_time << endl;
        }
    }

    // 2. Get configuration parameters
    params.max_download_size = GET_CONFIG().max_video_download_size_MB;
    params.get_accurate = full_length == "true" ? true : false;
    auto dbHelper = GET_DB_INSTANCE();

    // 3. Get file list from database
    // Determine effective sensor type (for disconnected sensors, detect from metadata)
    string disconnected_sensor_type = sensor_type;

    if (sensor_type.empty())
    {
        // Disconnected sensor - first try to get files using getFileList
        LOG(info) << "Disconnected sensor detected, attempting to retrieve files for streamId: " << sensor_id << endl;
        params.fileNameArray = dbHelper->getFileList(
            sensor_id,
            params.epoch_user_start_time,
            params.epoch_user_end_time,
            params.max_download_size,
            params.get_accurate
        );

        // Check if any file has metadata_json - indicates sensor_file type
        for (const auto& file : params.fileNameArray)
        {
            if (!file.m_metadataJson.empty())
            {
                LOG(info) << "Detected sensor_file type from metadata_json for disconnected sensor" << endl;
                disconnected_sensor_type = SENSOR_TYPE_FILE;
                break;
            }
        }

        // If sensor_file type detected, re-fetch using the specialized method
        if (disconnected_sensor_type == std::string(SENSOR_TYPE_FILE))
        {
            params.fileNameArray = dbHelper->getFileListUniqueIdSensorIdBased(
                id,
                sensor_id,
                params.epoch_user_start_time,
                params.epoch_user_end_time
            );
        }
        // else: already have files from getFileList, use them
    }
    else if (sensor_type == std::string(SENSOR_TYPE_FILE))
    {
        params.fileNameArray = dbHelper->getFileListUniqueIdSensorIdBased(
            id,
            sensor_id,
            params.epoch_user_start_time,
            params.epoch_user_end_time
        );
    }
    else
    {
        params.fileNameArray = dbHelper->getFileList(
            sensor_id,
            params.epoch_user_start_time,
            params.epoch_user_end_time,
            params.max_download_size,
            params.get_accurate
        );
    }

    if (params.fileNameArray.size() == 0)
    {
        LOG(error) << "No streams found for the given timestamps" << endl;
        return nv_vms::VmsErrorCode::VMSNoDataError;
    }

    // 4. Process the first file
    params.input_file_path = params.fileNameArray[0].m_filePath;
    params.file_start_time = params.fileNameArray[0].m_startTime;

    LOG(info) << "Using input file: " << params.input_file_path << endl;
    LOG(info) << "File start time: " << params.file_start_time << endl;

    // 5. Calculate relative start and end times
    if (use_relative_ms_offsets)
    {
        // Direct relative millisecond offsets from file start - no complex calculations needed
        params.relative_start_sec = params.relative_start_ms / 1000;
        params.relative_end_sec = params.relative_end_ms / 1000;
        params.seek_start_pos = params.relative_start_ms;
        params.seek_end_pos = params.relative_end_ms;

        LOG(info) << "Using direct relative offsets - start_ms: " << params.relative_start_ms
                  << ", end_ms: " << params.relative_end_ms << endl;
    }
    else if (params.epoch_user_start_time == 0 && params.epoch_user_end_time == 0)
    {
        LOG(warning) << "Both start and end time not provided via epoch, using string conversion with defaults" << endl;

        params.relative_start_sec = stringToInt(user_start_time, 0);
        params.relative_end_sec = stringToInt(user_end_time, INT_MAX);
        params.seek_start_pos = params.relative_start_sec * 1000;
        params.seek_end_pos = (params.relative_end_sec == INT_MAX) ? std::numeric_limits<int64_t>::max() : params.relative_end_sec * 1000;
        params.relative_start_ms = params.relative_start_sec * 1000;
        params.relative_end_ms = (params.relative_end_sec == INT_MAX) ? std::numeric_limits<int64_t>::max() : params.relative_end_sec * 1000;
    }
    else if (params.epoch_user_start_time == 0)
    {
        LOG(info) << "Start time not provided, using 0. End time provided: " << params.epoch_user_end_time << endl;
        // Start time not provided, end time provided => start time should be 0
        params.epoch_user_start_time = 0;
        params.seek_start_pos = 0;
        params.seek_end_pos = params.epoch_user_end_time - params.file_start_time;
        params.relative_start_ms = 0;
        params.relative_end_ms = params.epoch_user_end_time - params.file_start_time;
        params.relative_start_sec = 0;
        params.relative_end_sec = params.relative_end_ms / 1000;
        params.use_millisecond_precision = true;
    }
    else if (params.epoch_user_end_time == 0)
    {
        LOG(info) << "End time not provided, using max. Start time provided: " << params.epoch_user_start_time << endl;
        // End time not provided, start time provided => end time should be max
        params.epoch_user_end_time = std::numeric_limits<int64_t>::max();
        if (params.epoch_user_start_time < (int64_t)params.file_start_time)
        {
            LOG(warning) << "User Start time is less than file start time, setting User Start time = 0" << endl;
            params.seek_start_pos = 0;
        }
        else
        {
            params.seek_start_pos = params.epoch_user_start_time - params.file_start_time;
        }
        params.seek_end_pos = std::numeric_limits<int64_t>::max();
        params.relative_start_ms = params.epoch_user_start_time - params.file_start_time;
        params.relative_end_ms = std::numeric_limits<int64_t>::max();
        params.relative_start_sec = params.relative_start_ms / 1000;
        params.relative_end_sec = INT_MAX;
        params.use_millisecond_precision = true;
    }
    else
    {
        // Calculate seek positions for video processing (in milliseconds)
        if (params.epoch_user_start_time < params.epoch_user_end_time || params.epoch_user_end_time == 0)
        {
            if (params.epoch_user_start_time)
            {
                if (params.epoch_user_start_time < (int64_t)params.file_start_time)
                {
                    LOG(warning) << "User Start time is less than file start time, setting User Start time = 0" << endl;
                    params.seek_start_pos = 0;
                }
                else
                {
                    params.seek_start_pos = params.epoch_user_start_time - params.file_start_time;
                }
            }
            else
            {
                params.seek_start_pos = 0;
            }

            if (params.epoch_user_end_time)
            {
                int64_t duration = params.epoch_user_end_time - (params.epoch_user_start_time == 0 ? params.file_start_time : params.epoch_user_start_time);
                params.seek_end_pos = duration + params.seek_start_pos;
            }
        }

        // Calculate relative times - preserve millisecond precision
        params.relative_start_ms = params.epoch_user_start_time - params.file_start_time;
        params.relative_end_ms = params.epoch_user_end_time - params.file_start_time;

        // Also calculate seconds for backward compatibility
        params.relative_start_sec = params.relative_start_ms / 1000;
        params.relative_end_sec = params.relative_end_ms / 1000;

        // Ensure we don't have negative start times
        if (params.relative_start_ms < 0) {
            params.relative_start_ms = 0;
            params.relative_start_sec = 0;
        }
        if (params.relative_end_ms < 0) {
            params.relative_end_ms = 0;
            params.relative_end_sec = 0;
        }

        // Flag that we have millisecond precision available
        params.use_millisecond_precision = true;
        LOG(info) << "Calculated relative times from epochs - start_ms: " << params.relative_start_ms << ", end_ms: " << params.relative_end_ms << endl;
    }

    // Disable giosrc for growing file when VST device and stream has B-frames (avoids decodebin/typefind "not enough data" errors)
    if (!params.fileNameArray.empty() && dbHelper)
    {
        StorageManagement* storageMgmt = GET_STORAGE_MNGT();
        if (storageMgmt)
        {
            string deviceType = storageMgmt->getDeviceTypeName();
            string bframesStr = dbHelper->readStreamProperty(sensor_id, SensorStreamsDBColumns::isBframesPresent);
            bool hasBframes = (bframesStr == "1" || iequals(bframesStr, "true"));
            if (deviceType == TYPE_VST && hasBframes)
            {
                params.disable_giosrc_for_growing_file = true;
                params.has_bframes = true;
                LOG(info) << "B-frames: disable_giosrc_for_growing_file=true, has_bframes=true" << endl;
            }
        }
    }

    // Note: Output filename generation moved to respective functions

    LOG(info) << "Total files: " << params.fileNameArray.size() << endl;
    LOG(info) << "Processed parameters - seek_start_pos: " << params.seek_start_pos
              << ", seek_end_pos: " << params.seek_end_pos
              << ", relative_start_sec: " << params.relative_start_sec
              << ", relative_end_sec: " << params.relative_end_sec << endl;

    return nv_vms::VmsErrorCode::NoError;
}

// Internal function that uses preprocessed parameters
nv_vms::VmsErrorCode extractVideoSegmentWithLibAV_Internal(
    const VideoFileProcessingParams& params,
    string& output_file,
    string& video_codec,
    const string& user_start_time,
    const string& user_end_time)
{
    LOG(info) << "extractVideoSegmentWithLibAV_Internal - Using VideoSegmentExtractor for segment extraction" << endl;

    // Generate output filename if not provided
    if (output_file.empty())
    {
        string sourceFilePath = params.input_file_path;

        // Parse from right: get extension (from rightmost dot)
        size_t lastDot = sourceFilePath.find_last_of('.');
        string containerExtension = (lastDot != string::npos) ? sourceFilePath.substr(lastDot) : ".mp4";

        // Parse from right: get filename without extension (between last slash and last dot)
        size_t lastSlash = sourceFilePath.find_last_of('/');
        size_t filenameStart = (lastSlash != string::npos) ? lastSlash + 1 : 0;
        size_t filenameEnd = (lastDot != string::npos) ? lastDot : sourceFilePath.length();
        string originalFileName = sourceFilePath.substr(filenameStart, filenameEnd - filenameStart);

        // Generate filename: segment_<original_file_name>_<complete_start_timestamp>_<complete_end_timestamp>.<original_container_type>
        // Replace invalid filename characters in timestamps
        string sanitizedStartTime = user_start_time;
        string sanitizedEndTime = user_end_time;

        // Replace characters that are invalid in filenames
        auto sanitize = [](string& str) {
            for (char& c : str) {
                if (c == ':' || c == '.' || c == 'T' || c == 'Z') {
                    c = '_';
                }
            }
        };

        sanitize(sanitizedStartTime);
        sanitize(sanitizedEndTime);

        output_file = "/tmp/segment_" + originalFileName + "_" +
            sanitizedStartTime + "_" + sanitizedEndTime + containerExtension;

        LOG(info) << "Generated output filename: " << output_file << endl;
    }

    // Try to use dynamic VideoSegmentExtractor
    static DynamicVideoSegmentExtractor extractor;
    static bool initialized = false;

    if (!initialized) {
        initialized = true;
        if (!extractor.initialize()) {
            LOG(info) << "VideoSegmentExtractor not available, will fall back to downloadVideoFile_Internal" << endl;
            return nv_vms::VmsErrorCode::VMSInternalError;
        }
    }

    if (!extractor.isAvailable()) {
        LOG(info) << "VideoSegmentExtractor not available, will fall back to downloadVideoFile_Internal" << endl;
        return nv_vms::VmsErrorCode::VMSInternalError;
    }

    // Convert to PTS (libav time base) with millisecond precision when available
    int64_t startPts, endPts;
    if (params.use_millisecond_precision)
    {
        // Use millisecond precision: convert ms to PTS (AV_TIME_BASE = 1000000)
        startPts = (params.relative_start_ms * AV_TIME_BASE) / 1000;

        // Handle max value case to avoid overflow
        if (params.relative_end_ms == std::numeric_limits<int64_t>::max())
        {
            endPts = std::numeric_limits<int64_t>::max();
            LOG(info) << "End time is max value, using max PTS" << endl;
        }
        else
        {
            endPts = (params.relative_end_ms * AV_TIME_BASE) / 1000;
        }

        LOG(info) << "Using millisecond precision - start_ms: " << params.relative_start_ms
                  << ", end_ms: " << params.relative_end_ms << endl;
        LOG(info) << "Calculated PTS - startPts: " << startPts << ", endPts: " << endPts << endl;
    }
    else
    {
        // Fallback to second precision (AV_TIME_BASE = 1000000)
        startPts = params.relative_start_sec * AV_TIME_BASE;

        // Handle max value case to avoid overflow
        if (params.relative_end_sec == INT_MAX)
        {
            endPts = std::numeric_limits<int64_t>::max();
            LOG(info) << "End time is max value, using max PTS" << endl;
        }
        else
        {
            endPts = params.relative_end_sec * AV_TIME_BASE;
        }

        LOG(info) << "Using second precision - start_sec: " << params.relative_start_sec
                  << ", end_sec: " << params.relative_end_sec << endl;
    }

    LOG(info) << "VideoSegmentExtractor - Extracting segment from " << params.input_file_path
              << " to " << output_file << endl;

    bool success = extractor.extractSegmentStreamCopy(params.input_file_path, output_file, startPts, endPts);

    if (success)
    {
        LOG(info) << "VideoSegmentExtractor - Segment extraction successful" << endl;

        // Set video codec based on source file (assume H.264 for now)
        video_codec = "h264";

        return nv_vms::VmsErrorCode::NoError;
    }
    else
    {
        LOG(error) << "VideoSegmentExtractor - Segment extraction failed: "
                   << extractor.getLastError() << endl;
        return nv_vms::VmsErrorCode::VMSInternalError;
    }
}



nv_vms::VmsErrorCode makeVideoFile (string user_start_time, string user_end_time,
                                    string sensor_id, string id, string device_name,
                                    string& output_file, string& video_codec,
                                    string full_length, string sensor_type, string container,
                                    string transcode, string disable_audio,
                                    string enable_overlay, OverlayBBoxParams *ol_params,
                                    string frameRate,
                                    nv_vms::IMediaInterface* media_interface,
                                    string uselibav, bool isCloudStream,
                                    int64_t* actual_start_epoch_ms)
{
    LOG(info) << "Enter makeVideoFile" << endl;
    bool do_seek = false;

    // Common preprocessing for all video file operations
    VideoFileProcessingParams params;
    VmsErrorCode ret = prepareVideoFileProcessing(user_start_time, user_end_time, sensor_id, id, sensor_type, full_length, output_file, params);
    if (ret != nv_vms::VmsErrorCode::NoError)
    {
        LOG(error) << "Common preprocessing failed" << endl;
        if (sensor_type != SENSOR_TYPE_MMS_ONVIF)
        {
            return ret;
        }
    }

    params.is_cloud_stream = isCloudStream;
    if ((uselibav == "true" || uselibav == "1") && enable_overlay == "false" && params.fileNameArray.size() == 1)
    {
        LOG(info) << "Using VideoSegmentExtractor (overlay disabled)" << endl;
        ret = extractVideoSegmentWithLibAV_Internal(params, output_file, video_codec, user_start_time, user_end_time);

        // Fallback to downloadVideoFile_Internal if VideoSegmentExtractor fails
        if (ret != nv_vms::VmsErrorCode::NoError)
        {
            LOG(warning) << "VideoSegmentExtractor failed, falling back to downloadVideoFile" << endl;
            ret = downloadVideoFile(params, output_file, video_codec, container,
                                    transcode, do_seek, disable_audio, enable_overlay,
                                    ol_params, user_start_time, user_end_time, device_name,
                                    sensor_id, sensor_type, frameRate, media_interface, actual_start_epoch_ms);
        }
    }
    else
    {
        /*
        * If overlay is enabled, we need to transcode the video to full.
        * This is because the overlay is not supported in GOP transcode.
        */
        if (enable_overlay == "true" || enable_overlay == "1")
        {
            transcode = "full";
        }
        LOG(info) << "Using downloadVideoFile (overlay enabled or default path)" << endl;
        ret = downloadVideoFile(params, output_file, video_codec, container,
                                    transcode, do_seek, disable_audio, enable_overlay,
                                    ol_params, user_start_time, user_end_time, device_name,
                                    sensor_id, sensor_type, frameRate, media_interface, actual_start_epoch_ms);
    }
    cleanupDownloadedFiles(params.remoteLocalPairs, GET_CONFIG().enable_cloud_storage);
    if (ret != nv_vms::VmsErrorCode::NoError)
    {
        LOG(error) << "Video file processing failed" << endl;
    }

    LOG(info) << "Exit makeVideoFile" << endl;
    return ret;
}

void cleanupDownloadedFiles(const std::vector<std::pair<std::string, std::string>>& remoteLocalPairs, bool enable_cloud_storage)
{
    if (enable_cloud_storage && !remoteLocalPairs.empty())
    {
        LOG(info) << "Cleaning up " << remoteLocalPairs.size() << " downloaded cloud files using unified storage manager..." << endl;

        // Initialize unified storage manager for file deletion
        auto unifiedStorageManager = nv_vms::UnifiedStorageManagerUtils::initializeStorageManager(GET_CONFIG());
        if (unifiedStorageManager)
        {
            for (const auto& pair : remoteLocalPairs)
            {
                const std::string& localPath = pair.second;
                LOG(verbose) << "Deleting downloaded file: " << localPath << endl;

                nv_vms::DeleteResult deleteResult = unifiedStorageManager->deleteFile(localPath);
                if (deleteResult.success)
                {
                    LOG(info) << "Successfully deleted downloaded file: " << localPath
                              << " (" << deleteResult.deletedSize << " bytes)" << endl;
                }
                else
                {
                    LOG(warning) << "Failed to delete downloaded file: " << localPath
                                << " - " << deleteResult.message << endl;
                }
            }
            LOG(info) << "Cloud file cleanup completed using unified storage manager" << endl;
        }
        else
        {
            LOG(warning) << "Failed to initialize unified storage manager, falling back to filesystem cleanup" << endl;

            // Fallback to original filesystem-based cleanup
            for (const auto& pair : remoteLocalPairs)
            {
                const std::string& localPath = pair.second;
                try
                {
                    if (std::filesystem::exists(localPath))
                    {
                        std::filesystem::remove(localPath);
                        LOG(info) << "Successfully deleted downloaded file (fallback): " << localPath << endl;
                    }
                    else
                    {
                        LOG(warning) << "Downloaded file does not exist for cleanup: " << localPath << endl;
                    }
                }
                catch (const std::exception& e)
                {
                    LOG(warning) << "Failed to delete downloaded file: " << localPath << " Error: " << e.what() << endl;
                }
            }
        }
    }
}

std::string generateTempVideoFilePath(const std::string& webRootPath, const std::string& baseFileName, const std::string& startTime)
{
    // Create temp_videos directory
    std::string tempVideoDir = webRootPath + TEMP_STORAGE_DIR;

    // Convert to absolute path using realpath to resolve any relative components
    std::string absTempVideoDir = getAbsolutePath(tempVideoDir);

    // Create directory if it doesn't exist
    if (!isDirExist(absTempVideoDir))
    {
        bool dirCreated = createDir(absTempVideoDir);
        LOG(info) << "Creating temp video directory: " << absTempVideoDir << " - " << (dirCreated ? "success" : "failed") << std::endl;
        if (!dirCreated)
        {
            LOG(error) << "Failed to create temp video directory: " << absTempVideoDir << std::endl;
            return "";
        }
    }

    // Generate unique filename with start time timestamp (same pattern as picture URLs)
    std::string sanitizedTimestamp = sanitizeTimestampForFilename(startTime);
    std::string tempFileName = absTempVideoDir + "/" + baseFileName + "_" + sanitizedTimestamp + ".mp4";
    return tempFileName;
}

VmsErrorCode recordTempFileForCleanup(const std::string& filePath, const std::string& streamId,
                                     const std::string& deviceId, int64_t expiryTimestamp)
{
    auto dbHelper = GET_DB_INSTANCE();
    if (!dbHelper)
    {
        LOG(warning) << "Database not available to record temp file for cleanup" << std::endl;
        return VmsErrorCode::VMSInternalError;
    }

    // Get file size
    const size_t fileSize = getFileSizeInBytes(filePath);
    if (fileSize == 0)
    {
        LOG(warning) << "Cannot record temp file with zero size: " << filePath << std::endl;
        return VmsErrorCode::VMSInternalError;
    }

    auto now = std::chrono::system_clock::now();
    auto timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();

    nv_vms::TempFilesDBColumns tempFileRecord;
    tempFileRecord.device_id_value = deviceId;
    tempFileRecord.file_path_value = filePath;
    tempFileRecord.expiry_timestamp_value = expiryTimestamp;
    tempFileRecord.created_timestamp_value = timestamp;
    tempFileRecord.stream_id_value = streamId;
    tempFileRecord.file_size_value = fileSize;

    int result = dbHelper->insertTempFileRecord(tempFileRecord);
    if (result == 0)
    {
        LOG(info) << "Recorded temp file for cleanup: " << filePath << " expires at: " << expiryTimestamp << std::endl;
        return VmsErrorCode::NoError;
    }
    else
    {
        LOG(warning) << "Failed to record temp file in database: " << filePath << std::endl;
        return VmsErrorCode::VMSInternalError;
    }
}

Json::Value convertCloudListResultToJson(const nv_vms::CloudListResult& result)
{
    Json::Value json;
    json["success"] = result.success;
    json["message"] = result.message;
    json["bucket"] = result.bucket;
    json["prefix"] = result.prefix;
    json["count"] = result.count;
    json["totalSize"] = static_cast<Json::UInt64>(result.totalSize);
    json["isTruncated"] = result.isTruncated;
    json["nextMarker"] = result.nextMarker;

    if (!result.errorCode.empty())
    {
        json["errorCode"] = result.errorCode;
    }

    Json::Value files = Json::arrayValue;
    for (const auto& object : result.objects)
    {
        Json::Value fileJson;
        fileJson["key"] = object.key;
        fileJson["etag"] = object.etag;
        fileJson["size"] = std::to_string(object.size);
        fileJson["lastModified"] = object.lastModified;
        fileJson["storageClass"] = object.storageClass;

        if (!object.metadata.empty())
        {
            Json::Value metadata;
            for (const auto& pair : object.metadata)
            {
                metadata[pair.first] = pair.second;
            }
            fileJson["metadata"] = metadata;
        }

        files.append(fileJson);
    }
    json["files"] = files;

    return json;
}

