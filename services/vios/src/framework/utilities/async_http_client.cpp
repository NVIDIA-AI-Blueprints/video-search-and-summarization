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

#include "async_http_client.h"

#include <utility>

#include "logger.h"

namespace
{
constexpr long DEFAULT_POLL_TIMEOUT_MS = 1000;
// Without curl_multi_wakeup() the loop must poll often enough to notice new submissions.
constexpr long LEGACY_POLL_TIMEOUT_MS = 200;
// Upper bound keeps the due-time arithmetic in submit() free of overflow.
constexpr int64_t MAX_SUBMIT_DELAY_MS = 24LL * 60 * 60 * 1000;

// Strips query parameters and userinfo so credentials or tokens embedded in a
// URL never reach the logs.
std::string loggableUrl(const std::string& url)
{
    std::string result = url.substr(0, url.find('?'));
    const size_t schemeEnd = result.find("://");
    if (schemeEnd != std::string::npos)
    {
        const size_t authorityEnd = result.find('/', schemeEnd + 3);
        const size_t userinfoEnd = result.find('@', schemeEnd + 3);
        if (userinfoEnd != std::string::npos &&
            (authorityEnd == std::string::npos || userinfoEnd < authorityEnd))
        {
            result.erase(schemeEnd + 3, userinfoEnd - schemeEnd - 2);
        }
    }
    return result;
}
}  // unnamed namespace

AsyncHttpClient::~AsyncHttpClient()
{
    try
    {
        stop();
    }
    catch (const std::exception& e)
    {
        LOG(error) << "AsyncHttpClient shutdown failed: " << e.what() << endl;
    }
    catch (...)
    {
        LOG(error) << "AsyncHttpClient shutdown failed" << endl;
    }
}

bool AsyncHttpClient::start()
{
    if (m_running)
    {
        LOG(warning) << "AsyncHttpClient already running" << endl;
        return true;
    }

    m_multiHandle = curl_multi_init();
    if (m_multiHandle == nullptr)
    {
        LOG(error) << "curl_multi_init failed" << endl;
        return false;
    }

    m_stopRequested = false;
    m_running = true;
    m_loopThread = std::thread(&AsyncHttpClient::loopThreadFunc, this);
    return true;
}

void AsyncHttpClient::stop()
{
    if (!m_running)
    {
        return;
    }

    m_stopRequested = true;
    wakeupLoop();
    if (m_loopThread.joinable())
    {
        m_loopThread.join();
    }
    m_running = false;

    // Requests submitted while the loop was exiting are aborted here.
    abortOutstanding();

    curl_multi_cleanup(m_multiHandle);
    m_multiHandle = nullptr;
}

bool AsyncHttpClient::submit(AsyncHttpRequest request, OnComplete onComplete,
                             std::any userData, int64_t delayMs)
{
    if (!m_running)
    {
        LOG(error) << "AsyncHttpClient not running, dropping request to "
                   << loggableUrl(request.m_url) << endl;
        return false;
    }
    if (request.m_url.empty() || request.m_method.empty())
    {
        LOG(error) << "AsyncHttpClient request rejected: empty url or method" << endl;
        return false;
    }
    if (delayMs < 0)
    {
        delayMs = 0;
    }
    if (delayMs > MAX_SUBMIT_DELAY_MS)
    {
        LOG(warning) << "AsyncHttpClient delay clamped to " << MAX_SUBMIT_DELAY_MS << " ms" << endl;
        delayMs = MAX_SUBMIT_DELAY_MS;
    }

    auto ctx = std::make_unique<RequestContext>();
    ctx->m_request = std::move(request);
    ctx->m_onComplete = std::move(onComplete);
    ctx->m_userData = std::move(userData);

    const Clock::time_point due = Clock::now() + std::chrono::milliseconds(delayMs);
    {
        std::lock_guard<std::mutex> lock(m_pendingMutex);
        m_pending.emplace(due, std::move(ctx));
    }
    wakeupLoop();
    return true;
}

void AsyncHttpClient::loopThreadFunc()
{
    LOG(info) << "AsyncHttpClient loop thread started" << endl;
    while (!m_stopRequested)
    {
        startDueRequests();

        int stillRunning = 0;
        const CURLMcode perfCode = curl_multi_perform(m_multiHandle, &stillRunning);
        if (perfCode != CURLM_OK)
        {
            LOG(error) << "curl_multi_perform failed: " << curl_multi_strerror(perfCode) << endl;
        }

        harvestCompletedTransfers();

        pollTransfers();
    }
    abortOutstanding();
    LOG(info) << "AsyncHttpClient loop thread exited" << endl;
}

