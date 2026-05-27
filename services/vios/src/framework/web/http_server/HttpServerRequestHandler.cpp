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

#include <string.h>
#include <iostream>

#include "HttpServerRequestHandler.h"
#include "UserAuthHandler.h"
#include "logger.h"
#include "error_code.h"
#include "utilities/config.h"
#include <SchemaValidator.h>
#include "network_utils.h"
#include "OtelTracing.h"

#ifdef USE_OTEL
#include "opentelemetry/trace/span.h"
#include "opentelemetry/trace/semantic_conventions.h"
#include "opentelemetry/trace/span_startoptions.h"

namespace trace_api = opentelemetry::trace;
namespace trace_semantic = opentelemetry::trace::SemanticConventions;
#endif

using namespace std;

constexpr char STORAGE_MANAGEMENT_UPLOAD_API[] = "/api/v1/storage/file";
constexpr char STORAGE_MANAGEMENT_UPLOAD_API_VST[] = "/vst/api/v1/storage/file";
constexpr int MAX_JSON_CONTENT_LENGTH = 100000;  // 100KB max for JSON payloads
#define MAX_FILE_UPLOAD_SIZE_MB_TO_BYTES(mb) ((long long)(mb) * 1024 * 1024)  // Convert MB to bytes

// HTTP Method constants
constexpr const char* HTTP_METHOD_GET = "GET";
constexpr const char* HTTP_METHOD_HEAD = "HEAD";
constexpr const char* HTTP_METHOD_DELETE = "DELETE";
constexpr const char* HTTP_METHOD_OPTIONS = "OPTIONS";
constexpr const char* HTTP_METHOD_POST = "POST";
constexpr const char* HTTP_METHOD_PUT = "PUT";
constexpr const char* HTTP_METHOD_PATCH = "PATCH";


int log_message(const struct mg_connection *conn, const char *message) 
{
    fprintf(stderr, "%s\n", message);
    LOG(verbose) << "HTTP SERVER: " << message << endl;
    return 0;
}

static struct CivetCallbacks _callbacks;
const struct CivetCallbacks * getCivetCallbacks() 
{
    //memset(&_callbacks, 0, sizeof(_callbacks));
    _callbacks.log_message = &log_message;
    return &_callbacks;
}

static bool log_api_info(const std::string& api_name)
{
    if ((api_name == "/api/stream/status") ||
        (api_name == "/api/stream/stats")  ||
        (api_name == "/api/debug/stats") ||
        (api_name == "/api/v1/live/stream/status") ||
        (api_name == "/api/v1/replay/stream/status") ||
        (api_name == "/api/v1/live/stream/stats") ||
        (api_name == "/api/v1/sensor/debug/system/stats") ||
        (api_name == "/api/v1/replay/stream/stats"))
    {
        return false;
    }

    // Check for /vst prefix apis
    if ((api_name == "/vst/api/stream/status") ||
        (api_name == "/vst/api/stream/stats")  ||
        (api_name == "/vst/api/debug/stats") ||
        (api_name == "/vst/api/v1/live/stream/status") ||
        (api_name == "/vst/api/v1/replay/stream/status") ||
        (api_name == "/vst/api/v1/live/stream/stats") ||
        (api_name == "/vst/api/v1/sensor/debug/system/stats") ||
        (api_name == "/vst/api/v1/replay/stream/stats"))
    {
        return false;
    }

    // Skip logging for health probe endpoints
    if ((api_name == "/v1/live") ||
        (api_name == "/v1/ready") ||
        (api_name == "/v1/startup"))
    {
        return false;
    }

    return true;
}

/* ---------------------------------------------------------------------------
**  Civet HTTP callback 
** -------------------------------------------------------------------------*/
class RequestHandler : public CivetHandler
{
  public:
    RequestHandler(std::string uri, HttpServerRequestHandler::httpFunction & func):  m_uri(uri)
                                                                                   , m_func(func)
    {
    }

