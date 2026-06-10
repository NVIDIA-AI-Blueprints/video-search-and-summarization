/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "s3_cloud_reader.h"
#include "common/aws_sdk_manager.h"
#include "logger.h"
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <sstream>

// AWS SDK includes
#include <aws/core/Aws.h>
#include <aws/core/http/URI.h>
#include <aws/core/platform/Environment.h>
#include <aws/core/utils/Outcome.h>
#include <aws/core/utils/crypto/Factories.h>

namespace nv_vms
{

// Default S3 endpoints by region
const std::map<std::string, std::string, std::less<>> S3CloudReader::DEFAULT_ENDPOINTS = {
    {"us-east-1", "s3.amazonaws.com"},
    {"us-east-2", "s3.us-east-2.amazonaws.com"},
    {"us-west-1", "s3.us-west-1.amazonaws.com"},
    {"us-west-2", "s3.us-west-2.amazonaws.com"},
    {"eu-west-1", "s3.eu-west-1.amazonaws.com"},
    {"eu-central-1", "s3.eu-central-1.amazonaws.com"},
    {"ap-southeast-1", "s3.ap-southeast-1.amazonaws.com"},
    {"ap-northeast-1", "s3.ap-northeast-1.amazonaws.com"}};

S3CloudReader::S3CloudReader() : m_initialized(false), m_awsSdkInitialized(false)
{
    initializeAwsSDK();
}

S3CloudReader::~S3CloudReader()
{
    shutdownAwsSDK();
}

bool S3CloudReader::initializeAwsSDK()
{
    if (m_awsSdkInitialized)
    {
        return true;
    }

    if (AwsSdkManager::getInstance().initializeAwsSDK())
    {
        m_awsSdkInitialized = true;
        return true;
    }

    setLastError("Failed to initialize AWS SDK through global manager");
    return false;
}

void S3CloudReader::shutdownAwsSDK()
{
    if (m_awsSdkInitialized)
    {
        AwsSdkManager::getInstance().shutdownAwsSDK();
        m_awsSdkInitialized = false;
    }
}

bool S3CloudReader::isAvailable() const
{
    std::lock_guard<std::mutex> lock(m_config_mutex);
    return m_initialized && m_awsSdkInitialized && !m_config.accessKeyId.empty() && !m_config.secretAccessKey.empty();
}

bool S3CloudReader::configure(const CloudReaderConfig& config)
{
    std::lock_guard<std::mutex> lock(m_config_mutex);

    m_config = config;
    m_config.storageType = CloudStorageType::AWS_S3;

    // Set default region if not specified
    if (m_config.region.empty())
    {
        m_config.region = "us-west-1";
    }

    // Set endpoint based on configuration
    // Priority: 1) Explicit config.endpoint (for MinIO/custom S3)
    //           2) Default AWS endpoints by region
    if (!m_config.endpoint.empty())
    {
        m_endpoint = m_config.endpoint;
    }
    else
    {
        auto it = DEFAULT_ENDPOINTS.find(m_config.region);
        if (it != DEFAULT_ENDPOINTS.end())
        {
            m_endpoint = std::string(m_config.useSSL ? "https://" : "http://") + it->second;
        }
        else
        {
            m_endpoint =
                std::string(m_config.useSSL ? "https://" : "http://") + "s3." + m_config.region + ".amazonaws.com";
        }
    }

    // Initialize AWS components
    if (!initializeAwsSDK())
    {
        return false;
    }

    // Create credentials provider using thread-safe method
    m_credentialsProvider =
        AwsSdkManager::getInstance().createCredentialsProvider(m_config.accessKeyId, m_config.secretAccessKey);
    if (!m_credentialsProvider)
    {
        setLastError("Failed to create AWS credentials provider");
        return false;
    }

    // Create AWS V4 signer using thread-safe method
    m_signer = AwsSdkManager::getInstance().createV4Signer(
        m_credentialsProvider,
        "s3",
        m_config.region,
        Aws::Client::AWSAuthV4Signer::PayloadSigningPolicy::RequestDependent);
    if (!m_signer)
    {
        setLastError("Failed to create AWS V4 signer");
        return false;
    }

    // Create client configuration using thread-safe method
    // Pass endpoint for MinIO/custom S3 compatibility
    m_clientConfig = AwsSdkManager::getInstance().createClientConfiguration(m_config.region, m_config.useSSL,
                                                                            m_config.timeoutSeconds, m_endpoint);
    if (!m_clientConfig)
    {
        setLastError("Failed to create client configuration");
        return false;
    }

    // Create HTTP client using thread-safe method
    m_httpClient = AwsSdkManager::getInstance().createHttpClient(m_clientConfig);
    if (!m_httpClient)
    {
        setLastError("Failed to create AWS HTTP client");
        return false;
    }

    m_initialized = validateConfig();
    if (!m_initialized)
    {
        setLastError("Invalid configuration provided");
        return false;
    }

    return true;
}

CloudReaderConfig S3CloudReader::getConfiguration() const
{
    std::lock_guard<std::mutex> lock(m_config_mutex);
    return m_config;
}

CloudListResult S3CloudReader::listObjects(const std::string& bucket, const std::string& prefix, uint32_t maxKeys)
{
    return listObjectsPaginated(bucket, prefix, "", maxKeys);
}

CloudListResult S3CloudReader::listAllObjects(const std::string& bucket, const std::string& prefix)
{
    auto start_time = std::chrono::high_resolution_clock::now();
    CloudListResult combinedResult(false);
    combinedResult.bucket = bucket;
    combinedResult.prefix = prefix;

    std::string marker = "";
    uint32_t totalPages = 0;
    const uint32_t maxKeysPerPage = 1000; // S3 maximum

    do
    {
        totalPages++;
        CloudListResult pageResult = listObjectsPaginated(bucket, prefix, marker, maxKeysPerPage);

        if (!pageResult.success)
        {
            combinedResult.message = "Failed at page " + std::to_string(totalPages) + ": " + pageResult.message;
            combinedResult.errorCode = pageResult.errorCode;
            auto end_time = std::chrono::high_resolution_clock::now();
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
            LOG(error) << "listAllObjects failed after " << totalPages << " pages, duration: " << duration.count() << "ms" << std::endl;
            return combinedResult;
        }

        // Append objects from this page
        combinedResult.objects.insert(combinedResult.objects.end(),
                                      pageResult.objects.begin(),
                                      pageResult.objects.end());
        combinedResult.count += pageResult.count;
        combinedResult.totalSize += pageResult.totalSize;

        LOG(info) << "Page " << totalPages << ": fetched " << pageResult.count
                     << " objects, total so far: " << combinedResult.count << std::endl;

        // Check if there are more pages
        if (pageResult.isTruncated && !pageResult.nextMarker.empty())
        {
            marker = pageResult.nextMarker;
        }
        else
        {
            // No more pages
            combinedResult.isTruncated = false;
            combinedResult.nextMarker = "";
            break;
        }

    } while (true);

    combinedResult.success = true;
    combinedResult.message = "Successfully listed all " + std::to_string(combinedResult.count) +
                            " objects in " + std::to_string(totalPages) + " pages";

    auto end_time = std::chrono::high_resolution_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
    LOG(info) << "listAllObjects completed: " << combinedResult.count << " objects from "
              << totalPages << " pages in " << duration.count() << "ms" << std::endl;

    return combinedResult;
}

CloudListResult S3CloudReader::listObjectsPaginated(const std::string& bucket, const std::string& prefix,
                                                    const std::string& marker, uint32_t maxKeys)
{
    auto start_time = std::chrono::high_resolution_clock::now();
    CloudListResult result(false);
    result.bucket = bucket;
    result.prefix = prefix;

    if (!isAvailable())
    {
        result.message = "S3 Cloud Reader not properly configured";
        result.errorCode = "CONFIG_ERROR";
        updateStats(false, std::chrono::milliseconds(0), "CONFIG_ERROR");
        return result;
    }

    try
    {
        // Build query parameters using structured approach for AWS SDK
        // We'll pass them as a map and let makeS3Request build the URI properly
        std::map<std::string, std::string, std::less<>> queryParamsMap;
        queryParamsMap["list-type"] = "2";

        if (!prefix.empty())
        {
            queryParamsMap["prefix"] = prefix;
        }
        if (!marker.empty())
        {
            // Pass continuation token as-is; AWS SDK will encode it properly
            queryParamsMap["continuation-token"] = marker;
            LOG(verbose) << "Using continuation token for page "
                     << "request (will be encoded by AWS SDK): " << marker << std::endl;
        }
        if (maxKeys > 0 && maxKeys != 1000)
        {
            queryParamsMap["max-keys"] = std::to_string(maxKeys);
        }

        // Make S3 request using AWS SDK with structured query parameters
        std::shared_ptr<Aws::Http::HttpResponse> response;
        CloudResult requestResult = makeS3RequestWithParams("GET", bucket, "", queryParamsMap, response);
        if (!requestResult.success)
        {
            result.message = "S3 request failed: " + requestResult.message;
            result.errorCode = requestResult.errorCode;

            auto end_time = std::chrono::high_resolution_clock::now();
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
            updateStats(false, duration, requestResult.errorCode);
            return result;
        }

        // Parse XML response using AWS SDK
        auto& responseStream = response->GetResponseBody();
        std::string xmlContent((std::istreambuf_iterator<char>(responseStream)), std::istreambuf_iterator<char>());

        CloudResult parseResult = parseS3ListResponseWithAwsXml(xmlContent, result);
        if (!parseResult.success)
        {
            result.success = false;
            result.message = "Failed to parse S3 response: " + parseResult.message;
            result.errorCode = "PARSE_ERROR";

            auto end_time = std::chrono::high_resolution_clock::now();
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
            updateStats(false, duration, "PARSE_ERROR");
            return result;
        }

        result.success = true;
        result.message = "Successfully listed " + std::to_string(result.count) + " objects";

        auto end_time = std::chrono::high_resolution_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        updateStats(true, duration);

        // Update stats with objects listed
        {
            std::lock_guard<std::mutex> lock(m_stats_mutex);
            m_stats.objectsListed += result.count;
        }
    }
    catch (const std::exception& e)
    {
        result.message = "Exception during S3 list operation: " + std::string(e.what());
        result.errorCode = "EXCEPTION";

        auto end_time = std::chrono::high_resolution_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        updateStats(false, duration, "EXCEPTION");
    }

    return result;
}

CloudResult S3CloudReader::downloadObject(const std::string& bucket, const std::string& objectKey,
                                          const std::string& localPath)
{
    auto start_time = std::chrono::high_resolution_clock::now();
    CloudResult result(false);

    if (!isAvailable())
    {
        result.message = "S3 Cloud Reader not properly configured";
        result.errorCode = "CONFIG_ERROR";
        updateStats(false, std::chrono::milliseconds(0), "CONFIG_ERROR");
        return result;
    }

    try
    {
        // Download file using AWS SDK
        CloudResult downloadResult = downloadToFileWithAwsClient(bucket, objectKey, localPath);
        if (!downloadResult.success)
        {
            result.message = "Download failed: " + downloadResult.message;
            result.errorCode = downloadResult.errorCode;

            auto end_time = std::chrono::high_resolution_clock::now();
            auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
            updateStats(false, duration, downloadResult.errorCode);
            return result;
        }

        result.success = true;
        result.message = "Successfully downloaded object to " + localPath;

        auto end_time = std::chrono::high_resolution_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        updateStats(true, duration);
    }
    catch (const std::exception& e)
    {
        result.message = "Exception during S3 download: " + std::string(e.what());
        result.errorCode = "EXCEPTION";

        auto end_time = std::chrono::high_resolution_clock::now();
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
        updateStats(false, duration, "EXCEPTION");
    }

    return result;
}

CloudResult S3CloudReader::makeS3RequestWithParams(const std::string& method, const std::string& bucket,
                                                   const std::string& objectKey,
                                                   const std::map<std::string, std::string, std::less<>>& queryParamsMap,
                                                   std::shared_ptr<Aws::Http::HttpResponse>& response)
{
    CloudResult result(false);

    try
    {
        // Use AWS SDK's URI class for secure URL construction
        // This ensures proper encoding and signature compatibility
        // Build S3 endpoint - use custom endpoint if provided (for MinIO compatibility)
        std::string host;
        if (!m_endpoint.empty())
        {
            // Extract host from custom endpoint (e.g., "http://<host>:9000")
            host = m_endpoint;
            // Remove protocol if present
            if (host.find("://") != std::string::npos)
            {
                host = host.substr(host.find("://") + 3);
            }
        }
        else
        {
            // Default to AWS S3 endpoint
            host = "s3." + m_config.region + ".amazonaws.com";
        }
        Aws::Http::Scheme awsScheme = m_config.useSSL ? Aws::Http::Scheme::HTTPS : Aws::Http::Scheme::HTTP;

        // Build path
        std::string path = "/" + bucket;
        if (!objectKey.empty())
        {
            path += "/" + objectKey;
        }

        // Create URI object - this handles encoding properly for AWS Signature V4
        Aws::Http::URI uri;
        uri.SetScheme(awsScheme);
        uri.SetAuthority(convertToAwsString(host));
        uri.SetPath(convertToAwsString(path));

        // Add query parameters using AddQueryStringParameter for proper encoding
        // This method properly encodes parameter values (including special chars like +, =, /)
        for (const auto& param : queryParamsMap)
        {
            uri.AddQueryStringParameter(param.first.c_str(),
                                       convertToAwsString(param.second));
        }

        // Create HTTP request using the properly constructed URI
        auto request = Aws::Http::CreateHttpRequest(
            uri,
            method == "GET" ? Aws::Http::HttpMethod::HTTP_GET : Aws::Http::HttpMethod::HTTP_HEAD,
            Aws::Utils::Stream::DefaultResponseStreamFactoryMethod);

        if (!request)
        {
            result.message = "Failed to create HTTP request";
            result.errorCode = "REQUEST_ERROR";
            return result;
        }
        
        // Debug logging for MinIO troubleshooting
        LOG(verbose) << "S3 ListObjects Request - Method: " << method << ", URI: " << convertFromAwsString(uri.GetURIString());

        // Sign the request using AWS V4 signer
        if (!m_signer->SignRequest(*request))
        {
            result.message = "Failed to sign AWS request";
            result.errorCode = "SIGN_ERROR";
            return result;
        }

        // Make the HTTP request
        response = m_httpClient->MakeRequest(request);
        if (!response)
        {
            result.message = "HTTP request failed - no response";
            result.errorCode = "HTTP_ERROR";
            return result;
        }

        // Check response code
        if (static_cast<int>(response->GetResponseCode()) != HTTP_OK)
        {
            return handleAwsHttpError(response);
        }

        result.success = true;
        result.message = "S3 request successful";
    }
    catch (const std::exception& e)
    {
        result.message = "Exception making S3 request: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult S3CloudReader::makeS3Request(const std::string& method, const std::string& bucket,
                                         const std::string& objectKey, const std::string& queryParams,
                                         std::shared_ptr<Aws::Http::HttpResponse>& response)
{
    CloudResult result(false);

    try
    {
        // Use AWS SDK's URI class for secure URL construction
        // This ensures proper encoding and signature compatibility
        // Build S3 endpoint - use custom endpoint if provided (for MinIO compatibility)
        std::string host;
        if (!m_endpoint.empty())
        {
            // Extract host from custom endpoint (e.g., "http://<host>:9000")
            host = m_endpoint;
            // Remove protocol if present
            if (host.find("://") != std::string::npos)
            {
                host = host.substr(host.find("://") + 3);
            }
        }
        else
        {
            // Default to AWS S3 endpoint
            host = "s3." + m_config.region + ".amazonaws.com";
        }
        Aws::Http::Scheme awsScheme = m_config.useSSL ? Aws::Http::Scheme::HTTPS : Aws::Http::Scheme::HTTP;

        // Build path
        std::string path = "/" + bucket;
        if (!objectKey.empty())
        {
            path += "/" + objectKey;
        }

        // Create URI object - this handles encoding properly for AWS Signature V4
        Aws::Http::URI uri;
        uri.SetScheme(awsScheme);
        uri.SetAuthority(convertToAwsString(host));
        uri.SetPath(convertToAwsString(path));

        // Add query parameters if present (as a raw string)
        // Note: For pagination, prefer makeS3RequestWithParams which properly encodes each parameter
        if (!queryParams.empty())
        {
            uri.SetQueryString(convertToAwsString(queryParams));
        }

        // Create HTTP request using the properly constructed URI
        auto request = Aws::Http::CreateHttpRequest(
            uri,
            method == "GET" ? Aws::Http::HttpMethod::HTTP_GET : Aws::Http::HttpMethod::HTTP_HEAD,
            Aws::Utils::Stream::DefaultResponseStreamFactoryMethod);

        if (!request)
        {
            result.message = "Failed to create HTTP request";
            result.errorCode = "REQUEST_ERROR";
            return result;
        }

        // Sign the request using AWS V4 signer
        if (!m_signer->SignRequest(*request))
        {
            result.message = "Failed to sign AWS request";
            result.errorCode = "SIGN_ERROR";
            return result;
        }

        // Make the HTTP request
        response = m_httpClient->MakeRequest(request);
        if (!response)
        {
            result.message = "HTTP request failed - no response";
            result.errorCode = "HTTP_ERROR";
            return result;
        }

        // Check response code
        if (static_cast<int>(response->GetResponseCode()) != HTTP_OK)
        {
            return handleAwsHttpError(response);
        }

        result.success = true;
        result.message = "S3 request successful";
    }
    catch (const std::exception& e)
    {
        result.message = "Exception making S3 request: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult S3CloudReader::downloadToFileWithAwsClient(const std::string& bucket, const std::string& objectKey,
                                                       const std::string& localPath)
{
    CloudResult result(false);

    try
    {
        // Make S3 request for object download
        std::shared_ptr<Aws::Http::HttpResponse> response;
        CloudResult requestResult = makeS3Request("GET", bucket, objectKey, "", response);
        if (!requestResult.success)
        {
            result.message = "Failed to make S3 request: " + requestResult.message;
            result.errorCode = requestResult.errorCode;
            return result;
        }

        // Write response to file
        std::ofstream outFile(localPath, std::ios::binary);
        if (!outFile.is_open())
        {
            result.message = "Failed to open local file for writing: " + localPath;
            result.errorCode = "FILE_ERROR";
            return result;
        }

        auto& responseStream = response->GetResponseBody();
        outFile << responseStream.rdbuf();
        outFile.close();

        if (outFile.fail())
        {
            result.message = "Failed to write to local file: " + localPath;
            result.errorCode = "FILE_WRITE_ERROR";
            return result;
        }

        result.success = true;
        result.message = "File downloaded successfully";
    }
    catch (const std::exception& e)
    {
        result.message = "Exception downloading file: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudResult S3CloudReader::parseS3ListResponseWithAwsXml(const std::string& xmlContent, CloudListResult& result)
{
    CloudResult parseResult(false);

    try
    {
        // Parse XML using AWS SDK
        auto xmlDoc = Aws::Utils::Xml::XmlDocument::CreateFromXmlString(convertToAwsString(xmlContent));
        if (!xmlDoc.WasParseSuccessful())
        {
            parseResult.message = "Failed to parse XML: " + convertFromAwsString(xmlDoc.GetErrorMessage());
            parseResult.errorCode = "XML_PARSE_ERROR";
            return parseResult;
        }

        auto rootNode = xmlDoc.GetRootElement();
        if (rootNode.IsNull())
        {
            parseResult.message = "No root element in XML response";
            parseResult.errorCode = "XML_STRUCTURE_ERROR";
            return parseResult;
        }

        result.objects.clear();
        result.count = 0;
        result.totalSize = 0;

        // Parse pagination fields FIRST
        auto isTruncatedNode = rootNode.FirstChild("IsTruncated");
        if (!isTruncatedNode.IsNull())
        {
            std::string truncatedStr = convertFromAwsString(isTruncatedNode.GetText());
            result.isTruncated = (truncatedStr == "true");
        }
        else
        {
            result.isTruncated = false;
        }

        auto nextContinuationTokenNode = rootNode.FirstChild("NextContinuationToken");
        if (!nextContinuationTokenNode.IsNull())
        {
            result.nextMarker = convertFromAwsString(nextContinuationTokenNode.GetText());
        }
        else
        {
            result.nextMarker = "";
        }

        // Parse Contents elements
        auto contentsNode = rootNode.FirstChild("Contents");
        while (!contentsNode.IsNull())
        {
            CloudObject obj;

            auto keyNode = contentsNode.FirstChild("Key");
            if (!keyNode.IsNull())
            {
                obj.key = convertFromAwsString(keyNode.GetText());
            }

            auto sizeNode = contentsNode.FirstChild("Size");
            if (!sizeNode.IsNull())
            {
                try
                {
                    obj.size = std::stoull(convertFromAwsString(sizeNode.GetText()));
                    result.totalSize += obj.size;
                }
                catch (...)
                {
                    obj.size = 0;
                }
            }

            auto lastModifiedNode = contentsNode.FirstChild("LastModified");
            if (!lastModifiedNode.IsNull())
            {
                obj.lastModified = convertFromAwsString(lastModifiedNode.GetText());
            }

            auto etagNode = contentsNode.FirstChild("ETag");
            if (!etagNode.IsNull())
            {
                obj.etag = convertFromAwsString(etagNode.GetText());
            }

            result.objects.push_back(obj);
            result.count++;

            contentsNode = contentsNode.NextNode("Contents");
        }

        parseResult.success = true;
        parseResult.message = "Successfully parsed S3 response with " + std::to_string(result.count) +
                             " objects, isTruncated=" + (result.isTruncated ? "true" : "false");
    }
    catch (const std::exception& e)
    {
        parseResult.message = "Exception parsing S3 response: " + std::string(e.what());
        parseResult.errorCode = "PARSE_EXCEPTION";
    }

    return parseResult;
}

// Utility methods
std::string S3CloudReader::buildS3Endpoint(const std::string& bucket) const
{
    // Use custom endpoint if provided (for MinIO compatibility)
    if (!m_endpoint.empty() && m_endpoint.find("://") != std::string::npos)
    {
        // Extract host from endpoint (e.g., "http://<host>:9000")
        std::string host = m_endpoint.substr(m_endpoint.find("://") + 3);
        return bucket + "." + host;
    }
    // Default to AWS S3 endpoint
    return bucket + ".s3." + m_config.region + ".amazonaws.com";
}

Aws::String S3CloudReader::convertToAwsString(const std::string& str) const
{
    return Aws::String(str.c_str(), str.length());
}

std::string S3CloudReader::convertFromAwsString(const Aws::String& awsStr) const
{
    return std::string(awsStr.c_str(), awsStr.length());
}

CloudResult S3CloudReader::handleAwsHttpError(const std::shared_ptr<Aws::Http::HttpResponse>& response)
{
    CloudResult result(false);
    int httpCode = static_cast<int>(response->GetResponseCode());
    result.errorCode = "HTTP_" + std::to_string(httpCode);

    // Get detailed error message from response body
    std::string errorDetails = getAwsErrorMessage(response);
    
    switch (httpCode)
    {
        case 403:
            result.message = "Access denied (403): Check your AWS credentials and permissions - " + errorDetails;
            break;
        case 404:
            result.message = "Not found (404): Bucket or object does not exist - " + errorDetails;
            break;
        case 500:
            result.message = "Internal server error (500): AWS service error - " + errorDetails;
            break;
        default:
            result.message = "HTTP error " + std::to_string(httpCode) + ": " + errorDetails;
            break;
    }
    
    LOG(error) << "S3/MinIO HTTP Error " << httpCode << ": " << errorDetails;
    return result;
}

std::string S3CloudReader::getAwsErrorMessage(const std::shared_ptr<Aws::Http::HttpResponse>& response)
{
    try
    {
        auto& responseStream = response->GetResponseBody();
        std::string errorContent((std::istreambuf_iterator<char>(responseStream)), std::istreambuf_iterator<char>());
        return errorContent;
    }
    catch (...)
    {
        return "Unknown error";
    }
}

// Placeholder implementations for remaining methods
CloudResult S3CloudReader::getObjectInfo(const std::string& bucket, const std::string& objectKey,
                                         CloudObject& objectInfo)
{
    // Implementation would make HEAD request to get object metadata
    return CloudResult(false, "Not implemented yet");
}

CloudResult S3CloudReader::checkObjectExists(const std::string& bucket, const std::string& objectKey)
{
    // Implementation would make HEAD request
    return CloudResult(false, "Not implemented yet");
}

CloudResult S3CloudReader::listBuckets(std::vector<std::string>& buckets)
{
    // Implementation would list all accessible buckets
    return CloudResult(false, "Not implemented yet");
}

CloudResult S3CloudReader::checkBucketExists(const std::string& bucket)
{
    // Implementation would check if bucket exists and is accessible
    return CloudResult(false, "Not implemented yet");
}

CloudResult S3CloudReader::generatePresignedUrl(const std::string& bucket, const std::string& objectKey,
                                                uint32_t expirationSeconds, std::string& url)
{
    CloudResult result(false);

    if (!isAvailable())
    {
        result.message = "S3 Cloud Reader not properly configured";
        result.errorCode = "CONFIG_ERROR";
        return result;
    }

    try
    {
        // Use path-style URL for presigned URLs (consistent with main requests)
        // Use custom endpoint if provided (for MinIO compatibility)
        std::string host;
        if (!m_endpoint.empty())
        {
            // Extract host from custom endpoint
            host = m_endpoint;
            if (host.find("://") != std::string::npos)
            {
                host = host.substr(host.find("://") + 3);
            }
        }
        else
        {
            // Default to AWS S3 endpoint
            host = "s3." + m_config.region + ".amazonaws.com";
        }
        std::string uri = "/" + bucket + "/" + objectKey;
        std::string fullUrl = (m_config.useSSL ? "https://" : "http://") + host + uri;

        // Create HTTP request for pre-signing
        auto request = Aws::Http::CreateHttpRequest(convertToAwsString(fullUrl), Aws::Http::HttpMethod::HTTP_GET,
                                                    Aws::Utils::Stream::DefaultResponseStreamFactoryMethod);

        if (!request)
        {
            result.message = "Failed to create HTTP request for presigning";
            result.errorCode = "REQUEST_ERROR";
            return result;
        }

        // Use the signer to create a presigned URL
        if (m_signer->PresignRequest(*request, static_cast<long long>(expirationSeconds)))
        {
            url = convertFromAwsString(request->GetUri().GetURIString());
            result.success = true;
            result.message = "Presigned URL generated successfully";
        }
        else
        {
            result.message = "Failed to presign request";
            result.errorCode = "PRESIGN_ERROR";
        }
    }
    catch (const std::exception& e)
    {
        result.message = "Exception generating presigned URL: " + std::string(e.what());
        result.errorCode = "EXCEPTION";
    }

    return result;
}

CloudReaderStats S3CloudReader::getStats() const
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    return m_stats;
}

void S3CloudReader::resetStats()
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    m_stats = CloudReaderStats{};
}

CloudResult S3CloudReader::performHealthCheck()
{
    // Implementation would perform a lightweight operation to check connectivity
    return CloudResult(false, "Not implemented yet");
}

std::string S3CloudReader::getLastError() const
{
    std::lock_guard<std::mutex> lock(m_stats_mutex);
    return m_lastError;
}

// Helper method implementations

bool S3CloudReader::validateConfig() const
{
    return !m_config.accessKeyId.empty() && !m_config.secretAccessKey.empty() && !m_config.region.empty();
}

std::string S3CloudReader::getRequiredConfigParameter(const std::string& key) const
{
    if (key == "accessKeyId")
        return m_config.accessKeyId;
    if (key == "secretAccessKey")
        return m_config.secretAccessKey;
    if (key == "region")
        return m_config.region;
    if (key == "endpoint")
        return m_config.endpoint;
    return "";
}

} // namespace nv_vms