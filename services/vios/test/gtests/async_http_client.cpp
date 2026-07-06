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

/**
 * @file async_http_client.cpp
 * @brief Temporary verification for AsyncHttpClient until WebhookNotifier lands.
 *
 * Spins a minimal in-process HTTP server on an ephemeral localhost port and
 * exercises POST, PUT and DELETE submissions, status code pass-through,
 * std::any user data round-trip, concurrent transfers, delayed submission,
 * transport error reporting, and stop() abort semantics.
 */

#include "gtest/gtest.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "async_http_client.h"

using namespace std::chrono;

namespace
{

/** Minimal HTTP/1.1 server: parses one request per connection, echoes the body back. */
class TinyHttpServer
{
public:
    struct ReceivedRequest
    {
        std::string m_method;
        std::string m_path;
        std::string m_body;
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
        {
            const size_t methodEnd = raw.find(' ');
            const size_t pathEnd = raw.find(' ', methodEnd + 1);
            request.m_method = raw.substr(0, methodEnd);
            request.m_path = raw.substr(methodEnd + 1, pathEnd - methodEnd - 1);
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

        {
            std::lock_guard<std::mutex> lock(m_requestsMutex);
            m_requests.push_back(request);
        }

        if (m_responseDelayMs > 0)
        {
            std::this_thread::sleep_for(milliseconds(m_responseDelayMs));
        }

        const std::string echo = "echo:" + request.m_body;
        const std::string response =
            "HTTP/1.1 " + std::to_string(m_responseStatus) + " Status\r\n"
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
};

/** Blocks a test until the completion callback fires. */
struct CallbackCapture
{
    void set(const AsyncHttpResponse& response, const std::any& userData)
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_response = response;
        m_userData = userData;
        m_done = true;
        m_cv.notify_all();
    }

    bool wait(int timeoutMs = 5000)
    {
        std::unique_lock<std::mutex> lock(m_mutex);
        return m_cv.wait_for(lock, milliseconds(timeoutMs), [this] { return m_done; });
    }

    std::mutex m_mutex;
    std::condition_variable m_cv;
    bool m_done{false};
    AsyncHttpResponse m_response;
    std::any m_userData;
};

}  // unnamed namespace

TEST(AsyncHttpClientTest, PostDeliversStatusBodyAndUserData)
{
    TinyHttpServer server(201);
    ASSERT_TRUE(server.start());

    AsyncHttpClient client;
    ASSERT_TRUE(client.start());

    AsyncHttpRequest request;
    request.m_url = server.url("/api/v1/cameras");
    request.m_method = "POST";
    request.m_body = R"({"event":"camera_add"})";
    request.m_headers = {"Content-Type: application/json", "X-Test-Header: vios"};

    CallbackCapture capture;
    ASSERT_TRUE(client.submit(request,
        [&capture](const AsyncHttpResponse& response, const std::any& userData) {
            capture.set(response, userData);
        },
        std::string("wh-001")));

    ASSERT_TRUE(capture.wait());
    EXPECT_TRUE(capture.m_response.transportOk());
    EXPECT_EQ(capture.m_response.m_httpStatus, 201);
    EXPECT_EQ(capture.m_response.m_body, R"(echo:{"event":"camera_add"})");
    EXPECT_EQ(std::any_cast<std::string>(capture.m_userData), "wh-001");

    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 1u);
    EXPECT_EQ(seen[0].m_method, "POST");
    EXPECT_EQ(seen[0].m_path, "/api/v1/cameras");
    EXPECT_EQ(seen[0].m_body, R"({"event":"camera_add"})");

    client.stop();
}

TEST(AsyncHttpClientTest, PutAndDeleteMethodsReachServer)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    AsyncHttpClient client;
    ASSERT_TRUE(client.start());

    AsyncHttpRequest putRequest;
    putRequest.m_url = server.url("/api/v1/streams");
    putRequest.m_method = "PUT";
    putRequest.m_body = R"({"state":"streaming"})";

    AsyncHttpRequest deleteRequest;
    deleteRequest.m_url = server.url("/api/v1/cameras/cam-7");
    deleteRequest.m_method = "DELETE";

    CallbackCapture putCapture;
    CallbackCapture deleteCapture;
    ASSERT_TRUE(client.submit(putRequest,
        [&putCapture](const AsyncHttpResponse& r, const std::any& d) { putCapture.set(r, d); }));
    ASSERT_TRUE(client.submit(deleteRequest,
        [&deleteCapture](const AsyncHttpResponse& r, const std::any& d) { deleteCapture.set(r, d); }));

    ASSERT_TRUE(putCapture.wait());
    ASSERT_TRUE(deleteCapture.wait());
    EXPECT_EQ(putCapture.m_response.m_httpStatus, 200);
    EXPECT_EQ(deleteCapture.m_response.m_httpStatus, 200);

    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 2u);
    bool sawPut = false;
    bool sawDelete = false;
    for (const auto& req : seen)
    {
        if (req.m_method == "PUT" && req.m_path == "/api/v1/streams" &&
            req.m_body == R"({"state":"streaming"})")
        {
            sawPut = true;
        }
        if (req.m_method == "DELETE" && req.m_path == "/api/v1/cameras/cam-7")
        {
            sawDelete = true;
        }
    }
    EXPECT_TRUE(sawPut);
    EXPECT_TRUE(sawDelete);

    client.stop();
}

