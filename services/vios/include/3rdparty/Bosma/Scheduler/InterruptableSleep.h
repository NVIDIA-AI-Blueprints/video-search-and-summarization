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

#pragma once

#include <chrono>
#include <thread>
#include <future>
#include <mutex>
#include <sstream>

namespace Bosma {
    class InterruptableSleep {

        using Clock = std::chrono::system_clock;

        // InterruptableSleep offers a sleep that can be interrupted by any thread.
        // It can be interrupted multiple times
        // and be interrupted before any sleep is called (the sleep will immediately complete)
        // Has same interface as condition_variables and futures, except with sleep instead of wait.
        // For a given object, sleep can be called on multiple threads safely, but is not recommended as behaviour is undefined.

    public:
        InterruptableSleep() : interrupted(false) {
        }

        InterruptableSleep(const InterruptableSleep &) = delete;

        InterruptableSleep(InterruptableSleep &&) noexcept = delete;

        ~InterruptableSleep() noexcept = default;

        InterruptableSleep &operator=(const InterruptableSleep &) noexcept = delete;

        InterruptableSleep &operator=(InterruptableSleep &&) noexcept = delete;

        void sleep_for(Clock::duration duration) {
          std::unique_lock<std::mutex> ul(m);
          cv.wait_for(ul, duration, [this] { return interrupted; });
          interrupted = false;
        }

        void sleep_until(Clock::time_point time) {
          std::unique_lock<std::mutex> ul(m);
          cv.wait_until(ul, time, [this] { return interrupted; });
          interrupted = false;
        }

        void sleep() {
          std::unique_lock<std::mutex> ul(m);
          cv.wait(ul, [this] { return interrupted; });
          interrupted = false;
        }

        void interrupt() {
          std::lock_guard<std::mutex> lg(m);
          interrupted = true;
          cv.notify_one();
        }

    private:
        bool interrupted;
        std::mutex m;
        std::condition_variable cv;
    };
}