void AsyncHttpClient::startDueRequests()
{
    std::vector<std::unique_ptr<RequestContext>> due;
    {
        std::lock_guard<std::mutex> lock(m_pendingMutex);
        const Clock::time_point now = Clock::now();
        auto it = m_pending.begin();
        while (it != m_pending.end() && it->first <= now)
        {
            due.push_back(std::move(it->second));
            it = m_pending.erase(it);
        }
    }
    for (auto& ctx : due)
    {
        startRequest(std::move(ctx));
    }
}

void AsyncHttpClient::startRequest(std::unique_ptr<RequestContext> ctx)
{
    CURL* easy = curl_easy_init();
    if (easy == nullptr)
    {
        LOG(error) << "curl_easy_init failed for " << loggableUrl(ctx->m_request.m_url) << endl;
        AsyncHttpResponse response;
        response.m_curlCode = CURLE_FAILED_INIT;
        response.m_error = "curl_easy_init failed";
        deliver(*ctx, response);
        return;
    }

    const AsyncHttpRequest& req = ctx->m_request;
    CURLcode rc = CURLE_OK;
    auto setopt = [&rc, easy](CURLoption opt, auto value) {
        if (rc == CURLE_OK)
        {
            rc = curl_easy_setopt(easy, opt, value);
        }
    };

    setopt(CURLOPT_URL, req.m_url.c_str());
    setopt(CURLOPT_CUSTOMREQUEST, req.m_method.c_str());
    setopt(CURLOPT_NOSIGNAL, 1L);
    // Webhook receivers are HTTP(S) endpoints; never let a configured URL reach
    // other libcurl protocols such as file:// or ftp://.
#if CURL_AT_LEAST_VERSION(7, 85, 0)
    setopt(CURLOPT_PROTOCOLS_STR, "http,https");
    setopt(CURLOPT_REDIR_PROTOCOLS_STR, "http,https");
#else
    setopt(CURLOPT_PROTOCOLS, static_cast<long>(CURLPROTO_HTTP | CURLPROTO_HTTPS));
    setopt(CURLOPT_REDIR_PROTOCOLS, static_cast<long>(CURLPROTO_HTTP | CURLPROTO_HTTPS));
#endif
    setopt(CURLOPT_TIMEOUT_MS, req.m_timeoutMs);
    setopt(CURLOPT_CONNECTTIMEOUT_MS, req.m_connectTimeoutMs);
    setopt(CURLOPT_SSL_VERIFYPEER, req.m_verifyTls ? 1L : 0L);
    setopt(CURLOPT_SSL_VERIFYHOST, req.m_verifyTls ? 2L : 0L);
    setopt(CURLOPT_WRITEFUNCTION, &AsyncHttpClient::writeCallback);
    setopt(CURLOPT_WRITEDATA, ctx.get());
    setopt(CURLOPT_ERRORBUFFER, ctx->m_errorBuffer);

    if (!req.m_body.empty())
    {
        // The body buffer stays valid for the transfer lifetime: ctx owns the
        // request and is kept in m_inflight until the handle completes.
        setopt(CURLOPT_POSTFIELDS, req.m_body.c_str());
        setopt(CURLOPT_POSTFIELDSIZE, static_cast<long>(req.m_body.size()));
    }

    for (const std::string& header : req.m_headers)
    {
        ctx->m_headerList = curl_slist_append(ctx->m_headerList, header.c_str());
    }
    if (ctx->m_headerList != nullptr)
    {
        setopt(CURLOPT_HTTPHEADER, ctx->m_headerList);
    }

    if (rc != CURLE_OK)
    {
        LOG(error) << "curl_easy_setopt failed for " << loggableUrl(req.m_url) << ": "
                   << curl_easy_strerror(rc) << endl;
        curl_easy_cleanup(easy);
        AsyncHttpResponse response;
        response.m_curlCode = rc;
        response.m_error = curl_easy_strerror(rc);
        deliver(*ctx, response);
        return;
    }

    const CURLMcode mc = curl_multi_add_handle(m_multiHandle, easy);
    if (mc != CURLM_OK)
    {
        LOG(error) << "curl_multi_add_handle failed for " << loggableUrl(req.m_url) << ": "
                   << curl_multi_strerror(mc) << endl;
        curl_easy_cleanup(easy);
        AsyncHttpResponse response;
        response.m_curlCode = CURLE_FAILED_INIT;
        response.m_error = curl_multi_strerror(mc);
        deliver(*ctx, response);
        return;
    }

    m_inflight.emplace(easy, std::move(ctx));
}

