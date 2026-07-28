/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

/**
 * Minimal in-process HTTP/1.1 server for unit tests: parses one request per
 * connection, records it, and echoes the body back. Shared by the
 * async_http_client and webhook_notifier test suites.
 */
class TinyHttpServer
{
public:
    struct ReceivedRequest
    {
        std::string m_method;
        std::string m_path;  // includes the query string when present
        std::string m_body;
        std::map<std::string, std::string> m_headers;
    };

    explicit TinyHttpServer(int responseStatus = 200, int responseDelayMs = 0)
        : m_responseStatus(responseStatus), m_responseDelayMs(responseDelayMs)
    {
    }

    ~TinyHttpServer() { stop(); }

    bool start()
    {
        m_listenFd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (m_listenFd < 0)
        {
            return false;
        }
        int reuse = 1;
        ::setsockopt(m_listenFd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = 0;  // ephemeral port
        if (::bind(m_listenFd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0 ||
            ::listen(m_listenFd, 16) != 0)
        {
            ::close(m_listenFd);
            m_listenFd = -1;
            return false;
        }

        socklen_t len = sizeof(addr);
        if (::getsockname(m_listenFd, reinterpret_cast<sockaddr*>(&addr), &len) != 0)
        {
            ::close(m_listenFd);
            m_listenFd = -1;
            return false;
        }
        m_port = ntohs(addr.sin_port);

        m_acceptThread = std::thread(&TinyHttpServer::acceptLoop, this);
        return true;
    }

    void stop()
    {
        if (m_listenFd >= 0)
        {
            ::shutdown(m_listenFd, SHUT_RDWR);
            ::close(m_listenFd);
            m_listenFd = -1;
        }
        if (m_acceptThread.joinable())
        {
            m_acceptThread.join();
        }
        for (auto& worker : m_workers)
        {
            if (worker.joinable())
            {
                worker.join();
            }
        }
        m_workers.clear();
    }

    int port() const { return m_port; }

    std::string url(const std::string& path) const
    {
        return "http://127.0.0.1:" + std::to_string(m_port) + path;
    }

    std::vector<ReceivedRequest> requests() const
    {
        std::lock_guard<std::mutex> lock(m_requestsMutex);
        return m_requests;
    }

    /**
     * Response status per request index; the last entry repeats for any
     * further requests. Overrides the constructor status. Set before the
     * requests it should apply to are made.
     */
    void setStatusSequence(std::vector<int> statuses)
    {
        std::lock_guard<std::mutex> lock(m_requestsMutex);
        m_statusSequence = std::move(statuses);
    }

private:
    void acceptLoop()
    {
        while (true)
        {
            const int connFd = ::accept(m_listenFd, nullptr, nullptr);
            if (connFd < 0)
            {
                break;  // listen socket closed by stop()
            }
            m_workers.emplace_back(&TinyHttpServer::handleConnection, this, connFd);
        }
    }

    void handleConnection(int connFd)
    {
        std::string raw;
        char buf[2048];
        size_t headerEnd = std::string::npos;
        while (headerEnd == std::string::npos)
        {
            const ssize_t n = ::recv(connFd, buf, sizeof(buf), 0);
            if (n <= 0)
            {
                ::close(connFd);
                return;
            }
            raw.append(buf, static_cast<size_t>(n));
            headerEnd = raw.find("\r\n\r\n");
        }

        ReceivedRequest request;
        size_t requestLineEnd = raw.find("\r\n");
        {
            const size_t methodEnd = raw.find(' ');
            const size_t pathEnd = raw.find(' ', methodEnd + 1);
            request.m_method = raw.substr(0, methodEnd);
            request.m_path = raw.substr(methodEnd + 1, pathEnd - methodEnd - 1);
        }

        size_t lineStart = requestLineEnd + 2;
        while (lineStart < headerEnd)
        {
            const size_t lineEnd = raw.find("\r\n", lineStart);
            const std::string line = raw.substr(lineStart, lineEnd - lineStart);
            const size_t colon = line.find(':');
            if (colon != std::string::npos)
            {
                size_t valueStart = colon + 1;
                while (valueStart < line.size() && line[valueStart] == ' ')
                {
                    valueStart++;
                }
                request.m_headers[line.substr(0, colon)] = line.substr(valueStart);
            }
            lineStart = lineEnd + 2;
        }

        size_t contentLength = 0;
        {
            const std::string marker = "Content-Length:";
            const size_t pos = raw.find(marker);
            if (pos != std::string::npos && pos < headerEnd)
            {
                contentLength = static_cast<size_t>(
                    std::stoul(raw.substr(pos + marker.size(), 20)));
            }
        }
        std::string body = raw.substr(headerEnd + 4);
        while (body.size() < contentLength)
        {
            const ssize_t n = ::recv(connFd, buf, sizeof(buf), 0);
            if (n <= 0)
            {
                break;
            }
            body.append(buf, static_cast<size_t>(n));
        }
        request.m_body = body;

        int status = m_responseStatus;
        {
            std::lock_guard<std::mutex> lock(m_requestsMutex);
            if (!m_statusSequence.empty())
            {
                const size_t index = std::min(m_requests.size(), m_statusSequence.size() - 1);
                status = m_statusSequence[index];
            }
            m_requests.push_back(request);
        }

        if (m_responseDelayMs > 0)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(m_responseDelayMs));
        }

        const std::string echo = "echo:" + request.m_body;
        const std::string response =
            "HTTP/1.1 " + std::to_string(status) + " Status\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: " + std::to_string(echo.size()) + "\r\n"
            "Connection: close\r\n\r\n" + echo;
        (void)::send(connFd, response.data(), response.size(), MSG_NOSIGNAL);
        ::close(connFd);
    }

    int m_responseStatus;
    int m_responseDelayMs;
    int m_listenFd{-1};
    int m_port{0};
    std::thread m_acceptThread;
    std::vector<std::thread> m_workers;
    mutable std::mutex m_requestsMutex;
    std::vector<ReceivedRequest> m_requests;
    std::vector<int> m_statusSequence;
};
