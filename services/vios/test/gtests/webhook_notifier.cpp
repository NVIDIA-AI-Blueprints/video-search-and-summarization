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
 * @file webhook_notifier.cpp
 * @brief WebhookNotifier verification against the shared in-process TinyHttpServer.
 *
 * Exercises the notification_config.json webhooks schema: the global enabled
 * switch over the items array, alert-type trigger keys, receiver fan-out over
 * the request array with a non-blocking deliverMessage, per-receiver
 * camera_type filtering, retry_on_status and backoff handling, header
 * templating, per-receiver user_defined_metadata merging into event.metadata,
 * and the deferred HMAC signature header (must be absent).
 */

#include "gtest/gtest.h"

#include <chrono>
#include <string>
#include <thread>
#include <vector>

#include <jsoncpp/json/json.h>

#include "webhook/webhook_notifier.h"
#include "utils/tiny_http_server.h"

using namespace std::chrono;

namespace
{

Json::Value makeRequest(const std::string& url, const std::string& method = "POST",
                        int maxAttempts = 1, const std::vector<int>& backoffMs = {},
                        const std::vector<int>& retryOnStatus = {},
                        const std::vector<std::string>& cameraTypes = {})
{
    Json::Value request;
    request["url"] = url;
    request["method"] = method;
    request["headers"]["Content-Type"] = "application/json";
    request["query_params"] = Json::Value(Json::objectValue);
    if (!cameraTypes.empty())
    {
        request["camera_type"] = Json::Value(Json::arrayValue);
        for (const std::string& cameraType : cameraTypes)
        {
            request["camera_type"].append(cameraType);
        }
    }
    request["timeout_ms"] = 3000;
    request["retry"]["max_attempts"] = maxAttempts;
    request["retry"]["backoff_ms"] = Json::Value(Json::arrayValue);
    for (const int backoff : backoffMs)
    {
        request["retry"]["backoff_ms"].append(backoff);
    }
    request["retry"]["retry_on_status"] = Json::Value(Json::arrayValue);
    for (const int status : retryOnStatus)
    {
        request["retry"]["retry_on_status"].append(status);
    }
    return request;
}

Json::Value makeWebhook(const std::string& alertType, const std::string& filterValue,
                        const std::vector<Json::Value>& requests)
{
    Json::Value webhook;
    webhook["enabled"] = true;
    webhook[alertType] = filterValue;
    webhook["request"] = Json::Value(Json::arrayValue);
    for (const Json::Value& request : requests)
    {
        webhook["request"].append(request);
    }
    return webhook;
}

Json::Value makeConfig(const std::vector<Json::Value>& webhooks)
{
    Json::Value config;
    config["webhooks"]["enabled"] = true;
    config["webhooks"]["items"] = Json::Value(Json::arrayValue);
    for (const Json::Value& webhook : webhooks)
    {
        config["webhooks"]["items"].append(webhook);
    }
    return config;
}

Json::Value makeCameraEvent(const std::string& change, const std::string& cameraId = "cam-1")
{
    Json::Value message;
    message["alert_type"] = "camera_status_change";
    message["source"] = "vst";
    message["created_at"] = "2026-01-01T00:00:00Z";
    message["event"]["camera_id"] = cameraId;
    message["event"]["camera_name"] = "test-camera";
    message["event"]["change"] = change;
    return message;
}

bool waitForRequestCount(const TinyHttpServer& server, size_t count, int timeoutMs = 5000)
{
    const auto deadline = steady_clock::now() + milliseconds(timeoutMs);
    while (steady_clock::now() < deadline)
    {
        if (server.requests().size() >= count)
        {
            return true;
        }
        std::this_thread::sleep_for(milliseconds(20));
    }
    return server.requests().size() >= count;
}

size_t countRequestsForPath(const TinyHttpServer& server, const std::string& path)
{
    size_t count = 0;
    for (const auto& request : server.requests())
    {
        if (request.m_path == path)
        {
            count++;
        }
    }
    return count;
}

}  // unnamed namespace

