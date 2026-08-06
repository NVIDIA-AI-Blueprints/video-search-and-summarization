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

#include <memory>
#include <mutex>
#include <vector>

#include "notification_manager.h"

/*
 * Fans one event stream out to several INotificationInterface backends, e.g.
 * a message broker plus webhooks. Forwarding uses each child's sendMessage(),
 * which only enqueues: every child keeps its own queue, connection state and
 * retry policy.
 */
class CompositeNotifier : public nv_vms::INotificationInterface
{
    // Keeps the constructor reachable from std::make_unique without making it
    // callable outside the class.
    struct PrivateTag
    {
    };

public:
    explicit CompositeNotifier(PrivateTag);
    virtual ~CompositeNotifier();

    CompositeNotifier(const CompositeNotifier&) = delete;
    CompositeNotifier& operator=(const CompositeNotifier&) = delete;

    static CompositeNotifier* getInstance();
    static void deleteInstance();

    // Notifiers are not owned. Idempotent: duplicates and nullptr are ignored.
    void addNotifier(nv_vms::INotificationInterface* notifier);
    size_t notifierCount() const;

    bool deliverMessage(Json::Value& message) override;
    void retryConnection() override;

private:
    mutable std::mutex m_notifiersMutex;
    std::vector<nv_vms::INotificationInterface*> m_notifiers;

    static std::unique_ptr<CompositeNotifier> _instance;
    static std::mutex _instanceMutex;
};