void AsyncHttpClient::harvestCompletedTransfers()
{
    int msgsLeft = 0;
    CURLMsg* msg = nullptr;
    while ((msg = curl_multi_info_read(m_multiHandle, &msgsLeft)) != nullptr)
    {
        if (msg->msg != CURLMSG_DONE)
        {
            continue;
        }

        CURL* easy = msg->easy_handle;
        const CURLcode result = msg->data.result;

        auto it = m_inflight.find(easy);
        if (it == m_inflight.end())
        {
            LOG(error) << "Completed transfer has no request context" << endl;
            curl_multi_remove_handle(m_multiHandle, easy);
            curl_easy_cleanup(easy);
            continue;
        }
        std::unique_ptr<RequestContext> ctx = std::move(it->second);
        m_inflight.erase(it);

        AsyncHttpResponse response;
        response.m_curlCode = result;
        if (result == CURLE_OK)
        {
            long httpStatus = 0;
            curl_easy_getinfo(easy, CURLINFO_RESPONSE_CODE, &httpStatus);
            response.m_httpStatus = httpStatus;
            response.m_body = std::move(ctx->m_responseBody);
        }
        else
        {
            response.m_error = (ctx->m_errorBuffer[0] != '\0') ? ctx->m_errorBuffer
                                                               : curl_easy_strerror(result);
            LOG(error) << "Transfer to " << loggableUrl(ctx->m_request.m_url) << " failed: "
                       << response.m_error << endl;
        }

        curl_multi_remove_handle(m_multiHandle, easy);
        curl_easy_cleanup(easy);

        deliver(*ctx, response);
    }
}

void AsyncHttpClient::abortOutstanding()
{
    for (auto& [easy, ctx] : m_inflight)
    {
        curl_multi_remove_handle(m_multiHandle, easy);
        curl_easy_cleanup(easy);

        AsyncHttpResponse response;
        response.m_curlCode = CURLE_ABORTED_BY_CALLBACK;
        response.m_error = "aborted: AsyncHttpClient stopped";
        deliver(*ctx, response);
    }
    m_inflight.clear();

    std::multimap<Clock::time_point, std::unique_ptr<RequestContext>> pending;
    {
        std::lock_guard<std::mutex> lock(m_pendingMutex);
        pending.swap(m_pending);
    }
    for (auto& [due, ctx] : pending)
    {
        (void)due;
        AsyncHttpResponse response;
        response.m_curlCode = CURLE_ABORTED_BY_CALLBACK;
        response.m_error = "aborted: AsyncHttpClient stopped";
        deliver(*ctx, response);
    }
}

void AsyncHttpClient::pollTransfers()
{
    const long timeoutMs = pollTimeoutMs();
    int numFds = 0;
#if CURL_AT_LEAST_VERSION(7, 66, 0)
    const CURLMcode mc = curl_multi_poll(m_multiHandle, nullptr, 0, static_cast<int>(timeoutMs), &numFds);
#else
    const CURLMcode mc = curl_multi_wait(m_multiHandle, nullptr, 0, static_cast<int>(timeoutMs), &numFds);
#endif
    if (mc != CURLM_OK)
    {
        LOG(error) << "curl_multi_poll failed: " << curl_multi_strerror(mc) << endl;
    }
}

long AsyncHttpClient::pollTimeoutMs()
{
#if CURL_AT_LEAST_VERSION(7, 68, 0)
    long timeout = DEFAULT_POLL_TIMEOUT_MS;
#else
    long timeout = LEGACY_POLL_TIMEOUT_MS;
#endif
    std::lock_guard<std::mutex> lock(m_pendingMutex);
    if (!m_pending.empty())
    {
        const auto untilDue = std::chrono::duration_cast<std::chrono::milliseconds>(
            m_pending.begin()->first - Clock::now()).count();
        timeout = std::max(0L, std::min(timeout, static_cast<long>(untilDue)));
    }
    return timeout;
}

void AsyncHttpClient::wakeupLoop()
{
#if CURL_AT_LEAST_VERSION(7, 68, 0)
    if (m_multiHandle != nullptr)
    {
        curl_multi_wakeup(m_multiHandle);
    }
#endif
    // Older libcurl: the loop polls with a short timeout and notices new work itself.
}

void AsyncHttpClient::deliver(const RequestContext& ctx, const AsyncHttpResponse& response)
{
    if (!ctx.m_onComplete)
    {
        return;
    }
    try
    {
        ctx.m_onComplete(response, ctx.m_userData);
    }
    catch (const std::exception& e)
    {
        LOG(error) << "AsyncHttpClient completion callback threw: " << e.what() << endl;
    }
    catch (...)
    {
        LOG(error) << "AsyncHttpClient completion callback threw" << endl;
    }
}

size_t AsyncHttpClient::writeCallback(char* data, size_t size, size_t nmemb, void* userp)
{
    auto* ctx = static_cast<RequestContext*>(userp);
    const size_t total = size * nmemb;
    try
    {
        ctx->m_responseBody.append(data, total);
    }
    catch (...)
    {
        return 0;  // signals CURLE_WRITE_ERROR to libcurl without propagating through C frames
    }
    return total;
}