TEST(WebhookNotifierTest, MatchesAlertTypeTriggerAndFilterValue)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    WebhookNotifier notifier(makeConfig({
        makeWebhook("camera_status_change", "camera_add", {makeRequest(server.url("/add"))}),
        makeWebhook("camera_status_change", "camera_remove", {makeRequest(server.url("/remove"))}),
    }));
    ASSERT_EQ(notifier.webhookCount(), 2u);

    Json::Value message = makeCameraEvent("camera_add");
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 1));
    // Let any spurious extra deliveries land before counting.
    std::this_thread::sleep_for(milliseconds(300));

    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 1u);
    EXPECT_EQ(seen[0].m_method, "POST");
    EXPECT_EQ(seen[0].m_path, "/add");

    // No configured id: webhook_id is empty, the rest matches verbatim.
    Json::Value receivedBody;
    ASSERT_TRUE(Json::Reader().parse(seen[0].m_body, receivedBody));
    EXPECT_EQ(receivedBody["webhook_id"].asString(), "");
    receivedBody.removeMember("webhook_id");
    EXPECT_EQ(receivedBody, message);

    // An event with a different alert_type matches no webhook at all.
    Json::Value serviceEvent;
    serviceEvent["alert_type"] = "service_status_change";
    serviceEvent["event"]["service_status"] = "init_ready";
    EXPECT_TRUE(notifier.deliverMessage(serviceEvent));
    std::this_thread::sleep_for(milliseconds(300));
    EXPECT_EQ(server.requests().size(), 1u);
}

TEST(WebhookNotifierTest, ConfiguredIdIsTaggedIntoDeliveredBody)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    Json::Value tagged = makeWebhook("camera_status_change", "camera_add",
                                     {makeRequest(server.url("/tagged"))});
    tagged["id"] = "wh-001";
    Json::Value untagged = makeWebhook("camera_status_change", "camera_add",
                                       {makeRequest(server.url("/untagged"))});

    WebhookNotifier notifier(makeConfig({tagged, untagged}));

    Json::Value message = makeCameraEvent("camera_add");
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 2));
    std::this_thread::sleep_for(milliseconds(300));

    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 2u);
    for (const auto& request : seen)
    {
        Json::Value body;
        ASSERT_TRUE(Json::Reader().parse(request.m_body, body));
        if (request.m_path == "/tagged")
        {
            EXPECT_EQ(body["webhook_id"].asString(), "wh-001");
        }
        else
        {
            EXPECT_EQ(body["webhook_id"].asString(), "");
            body.removeMember("webhook_id");
            EXPECT_EQ(body, message);
        }
    }
}

TEST(WebhookNotifierTest, FansOutToAllReceiversWithoutBlocking)
{
    constexpr int SERVER_DELAY_MS = 500;
    TinyHttpServer server(200, SERVER_DELAY_MS);
    ASSERT_TRUE(server.start());

    // One webhook with two receivers plus one non-matching webhook.
    WebhookNotifier notifier(makeConfig({
        makeWebhook("camera_status_change", "camera_add",
                    {makeRequest(server.url("/a")), makeRequest(server.url("/b"))}),
        makeWebhook("camera_status_change", "camera_remove", {makeRequest(server.url("/c"))}),
    }));

    Json::Value message = makeCameraEvent("camera_add");
    const auto begin = steady_clock::now();
    EXPECT_TRUE(notifier.deliverMessage(message));
    const auto elapsedMs = duration_cast<milliseconds>(steady_clock::now() - begin).count();

    // deliverMessage only enqueues; it must not wait on the slow receivers.
    EXPECT_LT(elapsedMs, SERVER_DELAY_MS / 2);

    ASSERT_TRUE(waitForRequestCount(server, 2));
    std::this_thread::sleep_for(milliseconds(300));
    EXPECT_EQ(countRequestsForPath(server, "/a"), 1u);
    EXPECT_EQ(countRequestsForPath(server, "/b"), 1u);
    EXPECT_EQ(countRequestsForPath(server, "/c"), 0u);
}

TEST(WebhookNotifierTest, RetryOnStatusIsJudgedPerReceiver)
{
    // Both servers answer 503. The listed receiver retries once more; the
    // receiver whose retry_on_status does not list 503 fails permanently.
    TinyHttpServer listedServer(503);
    TinyHttpServer unlistedServer(503);
    ASSERT_TRUE(listedServer.start());
    ASSERT_TRUE(unlistedServer.start());

    WebhookNotifier notifier(makeConfig({
        makeWebhook("camera_status_change", "camera_add",
                    {makeRequest(listedServer.url("/listed"), "POST", 2, {100}, {503}),
                     makeRequest(unlistedServer.url("/unlisted"), "POST", 3, {100}, {500})}),
    }));

    Json::Value message = makeCameraEvent("camera_add");
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(listedServer, 2));
    std::this_thread::sleep_for(milliseconds(400));
    EXPECT_EQ(listedServer.requests().size(), 2u);
    EXPECT_EQ(unlistedServer.requests().size(), 1u);
}