    bool handle(CivetServer *server, struct mg_connection *conn)
    {
        bool ret = false;
        Json::Value response;
        Json::Value req;
        VmsErrorCode result;
        DeviceConfig config =  GET_CONFIG();
        const struct mg_request_info *req_info = mg_get_request_info(conn);
        if(req_info == nullptr)
        {
            LOG(error) << "req_info is NULL "<< std::endl;
            return ret;
        }
        
        const std::string uri = safeGetString(req_info->request_uri);
        const std::string method = safeGetString(req_info->request_method);
        
#ifdef USE_OTEL
        trace_api::StartSpanOptions span_options;
        span_options.kind = trace_api::SpanKind::kServer;
        
        auto tracer = OtelTracing::GetTracer();
        opentelemetry::nostd::shared_ptr<trace_api::Span> span;
        if (tracer && OtelTracing::IsEnabled())
        {
            span = tracer->StartSpan("HTTP " + method + " " + uri, span_options);
        }
#endif
        
        if (log_api_info(uri))
        {
            LOG(info) << "uri:" << uri << std::endl;
            LOG(info) << "method:" << method << std::endl;
        }
        
#ifdef USE_OTEL
        if (span)
        {
            span->SetAttribute(trace_semantic::kHttpMethod, method);
            span->SetAttribute(trace_semantic::kHttpUrl, uri);
            span->SetAttribute(trace_semantic::kHttpScheme, "http");
            span->SetAttribute("http.client_ip", safeGetString(req_info->remote_addr));
        }
#endif
        
        // Validate HTTP method
        if (!isValidHttpMethod(req_info->request_method))
        {
            LOG(error) << "Invalid HTTP method: " << method << std::endl;
#ifdef USE_OTEL
            if (span)
            {
                span->SetStatus(trace_api::StatusCode::kError, "Invalid HTTP method");
                span->End();
            }
#endif
            return ret;
        }

        if (!m_uri.empty() && m_uri.back() != '*')
        {
            if (!safeStringEqual(req_info->request_uri, m_uri.c_str()))
            {
                LOG(error) << "Wrong API uri:" << safeGetString(req_info->request_uri) 
                          << " Please use correct uri: " << m_uri << std::endl;
#ifdef USE_OTEL
                if (span)
                {
                    span->SetStatus(trace_api::StatusCode::kError, "Wrong API URI");
                    span->End();
                }
#endif
                return ret;
            }
        }
        
        // read input
        Json::Value  in;
        result = this->getInputMessage(req_info, conn, in);
        if (result == VmsErrorCode::NoError)
        {
            // URL prefix for optional routing - strip /vst prefix from all API URLs
            // This normalizes URLs like /vst/api/v1/storage/file/... to /api/v1/storage/file/...
            // so that all API handlers work consistently regardless of prefix
            const std::string URL_PREFIX = "/vst";
            std::string normalizedUri = uri;
            if (uri.find(URL_PREFIX) == 0)
            {
                normalizedUri = uri.substr(URL_PREFIX.length());
            }

            req["url"] = normalizedUri;
            req["method"] = method;
            req["query"] = safeGetString(req_info->query_string);
            req["remote_addr"] = safeGetString(req_info->remote_addr);
            req["remote_user"] = safeGetString(req_info->remote_user);

            // Use the same normalized URI for schema validation
            std::string schemaValidationUri = normalizedUri;

            // Type check for requests with body content (POST, PUT, PATCH) and query parameter validation
            std::string queryString = safeGetString(req_info->query_string);
            if (isBodyContentMethod(method) && 
                !SchemaValidator::validateRequest(schemaValidationUri.c_str(), in, queryString))
            {
                LOG(error) << "Schema validation failed for " << method << " request to " << uri << endl;
                if (!queryString.empty())
                {
                    LOG(warning) << "Query string validation may have failed for security reasons" << endl;
                }
                SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response);
                result = VmsErrorCode::InvalidParameterError;
#ifdef USE_OTEL
                if (span)
                {
                    span->SetStatus(trace_api::StatusCode::kError, "Schema validation failed");
                    span->End();
                }
#endif
                return httpResponseHandler(result, response, conn);
            }
            // For GET and other methods, validate query parameters only  
            else if (isNonBodyContentMethod(method) && 
                     !queryString.empty() && !SchemaValidator::validateRequest(schemaValidationUri.c_str(), Json::objectValue, queryString))
            {
                LOG(error) << "Query parameter validation failed for " << method << " request to " << uri << endl;
                LOG(warning) << "Query parameters may contain security violations or exceed limits" << endl;
                SET_VMS_ERROR(VmsErrorCode::InvalidParameterError, response);
                result = VmsErrorCode::InvalidParameterError;
#ifdef USE_OTEL
                if (span)
                {
                    span->SetStatus(trace_api::StatusCode::kError, "Query parameter validation failed");
                    span->End();
                }
#endif
                return httpResponseHandler(result, response, conn);
            }
            // invoke API implementation
            if (config.use_multi_user)
            {
                if (!UserAuthHandler::isAuthorized(req, in, conn))
                {
                    string error_message = string("User is not authorized");
                    LOG(error) << error_message << endl;
                    SET_VMS_ERROR2(VmsErrorCode::ClientUnauthorizedError, response, error_message.c_str());
                    result = VmsErrorCode::ClientUnauthorizedError;
                }
                else if (safeStringEqual(req_info->request_uri, LOGIN_API))
                {
                    // LOGIN_APi may not have cookie header so handle it separately
                    result = m_func(req, in, response, conn);
                    if (result == VmsErrorCode::NoError)
                    {
                        return true;
                    }
                }
                else
                {
                    // APIs other than LOGIN_APi must have cookie header
                    string user;
                    string session;
                    if (!UserAuthHandler::extractUserCredentials(conn, user, session))
                    {
                        return false;
                    }
                    req["username"] = user;
                    req["session_id"] = session;
                    result = m_func(req, in, response, conn);
                }
            }
            else
            {
                // ignore multi-user support
                result = m_func(req, in, response, conn);
            }
        }
        else
        {
            response = in;
        }
        