TEST(AsyncHttpClientTest, TransfersRunConcurrently)
{
    constexpr int SERVER_DELAY_MS = 500;
    constexpr int REQUEST_COUNT = 3;
    TinyHttpServer server(200, SERVER_DELAY_MS);
    ASSERT_TRUE(server.start());

    AsyncHttpClient client;
    ASSERT_TRUE(client.start());

    std::mutex mutex;
    std::condition_variable cv;
    int completed = 0;
    int succeeded = 0;

    const auto begin = steady_clock::now();
    for (int i = 0; i < REQUEST_COUNT; i++)
    {
        AsyncHttpRequest request;
        request.m_url = server.url("/subscriber/" + std::to_string(i));
        request.m_method = "POST";
        request.m_body = "payload";
        ASSERT_TRUE(client.submit(request,
            [&](const AsyncHttpResponse& response, const std::any&) {
                std::lock_guard<std::mutex> lock(mutex);
                completed++;
                if (response.m_httpStatus == 200)
                {
                    succeeded++;
                }
                cv.notify_all();
            }));
    }

    std::unique_lock<std::mutex> lock(mutex);
    ASSERT_TRUE(cv.wait_for(lock, seconds(5), [&] { return completed == REQUEST_COUNT; }));
    const auto elapsedMs = duration_cast<milliseconds>(steady_clock::now() - begin).count();

    EXPECT_EQ(succeeded, REQUEST_COUNT);
    // Serial execution would need >= 3 * SERVER_DELAY_MS (1500 ms); allow generous slack.
    EXPECT_LT(elapsedMs, 2 * SERVER_DELAY_MS + 400);

    client.stop();
}

TEST(AsyncHttpClientTest, ConnectionErrorReported)
{
    // Grab an ephemeral port and close it so nothing is listening there.
    int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    ASSERT_GE(fd, 0);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    ASSERT_EQ(::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)), 0);
    socklen_t len = sizeof(addr);
    ASSERT_EQ(::getsockname(fd, reinterpret_cast<sockaddr*>(&addr), &len), 0);
    const int deadPort = ntohs(addr.sin_port);
    ::close(fd);

    AsyncHttpClient client;
    ASSERT_TRUE(client.start());

    AsyncHttpRequest request;
    request.m_url = "http://127.0.0.1:" + std::to_string(deadPort) + "/unreachable";
    request.m_method = "POST";
    request.m_body = "x";
    request.m_timeoutMs = 3000;
    request.m_connectTimeoutMs = 2000;

    CallbackCapture capture;
    ASSERT_TRUE(client.submit(request,
        [&capture](const AsyncHttpResponse& r, const std::any& d) { capture.set(r, d); }));

    ASSERT_TRUE(capture.wait());
    EXPECT_FALSE(capture.m_response.transportOk());
    EXPECT_EQ(capture.m_response.m_httpStatus, 0);
    EXPECT_FALSE(capture.m_response.m_error.empty());

    client.stop();
}

TEST(AsyncHttpClientTest, DelayedSubmitHonored)
{
    constexpr int64_t DELAY_MS = 400;
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    AsyncHttpClient client;
    ASSERT_TRUE(client.start());

    AsyncHttpRequest request;
    request.m_url = server.url("/delayed");
    request.m_method = "POST";
    request.m_body = "retry-attempt";

    CallbackCapture capture;
    const auto begin = steady_clock::now();
    ASSERT_TRUE(client.submit(request,
        [&capture](const AsyncHttpResponse& r, const std::any& d) { capture.set(r, d); },
        {}, DELAY_MS));

    ASSERT_TRUE(capture.wait());
    const auto elapsedMs = duration_cast<milliseconds>(steady_clock::now() - begin).count();
    EXPECT_GE(elapsedMs, DELAY_MS - 50);
    EXPECT_EQ(capture.m_response.m_httpStatus, 200);

    client.stop();
}

TEST(AsyncHttpClientTest, StopAbortsOutstandingRequests)
{
    TinyHttpServer server(200, 2000);
    ASSERT_TRUE(server.start());

    AsyncHttpClient client;
    ASSERT_TRUE(client.start());

    AsyncHttpRequest request;
    request.m_url = server.url("/slow");
    request.m_method = "POST";
    request.m_body = "x";

    CallbackCapture capture;
    ASSERT_TRUE(client.submit(request,
        [&capture](const AsyncHttpResponse& r, const std::any& d) { capture.set(r, d); }));

    std::this_thread::sleep_for(milliseconds(200));
    client.stop();

    // stop() aborts outstanding transfers before returning.
    ASSERT_TRUE(capture.wait(100));
    EXPECT_EQ(capture.m_response.m_curlCode, CURLE_ABORTED_BY_CALLBACK);

    EXPECT_FALSE(client.isRunning());
}

TEST(AsyncHttpClientTest, SubmitRejectedWhenNotRunning)
{
    AsyncHttpClient client;

    AsyncHttpRequest request;
    request.m_url = "http://127.0.0.1:1/never";

    const bool accepted = client.submit(request,
        [](const AsyncHttpResponse&, const std::any&) { FAIL() << "callback must not fire"; });
    EXPECT_FALSE(accepted);
}
