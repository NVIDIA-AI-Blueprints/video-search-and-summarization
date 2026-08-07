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

#include <any>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <curl/curl.h>

struct AsyncHttpRequest
{
    std::string m_url;
    std::string m_method{"POST"};        // "POST", "PUT", "DELETE" (any HTTP verb accepted)
    std::string m_body;                  // request payload, may be empty
    std::vector<std::string> m_headers;  // e.g. {"Content-Type: application/json", "Authorization: Bearer x"}
    long m_timeoutMs{10000};             // total transfer timeout per attempt
    long m_connectTimeoutMs{5000};
    bool m_verifyTls{true};              // set false only for receivers with self-signed certificates
};

struct AsyncHttpResponse
{
    int m_curlCode{CURLE_OK};   // transport result (CURLcode); CURLE_OK means the HTTP exchange completed
    long m_httpStatus{0};       // HTTP status code, 0 when the transport failed
    std::string m_body;         // response payload
    std::string m_error;        // transport error description when m_curlCode != CURLE_OK

    [[nodiscard]] bool transportOk() const { return m_curlCode == CURLE_OK; }
};

/*
 * Non-blocking HTTP client built on the libcurl multi interface.
 *
 * One internal loop thread drives all in-flight transfers concurrently; submit()
 * only enqueues and returns immediately. The completion callback runs on the loop
 * thread and must not block; offload heavy work to another thread. Caller context
 * travels through std::any (or capture state in the callback lambda directly).
 *
 * curl_global_init() is expected to have run already (done once in server.cpp).
 */
class AsyncHttpClient
{
public:
    using OnComplete = std::function<void(const AsyncHttpResponse& response, const std::any& userData)>;

    AsyncHttpClient() = default;
    ~AsyncHttpClient();

    AsyncHttpClient(const AsyncHttpClient&) = delete;
    AsyncHttpClient& operator=(const AsyncHttpClient&) = delete;

    [[nodiscard]] bool start();

    /*
     * Stops the loop thread. Outstanding requests (pending and in-flight) receive
     * their callback with m_curlCode = CURLE_ABORTED_BY_CALLBACK before this returns.
     */
    void stop();

    [[nodiscard]] bool isRunning() const { return m_running; }

    /*
     * Enqueue a request; returns immediately. delayMs postpones the transfer start
     * (hook for caller-driven retry backoff). Returns false if the client is not
     * running or the request is malformed; the callback is not invoked in that case.
     */
    [[nodiscard]] bool submit(AsyncHttpRequest request, OnComplete onComplete,
                              std::any userData = {}, int64_t delayMs = 0);

private:
    struct RequestContext
    {
        AsyncHttpRequest m_request;
        OnComplete m_onComplete;
        std::any m_userData;
        struct curl_slist* m_headerList{nullptr};
        std::string m_responseBody;
        char m_errorBuffer[CURL_ERROR_SIZE]{};

        RequestContext() = default;
        RequestContext(const RequestContext&) = delete;
        RequestContext& operator=(const RequestContext&) = delete;

        ~RequestContext()
        {
            if (m_headerList != nullptr)
            {
                curl_slist_free_all(m_headerList);
            }
        }
    };

    using Clock = std::chrono::steady_clock;

    void loopThreadFunc();
    void startDueRequests();
    void startRequest(std::unique_ptr<RequestContext> ctx);
    void harvestCompletedTransfers();
    void abortOutstanding();
    void pollTransfers();
    void wakeupLoop();
    [[nodiscard]] long pollTimeoutMs();
    static void deliver(const RequestContext& ctx, const AsyncHttpResponse& response);
    static size_t writeCallback(char* data, size_t size, size_t nmemb, void* userp);

    CURLM* m_multiHandle{nullptr};
    std::thread m_loopThread;
    std::atomic<bool> m_running{false};
    std::atomic<bool> m_stopRequested{false};

    std::mutex m_pendingMutex;
    // Pending requests keyed by the time they become due; drained by the loop thread.
    std::multimap<Clock::time_point, std::unique_ptr<RequestContext>> m_pending;

    // In-flight transfers; touched only by the loop thread.
    std::map<CURL*, std::unique_ptr<RequestContext>> m_inflight;
};