        bool final_result = httpResponseHandler(result, response, conn);
        
#ifdef USE_OTEL
        if (span)
        {
            if (result == VmsErrorCode::NoError)
            {
                span->SetAttribute(trace_semantic::kHttpStatusCode, 200);
                span->SetStatus(trace_api::StatusCode::kOk);
            }
            else
            {
                std::pair<int, string> http_err_code = translateVmsErrorCodeToCameraHttpErrorCode(result);
                span->SetAttribute(trace_semantic::kHttpStatusCode, http_err_code.first);
                span->SetStatus(trace_api::StatusCode::kError, http_err_code.second);
            }
            span->End();
        }
#endif
        
        return final_result;
    }

    bool httpResponseHandler(VmsErrorCode& result, Json::Value& response, struct mg_connection *conn)
    {
        if (result == VmsErrorCode::NoError)
        {
            mg_printf(conn,"HTTP/1.1 200 OK\r\n");
        }
        else
        {
            std::pair<int, string> http_err_code = translateVmsErrorCodeToCameraHttpErrorCode(result);
            string response = string("HTTP/1.1 ") + to_string(http_err_code.first) + " " + http_err_code.second;
            mg_printf(conn, "%s\r\n", response.c_str());
        }
        mg_printf(conn,"Access-Control-Allow-Origin: *\r\n");
        string content_type;
        if (response.isObject())
        {
            content_type = response.get("content_type", "").asString();
        }
        std::string answer;
        if (content_type.empty() == false)
        {
            answer = response.get("data", "").asString();
            mg_printf(conn,"Content-Type: image/jpeg\r\n");
        }
        else
        {
            string ans (Json::writeString(m_writerBuilder, response));
            answer = ans;
            mg_printf(conn,"Content-Type: text/plain\r\n");
        }
        mg_printf(conn,"Content-Length: %zd\r\n", answer.size());
        mg_printf(conn,"Connection: close\r\n");
        mg_printf(conn,"\r\n");
        mg_write(conn,answer.c_str(),answer.size());
        return true;
    }

    bool isBodyContentMethod(const std::string& method)
    {
        return (method == HTTP_METHOD_POST || method == HTTP_METHOD_PUT || method == HTTP_METHOD_PATCH);
    }

    bool isNonBodyContentMethod(const std::string& method)
    {
        return (method != HTTP_METHOD_POST && method != HTTP_METHOD_PUT && method != HTTP_METHOD_PATCH);
    }
    
    bool handleGet(CivetServer *server, struct mg_connection *conn)
    {
        return handle(server, conn);
    }
    bool handlePost(CivetServer *server, struct mg_connection *conn)
    {
        return handle(server, conn);
    }
    bool handlePut(CivetServer *server, struct mg_connection *conn)
    {
        return handle(server, conn);
    }
    bool handleDelete(CivetServer *server, struct mg_connection *conn)
    {
        return handle(server, conn);
    }

  private:
    std::string                                 m_uri;
    HttpServerRequestHandler::httpFunction      m_func; 
    Json::StreamWriterBuilder                   m_writerBuilder;
    Json::CharReaderBuilder                     m_readerBuilder;

    VmsErrorCode handleApiFile(std::string &fileName, std::string &fileExtension, const char *content_type, 
                                const char *content_disposition, Json::Value& out)
    {
        DeviceConfig config =  GET_CONFIG();
        vector<std::string> supportedMedia = config.media_containers;
        fileName = getFileNameFromHeader(content_disposition);
#if 0
        bool isMediaTypeSupported = false;
        fileExtension = getExtensionFromHeader(content_type);
        if (fileName.empty() || fileExtension.empty())
        {
            out = Json::nullValue;
            string error_message = "Unable to get file information from HTTP header";
            LOG(error) << error_message << std::endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
        for(uint32_t i = 0; i < supportedMedia.size(); i++)
        {
            if(supportedMedia[i] == fileExtension)
            {
                isMediaTypeSupported = true;
                break;
            }
        }
        if (!isMediaTypeSupported)
        {
            out = Json::nullValue;
            string error_message = "Media type is not supported";
            LOG(error) << error_message << std::endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str())
            return VmsErrorCode::InvalidParameterError;
        }
#endif
        return VmsErrorCode::NoError;
    }

    bool isFileUploadAPI(const char *api, const char *method)
    {
        if (api == nullptr || method == nullptr)
        {
            return false;
        }

        // Check for POST to exact storage file API (with and without /vst prefix)
        if ((strcmp(method, HTTP_METHOD_POST) == 0) &&
            ((strcmp(api, STORAGE_MANAGEMENT_UPLOAD_API) == 0) ||
             (strcmp(api, STORAGE_MANAGEMENT_UPLOAD_API_VST) == 0)))
        {
            return true;
        }

        // Check for PUT to storage file API path (with and without /vst prefix)
        if ((strcmp(method, HTTP_METHOD_PUT) == 0) &&
            ((strncmp(api, STORAGE_MANAGEMENT_UPLOAD_API, sizeof(STORAGE_MANAGEMENT_UPLOAD_API) - 1) == 0) ||
             (strncmp(api, STORAGE_MANAGEMENT_UPLOAD_API_VST, sizeof(STORAGE_MANAGEMENT_UPLOAD_API_VST) - 1) == 0)))
        {
            return true;
        }

        return false;
    }

    VmsErrorCode getInputMessage(const struct mg_request_info *req_info, struct mg_connection *conn, Json::Value& out)
    {
        //Return if content length is zero otherwise procede to check content type
        if(req_info == nullptr || conn == nullptr)
        {
            out = Json::nullValue;
            string error_message = "Request Information is null";
            LOG(error) << error_message << std::endl;
            SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
            return VmsErrorCode::InvalidParameterError;
        }
        // Don't parse input message if its a upload API
        if(isFileUploadAPI(req_info->request_uri, req_info->request_method))
        {
            LOG(info) << "Upload API, skip parsing message" << endl;
            return VmsErrorCode::NoError;
        }
        
        // For GET, HEAD, DELETE, OPTIONS requests, typically no content to parse
        std::string method = safeGetString(req_info->request_method);
        if (method == HTTP_METHOD_GET || method == HTTP_METHOD_HEAD || method == HTTP_METHOD_DELETE || method == HTTP_METHOD_OPTIONS) 
        {
            if (req_info->content_length > 0) 
            {
                LOG(info) << "Unexpected content for " << method << " request, ignoring" << endl;
            }
            return VmsErrorCode::NoError;
        }
        
        // For other methods, validate content length with appropriate limits
        long long maxAllowedLength;
        
        if (isFileUploadAPI(req_info->request_uri, req_info->request_method)) 
        {
            // For file upload APIs, use the configured upload size limit
            DeviceConfig config = GET_CONFIG();
            maxAllowedLength = MAX_FILE_UPLOAD_SIZE_MB_TO_BYTES(config.nv_streamer_max_upload_file_size_MB);
        }
        else 
        {
            // For JSON APIs, use smaller limit
            maxAllowedLength = MAX_JSON_CONTENT_LENGTH;
        }
        
        if (!isValidContentLength(req_info->content_length, maxAllowedLength, req_info->request_method))
        {
            LOG(error) << "Invalid content length: " << req_info->content_length 
                      << " for method: " << method << " (max allowed: " << maxAllowedLength << ")" << endl;
            return VmsErrorCode::VMSNotSupportedError;
        }

        long long tlen = req_info->content_length;
        if (tlen > 0)
        {
            try 
            {
                std::string body;
                body.reserve(tlen);  // Pre-allocate memory
                
                const size_t bufSize = 4096;  // Larger buffer for better performance
                std::vector<char> buf(bufSize);
                long long nlen = 0;
                
                while (nlen < tlen) 
                {
                    size_t toRead = std::min((long long)bufSize, tlen - nlen);
                    size_t rlen = mg_read(conn, buf.data(), toRead);
                    
                    if (rlen <= 0) 
                    {
                        LOG(error) << "Failed to read full request body (" << nlen << "/" << tlen << " bytes)";
                        SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out,
                                       "Incomplete request body");
                        return VmsErrorCode::InvalidParameterError;
                    }
                    
                    body.append(buf.data(), rlen);
                    nlen += rlen;
                }
                
                // Get client IP for logging
                std::string clientIp = safeGetString(req_info->remote_addr);
                
                // Validate JSON safety before parsing to prevent attacks
                if (!isJsonSafe(body, 50, 1000)) 
                {
                    out = Json::nullValue;
                    string error_message = "JSON structure unsafe: excessive nesting or size detected";
                    LOG(error) << error_message << " (size: " << body.size() << " bytes) from IP: " << clientIp << std::endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
                    return VmsErrorCode::InvalidParameterError;
                }
                
                // Use safe JSON parser with strict limits
                Json::CharReaderBuilder safeBuilder = createSafeJsonReaderBuilder();
                std::unique_ptr<Json::CharReader> reader(safeBuilder.newCharReader());
                std::string errors;
                
                if (!reader->parse(body.c_str(), body.c_str() + body.size(), &out, &errors))
                {
                    out = Json::nullValue;
                    string error_message = "JSON parse failed: " + errors;
                    LOG(error) << error_message << std::endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
                    return VmsErrorCode::InvalidParameterError;
                }
                
                // Validate parsed JSON structure for additional safety
                if (!validateJsonStructure(out, 50)) 
                {
                    out = Json::nullValue;
                    string error_message = "JSON structure validation failed: unsafe nesting or size";
                    LOG(error) << error_message << std::endl;
                    SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, error_message.c_str());
                    return VmsErrorCode::InvalidParameterError;
                }
            }
            catch(const std::exception& e)
            {
                LOG(error) << "Exception while reading content: " << e.what() << endl;
                out = Json::nullValue;
                SET_VMS_ERROR2(VmsErrorCode::InvalidParameterError, out, e.what());
                return VmsErrorCode::InvalidParameterError;
            }
        }
        return VmsErrorCode::NoError;
    }
};