TEST(WebhookNotifierTest, RetriesWithBackoffUntilSuccess)
{
    constexpr int FIRST_BACKOFF_MS = 100;
    constexpr int SECOND_BACKOFF_MS = 300;
    TinyHttpServer server;
    ASSERT_TRUE(server.start());
    server.setStatusSequence({500, 502, 200});

    WebhookNotifier notifier(makeConfig({
        makeWebhook("camera_status_change", "camera_add",
                    {makeRequest(server.url("/retry"), "POST", 3,
                                 {FIRST_BACKOFF_MS, SECOND_BACKOFF_MS}, {500, 502})}),
    }));

    Json::Value message = makeCameraEvent("camera_add");
    const auto begin = steady_clock::now();
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 3));
    const auto elapsedMs = duration_cast<milliseconds>(steady_clock::now() - begin).count();
    // The third attempt cannot start before both backoff delays have elapsed.
    EXPECT_GE(elapsedMs, FIRST_BACKOFF_MS + SECOND_BACKOFF_MS - 50);

    // The 200 on attempt three ends the retry loop.
    std::this_thread::sleep_for(milliseconds(400));
    EXPECT_EQ(server.requests().size(), 3u);
}

TEST(WebhookNotifierTest, GivesUpAfterMaxAttempts)
{
    TinyHttpServer server(503);
    ASSERT_TRUE(server.start());

    WebhookNotifier notifier(makeConfig({
        makeWebhook("camera_status_change", "camera_add",
                    {makeRequest(server.url("/fail"), "POST", 2, {50}, {503})}),
    }));

    Json::Value message = makeCameraEvent("camera_add");
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 2));
    // max_attempts exhausted: no further request may arrive.
    std::this_thread::sleep_for(milliseconds(400));
    EXPECT_EQ(server.requests().size(), 2u);
}

TEST(WebhookNotifierTest, TemplatedHeadersRenderAndHmacSignatureStaysAbsent)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    Json::Value request = makeRequest(server.url("/hook"));
    request["headers"]["streamId"] = "{{event.camera_id}}";
    request["headers"]["Authorization"] = "Bearer {{secrets.token}}";
    request["query_params"]["camera"] = "{{event.camera_id}}";

    Json::Value webhook = makeWebhook("camera_status_change", "camera_add", {request});
    webhook["auth"]["type"] = "hmac-sha256";
    webhook["auth"]["secret"] = "{{secrets.signing_key}}";
    webhook["auth"]["header_name"] = "X-VIOS-Signature";

    WebhookNotifier notifier(makeConfig({webhook}));

    Json::Value message = makeCameraEvent("camera_add", "cam-42");
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 1));
    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 1u);

    // Templates resolve against the event payload, in headers and query params.
    EXPECT_EQ(seen[0].m_path, "/hook?camera=cam-42");
    ASSERT_NE(seen[0].m_headers.find("streamId"), seen[0].m_headers.end());
    EXPECT_EQ(seen[0].m_headers.at("streamId"), "cam-42");
    ASSERT_NE(seen[0].m_headers.find("Content-Type"), seen[0].m_headers.end());
    EXPECT_EQ(seen[0].m_headers.at("Content-Type"), "application/json");

    // {{secrets.*}} cannot be resolved yet: the credential header is omitted
    // rather than sent half-rendered.
    EXPECT_EQ(seen[0].m_headers.find("Authorization"), seen[0].m_headers.end());

    // HMAC signing is deliberately not implemented; no signature header may
    // be fabricated. Flip this expectation when hmac-sha256 support lands.
    EXPECT_EQ(seen[0].m_headers.find("X-VIOS-Signature"), seen[0].m_headers.end());
}

