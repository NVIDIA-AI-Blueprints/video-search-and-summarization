/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: MIT
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 * DEALINGS IN THE SOFTWARE.
 */

#pragma once

#include <exception>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <utility>
#include <vector>
#include "logger.h"

/*
 * AvLoopSyncCoordinator
 * ---------------------
 * Per-RTSP-session rendezvous barrier used to keep audio and video
 * subsessions of the same file aligned at loop boundaries.
 *
 * Without this barrier each subsession owns its own GstDeMux pipeline,
 * each reaches EOS at a slightly different wall-clock instant (B-frame
 * drain tail, queue depths, etc.), and each independently seeks back
 * to start. The audio side typically restarts ~100-200 ms before the
 * video side and that delta accumulates every loop -- audible AV drift.
 *
 * Lifecycle:
 *   - One coordinator is created in DynamicRTSPServer::createNewSMS()
 *     when BOTH a video and an audio NvFileServerMediaSubsession are
 *     being added to the same ServerMediaSession (i.e. URL contains
 *     "includeAudio=true" and sourceType is file).
 *   - The shared_ptr is injected into both subsessions, which forward
 *     it to their NvMediaSource and on to their ByteStreamSource.
 *   - Each ByteStreamSource registers itself with the coordinator on
 *     construction (only when loop playback is active for that source)
 *     and unregisters in its destructor.
 *   - On EOS, instead of calling seekToStart() + retryGetFrame()
 *     immediately, the source calls signalEos(this). When ALL registered
 *     participants have signalled, the coordinator invokes every restart
 *     callback synchronously -- both subsessions restart on the same
 *     task-scheduler tick.
 *
 * Thread-safety:
 *   - All public methods are mutex-guarded.
 *   - Restart callbacks fire OUTSIDE the mutex so that callbacks which
 *     end up calling back into the coordinator (e.g. via a teardown)
 *     don't deadlock.
 *   - In practice signalEos / register / unregister all run on the
 *     live555 task-scheduler thread (closeSource -> signalEos, ctor/dtor
 *     of ByteStreamSource), so contention is essentially nil.
 *
 * Multi-client safety:
 *   - The coordinator is NOT a singleton. Each ServerMediaSession owns
 *     one. Two concurrent RTSP clients on the same file get two
 *     independent coordinators -- they don't barrier-block each other.
 *
 * Single-participant degenerate case:
 *   - With the createNewSMS() gate above, the coordinator is only
 *     created when both audio and video subsessions exist. But if one
 *     side tears down (or fails to start) while the other is parked
 *     waiting, the unregisterParticipant() call re-evaluates the
 *     barrier so the remaining participant doesn't deadlock.
 */
class AvLoopSyncCoordinator
{
public:
    using RestartFn = std::function<void()>;

    explicit AvLoopSyncCoordinator(std::string label = std::string())
        : m_label(std::move(label))
    {
    }

    /*
     * Register a participant with a restart callback. `token` must be
     * a stable, unique identifier per participant (typically `this` of
     * the ByteStreamSource). Idempotent: re-registering the same token
     * replaces the callback and resets its arrived flag to false.
     */
    void registerParticipant(void* token, RestartFn onRestart)
    {
        if (token == nullptr)
        {
            return;
        }
        std::lock_guard<std::mutex> guard(m_mu);
        auto& p = m_participants[token];
        p.onRestart = std::move(onRestart);
        p.arrived = false;
        LOG(info) << "AvLoopSyncCoordinator[" << m_label << "] registered token="
                  << token << ", total=" << m_participants.size() << std::endl;
    }

    /*
     * Remove a participant. If after removal the remaining participants
     * are all already at EOS, fire their restart callbacks so they
     * don't wait forever for the now-gone participant.
     */
    void unregisterParticipant(void* token)
    {
        if (token == nullptr)
        {
            return;
        }
        std::vector<RestartFn> toFire;
        {
            std::lock_guard<std::mutex> guard(m_mu);
            auto it = m_participants.find(token);
            if (it == m_participants.end())
            {
                return;
            }
            m_participants.erase(it);
            LOG(info) << "AvLoopSyncCoordinator[" << m_label << "] unregistered token="
                      << token << ", remaining=" << m_participants.size() << std::endl;

            if (!m_participants.empty() && allArrivedLocked())
            {
                /* Surviving participants were already parked waiting
                 * for the one we just removed. Release them now. */
                LOG(info) << "AvLoopSyncCoordinator[" << m_label
                          << "] releasing survivors after unregister" << std::endl;
                toFire = collectAndResetLocked();
            }
        }
        fireOutsideLock(toFire);
    }

    /*
     * Mark a participant as arrived at EOS. If this completes the
     * barrier (all registered participants arrived), fire every
     * restart callback synchronously on the calling thread.
     */
    void signalEos(void* token)
    {
        if (token == nullptr)
        {
            return;
        }
        std::vector<RestartFn> toFire;
        {
            std::lock_guard<std::mutex> guard(m_mu);
            auto it = m_participants.find(token);
            if (it == m_participants.end())
            {
                LOG(warning) << "AvLoopSyncCoordinator[" << m_label
                             << "] signalEos for unknown token=" << token << std::endl;
                return;
            }
            it->second.arrived = true;
            LOG(info) << "AvLoopSyncCoordinator[" << m_label << "] signalEos token="
                      << token << ", arrived=" << arrivedCountLocked()
                      << "/" << m_participants.size() << std::endl;

            if (allArrivedLocked())
            {
                LOG(info) << "AvLoopSyncCoordinator[" << m_label
                          << "] all participants arrived -> firing restarts" << std::endl;
                toFire = collectAndResetLocked();
            }
        }
        fireOutsideLock(toFire);
    }

    size_t participantCount()
    {
        std::lock_guard<std::mutex> guard(m_mu);
        return m_participants.size();
    }

private:
    struct Participant
    {
        RestartFn onRestart;
        bool arrived = false;
    };

    bool allArrivedLocked() const
    {
        if (m_participants.empty())
        {
            return false;
        }
        for (const auto& kv : m_participants)
        {
            if (!kv.second.arrived)
            {
                return false;
            }
        }
        return true;
    }

    size_t arrivedCountLocked() const
    {
        size_t n = 0;
        for (const auto& kv : m_participants)
        {
            if (kv.second.arrived) ++n;
        }
        return n;
    }

    std::vector<RestartFn> collectAndResetLocked()
    {
        std::vector<RestartFn> out;
        out.reserve(m_participants.size());
        for (auto& kv : m_participants)
        {
            if (kv.second.onRestart)
            {
                out.push_back(kv.second.onRestart);
            }
            kv.second.arrived = false;
        }
        return out;
    }

    static void fireOutsideLock(const std::vector<RestartFn>& fns)
    {
        for (const auto& fn : fns)
        {
            try
            {
                fn();
            }
            catch (const std::exception& e)
            {
                LOG(error) << "AvLoopSyncCoordinator restart callback threw: "
                           << e.what() << std::endl;
            }
            catch (...)
            {
                LOG(error) << "AvLoopSyncCoordinator restart callback threw (unknown)"
                           << std::endl;
            }
        }
    }

    std::string                       m_label;
    std::mutex                        m_mu;
    std::map<void*, Participant>      m_participants;
};