/* ---------------------------------------------------------------------------
**  Constructor
** -------------------------------------------------------------------------*/
HttpServerRequestHandler::HttpServerRequestHandler(std::shared_ptr<CivetServer> m_civetServer)
{
    m_civet = m_civetServer;
    LOG(info) << "Civetweb version: v" << mg_version() << endl;
}

void HttpServerRequestHandler::addRequestHandler(std::map<std::string,httpFunction, std::less<>>& func)
{
    // URL prefix for optional routing (e.g., /vst/api/v1/... → /api/v1/...)
    const std::string URL_PREFIX = "/vst";

    // register handlers
    for (auto it : func)
    {
        // Register the original handler (we retain ownership in m_handlers)
        auto handler1 = std::make_unique<RequestHandler>(it.first, it.second);
        m_civet->addHandler(it.first, handler1.get());
        m_handlers.push_back(std::move(handler1));

        // Also register with /vst prefix for compatibility
        // e.g., /api/v1/sensor/list → also accessible at /vst/api/v1/sensor/list
        std::string prefixedUri = URL_PREFIX + it.first;
        auto handler2 = std::make_unique<RequestHandler>(prefixedUri, it.second);
        m_civet->addHandler(prefixedUri, handler2.get());
        m_handlers.push_back(std::move(handler2));
    }
}

void HttpServerRequestHandler::initializeTracing(const std::string& otlp_endpoint, const std::string& service_name)
{
    OtelTracing::Initialize(otlp_endpoint, service_name);
    if (OtelTracing::IsEnabled())
    {
        LOG(info) << "OpenTelemetry tracing initialized with OTLP endpoint: " << otlp_endpoint << endl;
    }
    else
    {
        LOG(warning) << "OpenTelemetry tracing initialization failed" << endl;
    }
}

void HttpServerRequestHandler::shutdownTracing()
{
    OtelTracing::Shutdown();
    LOG(info) << "OpenTelemetry tracing shutdown complete" << endl;
}