TEST(WebhookNotifierTest, HeaderValuesAreSanitizedAgainstCrlfInjection)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    Json::Value request = makeRequest(server.url("/hook"));
    request["headers"]["streamId"] = "{{event.camera_id}}";

    WebhookNotifier notifier(
        makeConfig({makeWebhook("camera_status_change", "camera_add", {request})}));

    // A sensor id carrying CRLF must not smuggle an extra header line.
    Json::Value message = makeCameraEvent("camera_add", "cam-1\r\nX-Evil: injected");
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 1));
    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 1u);
    EXPECT_EQ(seen[0].m_headers.find("X-Evil"), seen[0].m_headers.end());
    ASSERT_NE(seen[0].m_headers.find("streamId"), seen[0].m_headers.end());
    EXPECT_EQ(seen[0].m_headers.at("streamId"), "cam-1X-Evil: injected");
}

TEST(WebhookNotifierTest, UserDefinedMetadataMergesIntoEventMetadata)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    // Receiver with user metadata, including a key colliding with the event's
    // own metadata, plus nested JSON; a second receiver defines none.
    Json::Value withMetadata = makeRequest(server.url("/with"));
    withMetadata["user_defined_metadata"]["model"] = "some_model_name";
    withMetadata["user_defined_metadata"]["chunk"] = 123;
    withMetadata["user_defined_metadata"]["codec"] = "user-override";
    withMetadata["user_defined_metadata"]["nested"]["a"] = true;
    Json::Value without = makeRequest(server.url("/without"));

    WebhookNotifier notifier(
        makeConfig({makeWebhook("camera_status_change", "camera_add", {withMetadata, without})}));

    Json::Value message = makeCameraEvent("camera_add");
    message["event"]["metadata"]["codec"] = "h264";
    message["event"]["metadata"]["framerate"] = 30;
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 2));
    std::this_thread::sleep_for(milliseconds(300));

    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 2u);
    for (const auto& request : seen)
    {
        Json::Value body;
        ASSERT_TRUE(Json::Reader().parse(request.m_body, body));
        const Json::Value& metadata = body["event"]["metadata"];
        if (request.m_path == "/with")
        {
            // User keys are added, collisions favor the user, others survive.
            EXPECT_EQ(metadata["model"].asString(), "some_model_name");
            EXPECT_EQ(metadata["chunk"].asInt(), 123);
            EXPECT_EQ(metadata["codec"].asString(), "user-override");
            EXPECT_TRUE(metadata["nested"]["a"].asBool());
            EXPECT_EQ(metadata["framerate"].asInt(), 30);
        }
        else
        {
            // The receiver without user metadata gets the event verbatim.
            EXPECT_EQ(metadata["codec"].asString(), "h264");
            EXPECT_FALSE(metadata.isMember("model"));
        }
    }
}

TEST(WebhookNotifierTest, UserDefinedMetadataCreatesMissingMetadataObject)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    Json::Value request = makeRequest(server.url("/hook"));
    request["user_defined_metadata"]["model"] = "some_model_name";

    WebhookNotifier notifier(
        makeConfig({makeWebhook("camera_status_change", "camera_add", {request})}));

    // makeCameraEvent carries no event.metadata at all.
    Json::Value message = makeCameraEvent("camera_add");
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 1));
    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 1u);
    Json::Value body;
    ASSERT_TRUE(Json::Reader().parse(seen[0].m_body, body));
    ASSERT_TRUE(body["event"]["metadata"].isObject());
    EXPECT_EQ(body["event"]["metadata"]["model"].asString(), "some_model_name");
    // The rest of the event is untouched.
    EXPECT_EQ(body["event"]["camera_id"].asString(), "cam-1");
}

TEST(WebhookNotifierTest, NonObjectUserDefinedMetadataIsIgnored)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    Json::Value request = makeRequest(server.url("/hook"));
    request["user_defined_metadata"] = "not-an-object";

    WebhookNotifier notifier(
        makeConfig({makeWebhook("camera_status_change", "camera_add", {request})}));
    EXPECT_EQ(notifier.webhookCount(), 1u);

    Json::Value message = makeCameraEvent("camera_add");
    EXPECT_TRUE(notifier.deliverMessage(message));

    ASSERT_TRUE(waitForRequestCount(server, 1));
    const auto seen = server.requests();
    ASSERT_EQ(seen.size(), 1u);
    Json::Value body;
    ASSERT_TRUE(Json::Reader().parse(seen[0].m_body, body));
    // The malformed field must not leak into the body in any form.
    EXPECT_FALSE(body["event"].isMember("metadata"));
    body.removeMember("webhook_id");
    EXPECT_EQ(body, message);
}

