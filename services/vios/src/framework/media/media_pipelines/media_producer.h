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

#pragma once

#include <memory>
#include <string>
#include <map>
#include <functional>
#include "media_consumer.h"

/**
 * @brief Interface for media data producers that can provide data to consumers
 * 
 * This interface defines the contract for all media data producers in the pipeline.
 * Producers are responsible for generating or sourcing media data and distributing
 * it to registered consumers.
 */
class IMediaDataProducer : public std::enable_shared_from_this<IMediaDataProducer>
{
public:
    virtual ~IMediaDataProducer() = default;

    /**
     * @brief Register a consumer to receive data from this producer
     * @param consumer The consumer to register
     * @param identifier Optional identifier for the consumer (e.g., peer ID, stream ID)
     */
    virtual void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, 
                                 const std::string& identifier = "") = 0;

    /**
     * @brief Register a consumer to receive data from this producer with media type
     * @param consumer The consumer to register
     * @param identifier Identifier for the consumer (e.g., peer ID, stream ID)
     * @param media_type Media type for the consumer (e.g., "video", "audio")
     */
    virtual void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer, 
        const std::string& identifier,
        const std::string& media_type)
    {
        // Default implementation delegates to standard registerConsumer
        // Derived classes can override this to support media type
        (void)media_type;  // Suppress unused warning
        registerConsumer(consumer, identifier);
    }

    /**
     * @brief Register a consumer to receive data from this producer with time range
     * @param consumer The consumer to register
     * @param identifier Optional identifier for the consumer (e.g., peer ID, stream ID)
     * @param startTime Start time for playback
     * @param endTime End time for playback
     */
    virtual void registerConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                 const std::string& identifier,
                                 const std::string& startTime,
                                 const std::string& endTime)
    {
        // Default implementation delegates to standard registerConsumer
        // Derived classes can override this to support time-range playback
        registerConsumer(consumer, identifier);
    }

    /**
     * @brief Unregister a consumer from this producer
     * @param consumer The consumer to unregister
     * @param identifier Optional identifier for the consumer
     */
    virtual void unregisterConsumer(std::shared_ptr<IMediaDataConsumer> consumer,
                                   const std::string& identifier = "", bool doNotRemoveClient = false) = 0;

    /**
     * @brief Start the producer and begin generating/sourcing data
     * @return true if started successfully, false otherwise
     */
    virtual bool start() = 0;

    /**
     * @brief Stop the producer and stop generating/sourcing data
     */
    virtual void stop() = 0;

    /**
     * @brief Check if the producer is currently running
     * @return true if running, false otherwise
     */
    virtual bool isRunning() const = 0;

    /**
     * @brief Get the media type this producer generates
     * @return The media type (video, audio, etc.)
     */
    virtual eMediaType getProducerMediaType() const = 0;

    /**
     * @brief Get the source identifier for this producer
     * @return The source identifier (URL, device ID, etc.)
     */
    virtual std::string getSourceIdentifier() const = 0;

    /**
     * @brief Get the number of registered consumers
     * @return Number of active consumers
     */
    virtual size_t getConsumerCount() const = 0;

    /**
     * @brief Check if the producer has any registered consumers
     * @return true if has consumers, false otherwise
     */
    virtual bool hasConsumers() const = 0;

    /**
     * @brief Register a callback to be notified when the producer finishes (EOS)
     * @param callback Function to call when the producer reaches end of stream
     *
     * Default implementation does nothing. Override in derived classes that support callbacks.
     */
    virtual void onFinished(std::function<void()> callback) { (void)callback; }

    /**
     * @brief Register a callback to be notified when the producer encounters an error
     * @param callback Function to call with error message and error code
     *
     * Default implementation does nothing. Override in derived classes that support callbacks.
     */
    virtual void onError(std::function<void(const std::string&, int)> callback) { (void)callback; }

    /**
     * @brief Get a shared pointer to self (for enable_shared_from_this)
     * @return Shared pointer to this producer
     */
    std::shared_ptr<IMediaDataProducer> getSelf()
    {
        try
        {
            return shared_from_this();
        }
        catch (const std::bad_weak_ptr& e)
        {
            // Return null if not managed by shared_ptr
            return std::shared_ptr<IMediaDataProducer>(nullptr);
        }
    }

protected:
    /**
     * @brief Distribute data to all registered consumers
     * @param frameData The frame data to distribute
     */
    virtual void distributeToConsumers(std::shared_ptr<RawFrameParams> frameData) = 0;

    /**
     * @brief Distribute data to all registered consumers
     * @param frameParams The frame parameters to distribute
     */
    virtual void distributeToConsumers(FrameParams& frameParams) = 0;
};
