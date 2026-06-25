/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "webServer.h"
#include "logger.h"
#include "utils.h"
#include "vst_common.h"
#include "config.h"

// Handler to redirect /vst prefixed static file requests to root
// e.g., /vst/ → /, /vst/index.html → /index.html
// Note: API paths (/vst/api/...) are NOT redirected - they're handled by API handlers
class VstPrefixRedirectHandler : public CivetHandler
{
public:
    bool handleGet(CivetServer *server, struct mg_connection *conn) override
    {
        const struct mg_request_info *req_info = mg_get_request_info(conn);
        std::string uri = req_info->request_uri;

        const std::string URL_PREFIX = "/vst";
        const std::string API_PREFIX = "/vst/api/";

        // Don't redirect API paths - let them be handled by API handlers
        if (uri.find(API_PREFIX) == 0 || uri == "/vst/api")
        {
            return false; // Let other handlers process this
        }

        // Strip /vst prefix and redirect for static files
        std::string newUri = "/";
        if (uri.length() > URL_PREFIX.length())
        {
            newUri = uri.substr(URL_PREFIX.length());
        }

        // Send 302 redirect
        mg_printf(conn, "HTTP/1.1 302 Found\r\n");
        mg_printf(conn, "Location: %s\r\n", newUri.c_str());
        mg_printf(conn, "Content-Length: 0\r\n");
        mg_printf(conn, "\r\n");
        return true;
    }
};

WebServer::WebServer()
{
    LOG(verbose) << "Starting HTTP Server" << endl;
    std::string streamName;
    std::string nbthreads;

    DeviceConfig& config = GET_CONFIG();
    string httpAddress;
    httpAddress.append(config.http_port);

    std::string sslCertificate = "";
    string caCert;
    caCert = config.vst_data_path + string("/") + CA_CERTIFICATE_FILE_NAME;
    if (isFileExist(caCert))
    {
        LOG(info) << "CA certificate file path:" << caCert << endl;
        sslCertificate = caCert;
    }
    else
    {
        // Generate Self-signed ssl certificate.
        sslCertificate = vst_common::getSslCertificate();
    }
    // If HTTPS config is enabled check if certificate was generated successfully or not
    bool useHTTPS = config.use_https && !sslCertificate.empty();
    string enableWebsocketPingpong = config.enable_websocket_pingpong ? "yes" : "no";
    // http server
    string webroot       = VmsConfigManager::getInstance()->getWebRootPath();
    std::vector<std::string> options;
    options.push_back("document_root");
    options.push_back(webroot.c_str());
    options.push_back("enable_websocket_ping_pong");
    options.push_back(enableWebsocketPingpong);
    options.push_back("access_control_allow_origin");
    options.push_back("*");
    options.push_back("access_control_allow_methods");
    options.push_back("*");
    options.push_back("access_control_allow_headers");
    options.push_back("*");
    options.push_back("static_file_max_age");
    options.push_back("0");
    options.push_back("enable_directory_listing");
    options.push_back("no");
    options.push_back("extra_mime_types");
    options.push_back(".ts=video/mp2t");
    if (config.webservice_access_control_list.empty() == false)
    {
        options.push_back("access_control_list");
        options.push_back(config.webservice_access_control_list);
    }
    if (useHTTPS)
    {
        // Port config. If port has 's' appeneded to it, civetweb will enable SSL for REST server
        httpAddress = httpAddress + 's';
        // SSL certificate config
        options.push_back("ssl_certificate");
        options.push_back(sslCertificate);
    }
    options.push_back("listening_ports");
    options.push_back(httpAddress);

    // Enable MG_FEATURES_SSL feature of civetweb
    mg_init_library(MG_FEATURES_SSL);

    if (!nbthreads.empty()) {
        options.push_back("num_threads");
        options.push_back(nbthreads);
    }
    if (config.use_http_digest_authentication) {
        options.push_back("global_auth_file");
        options.push_back(config.password_file_path);
        options.push_back("authentication_domain");
        options.push_back(AUTHENTICATION_DOMAIN);
    }
    options.push_back("error_log_file");
    options.push_back("./webroot/log/vms_webserver_error.log");

    m_civetServer.reset (new CivetServer(options));
    m_websocket.reset(new WebsocketServerRequestHandler());

    // URL prefix for optional routing (e.g., /vst/api/v1/... → /api/v1/...)
    const std::string URL_PREFIX = "/vst";

    // add websocket endpoint
    m_civetServer->addWebSocketHandler("/vms/ws", *m_websocket);
    m_civetServer->addWebSocketHandler(URL_PREFIX + "/vms/ws", *m_websocket);

    #ifdef LIVE_STREAM_MODULE
    m_civetServer->addWebSocketHandler("/api/v1/live/ws", *m_websocket);
    m_civetServer->addWebSocketHandler(URL_PREFIX + "/api/v1/live/ws", *m_websocket);
    #endif

    #ifdef REPLAY_STREAM_MODULE
    m_civetServer->addWebSocketHandler("/api/v1/replay/ws", *m_websocket);
    m_civetServer->addWebSocketHandler(URL_PREFIX + "/api/v1/replay/ws", *m_websocket);
    #endif

    #ifdef STREAMBRIDGE_MODULE
    m_civetServer->addWebSocketHandler("/api/v1/streambridge/ws", *m_websocket);
    m_civetServer->addWebSocketHandler(URL_PREFIX + "/api/v1/streambridge/ws", *m_websocket);
    #endif

    // Add redirect handler for /vst prefixed static file requests (web UI)
    // Only handle exact /vst and /vst/ matches to redirect to root
    // Note: Do NOT register /vst/* as it would intercept API routes
    // API routes with /vst prefix are registered explicitly in HttpServerRequestHandler::addRequestHandler
    static VstPrefixRedirectHandler vstRedirectHandler;
    m_civetServer->addHandler("/vst$", vstRedirectHandler);  // Exact match for /vst
    m_civetServer->addHandler("/vst/$", vstRedirectHandler); // Exact match for /vst/

    m_httpServerHandler.reset( new HttpServerRequestHandler(m_civetServer));
    
    // Initialize OpenTelemetry tracing with module-specific service name
    if (config.enable_telemetry)
    {
        std::string otlpEndpoint = config.otlp_endpoint.empty() 
            ? "http://localhost:4318/v1/traces" 
            : config.otlp_endpoint;
        
#ifdef MODULE_ID
        std::string serviceName = std::string("vst-") + MODULE_ID;
#else
        std::string serviceName = "vst-monolith";
#endif
        
        HttpServerRequestHandler::initializeTracing(otlpEndpoint, serviceName);
    }
    else
    {
        LOG(info) << "OpenTelemetry tracing disabled by configuration" << std::endl;
    }

    LOG(info) << "HTTP Listen at " << g_hostIp << ":" << config.http_port << std::endl;
}


WebServer::~WebServer()
{
    // Shutdown OpenTelemetry tracing
    HttpServerRequestHandler::shutdownTracing();
    
    m_civetServer->close();
    // call mg_exit_library() because mg_init_library(MG_FEATURES_SSL) was called earlier
    if (mg_exit_library() == 0)
    {
        LOG(error) << "Failed to exit civetweb library" << endl;
    }
}


void WebServer::registerRESTAPIs(std::map<std::string, HttpServerRequestHandler::httpFunction, std::less<>>& func)
{
    m_httpServerHandler->addRequestHandler(func);
}

void WebServer::registerWSAPIs(std::map<std::string, WebsocketServerRequestHandler::httpFunction, std::less<>>& func)
{
    m_websocket->addRequestHandler(func);
} 