TEST(WebhookNotifierTest, DisabledAndMalformedWebhooksAreSkipped)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    Json::Value disabledWebhook =
        makeWebhook("camera_status_change", "camera_add", {makeRequest(server.url("/off"))});
    disabledWebhook["enabled"] = false;

    // Enabled entry whose only receiver has no url.
    Json::Value urlLessWebhook = makeWebhook("camera_status_change", "camera_add", {makeRequest("")});

    // Enabled entry triggering on an alert type the notifier does not know.
    Json::Value unknownAlertWebhook =
        makeWebhook("unknown_alert_type", "some_value", {makeRequest(server.url("/unknown"))});

    // Enabled entry whose only receiver lacks the mandatory method field.
    Json::Value methodLessRequest = makeRequest(server.url("/nomethod"));
    methodLessRequest.removeMember("method");
    Json::Value methodLessWebhook =
        makeWebhook("camera_status_change", "camera_add", {methodLessRequest});

    WebhookNotifier notifier(
        makeConfig({disabledWebhook, urlLessWebhook, unknownAlertWebhook, methodLessWebhook}));
    EXPECT_EQ(notifier.webhookCount(), 0u);

    Json::Value message = makeCameraEvent("camera_add");
    EXPECT_TRUE(notifier.deliverMessage(message));
    std::this_thread::sleep_for(milliseconds(200));
    EXPECT_TRUE(server.requests().empty());
}

TEST(WebhookNotifierTest, CameraTypeFilterGatesReceivers)
{
    TinyHttpServer server;
    ASSERT_TRUE(server.start());

    WebhookNotifier notifier(makeConfig({makeWebhook(
        "camera_status_change", "camera_add",
        {makeRequest(server.url("/file-only"), "POST", 1, {}, {}, {"file"}),
         makeRequest(server.url("/rtsp-only"), "POST", 1, {}, {}, {"rtsp"}),
         makeRequest(server.url("/all"))})}));

    // A file event reaches the file receiver and the unfiltered receiver.
    Json::Value fileEvent = makeCameraEvent("camera_add");
    fileEvent["event"]["camera_type"] = "file";
    EXPECT_TRUE(notifier.deliverMessage(fileEvent));
    ASSERT_TRUE(waitForRequestCount(server, 2));

    // An event without camera_type only reaches the unfiltered receiver.
    Json::Value untypedEvent = makeCameraEvent("camera_add");
    EXPECT_TRUE(notifier.deliverMessage(untypedEvent));
    ASSERT_TRUE(waitForRequestCount(server, 3));

    std::this_thread::sleep_for(milliseconds(300));
    EXPECT_EQ(countRequestsForPath(server, "/file-only"), 1u);
    EXPECT_EQ(countRequestsForPath(server, "/rtsp-only"), 0u);
    EXPECT_EQ(countRequestsForPath(server, "/all"), 2u);
}

TEST(WebhookNotifierTest, GloballyDisabledBlockLoadsNoWebhooks)
{
    Json::Value config = makeConfig(
        {makeWebhook("camera_status_change", "camera_add", {makeRequest("http://127.0.0.1:9/off")})});
    config["webhooks"]["enabled"] = false;

    WebhookNotifier notifier(config);
    EXPECT_EQ(notifier.webhookCount(), 0u);
}

TEST(WebhookNotifierTest, MissingGlobalEnabledFlagDisablesWebhooks)
{
    Json::Value config = makeConfig(
        {makeWebhook("camera_status_change", "camera_add", {makeRequest("http://127.0.0.1:9/off")})});
    config["webhooks"].removeMember("enabled");

    WebhookNotifier notifier(config);
    EXPECT_EQ(notifier.webhookCount(), 0u);
}

TEST(WebhookNotifierTest, LegacyFlatWebhookArrayIsRejected)
{
    // The pre-items schema: webhooks as a bare array of enabled entries.
    Json::Value config;
    config["webhooks"] = Json::Value(Json::arrayValue);
    config["webhooks"].append(makeWebhook("camera_status_change", "camera_add",
                                          {makeRequest("http://127.0.0.1:9/legacy")}));

    WebhookNotifier notifier(config);
    EXPECT_EQ(notifier.webhookCount(), 0u);
}
