/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/**********
This library is free software; you can redistribute it and/or modify it under
the terms of the GNU Lesser General Public License as published by the
Free Software Foundation; either version 3 of the License, or (at your
option) any later version. (See <http://www.gnu.org/copyleft/lesser.html>.)

This library is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for
more details.

You should have received a copy of the GNU Lesser General Public License
along with this library; if not, write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
**********/
// Copyright (c) 1996-2023 Live Networks, Inc.  All rights reserved.
// Basic Usage Environment: for a simple, non-scripted, console application
// C++ header

#ifndef _BASIC_USAGE_ENVIRONMENT0_HH
#define _BASIC_USAGE_ENVIRONMENT0_HH

#ifndef _BASICUSAGEENVIRONMENT_VERSION_HH
#include "BasicUsageEnvironment_version.hh"
#endif

#ifndef _USAGE_ENVIRONMENT_HH
#include "UsageEnvironment.hh"
#endif

#ifndef _DELAY_QUEUE_HH
#include "DelayQueue.hh"
#endif

#ifndef NO_STD_LIB
#ifndef _LIBCPP_ATOMIC
#include <atomic>
#endif
#endif

#define RESULT_MSG_BUFFER_MAX 1000

// An abstract base class, useful for subclassing
// (e.g., to redefine the implementation of "operator<<")
class BasicUsageEnvironment0: public UsageEnvironment {
public:
  // redefined virtual functions:
  virtual MsgString getResultMsg() const;

  virtual void setResultMsg(MsgString msg);
  virtual void setResultMsg(MsgString msg1,
		    MsgString msg2);
  virtual void setResultMsg(MsgString msg1,
		    MsgString msg2,
		    MsgString msg3);
  virtual void setResultErrMsg(MsgString msg, int err = 0);

  virtual void appendToResultMsg(MsgString msg);

  virtual void reportBackgroundError();

protected:
  BasicUsageEnvironment0(TaskScheduler& taskScheduler);
  virtual ~BasicUsageEnvironment0();

private:
  void reset();

  char fResultMsgBuffer[RESULT_MSG_BUFFER_MAX];
  unsigned fCurBufferSize;
  unsigned fBufferMaxSize;
};

class HandlerSet; // forward

// Note: You may redefine MAX_NUM_EVENT_TRIGGERS,
// but it must be <= the number of bits in an "EventTriggerId"
#ifndef MAX_NUM_EVENT_TRIGGERS
#define MAX_NUM_EVENT_TRIGGERS 32
#endif
#define EVENT_TRIGGER_ID_HIGH_BIT (1 << (MAX_NUM_EVENT_TRIGGERS-1))

// An abstract base class, useful for subclassing
// (e.g., to redefine the implementation of socket event handling)
class BasicTaskScheduler0: public TaskScheduler {
public:
  virtual ~BasicTaskScheduler0();

  virtual void SingleStep(unsigned maxDelayTime = 0) = 0;
      // "maxDelayTime" is in microseconds.  It allows a subclass to impose a limit
      // on how long "select()" can delay, in case it wants to also do polling.
      // 0 (the default value) means: There's no maximum; just look at the delay queue

public:
  // Redefined virtual functions:
  virtual TaskToken scheduleDelayedTask(int64_t microseconds, TaskFunc* proc,
				void* clientData);
  virtual void unscheduleDelayedTask(TaskToken& prevTask);

  virtual void doEventLoop(char volatile* watchVariable);

  virtual EventTriggerId createEventTrigger(TaskFunc* eventHandlerProc);
  virtual void deleteEventTrigger(EventTriggerId eventTriggerId);
  virtual void triggerEvent(EventTriggerId eventTriggerId, void* clientData = NULL);

protected:
  BasicTaskScheduler0();

protected:
  // To implement delayed operations:
  intptr_t fTokenCounter;
  DelayQueue fDelayQueue;

  // To implement background reads:
  HandlerSet* fHandlers;
  int fLastHandledSocketNum;

  // To implement event triggers:
#ifndef NO_STD_LIB
  std::atomic_flag fTriggersAwaitingHandling[MAX_NUM_EVENT_TRIGGERS];
#else
  Boolean volatile fTriggersAwaitingHandling[MAX_NUM_EVENT_TRIGGERS];
#endif
  u_int32_t fLastUsedTriggerMask; // implemented as a 32-bit bitmap
  TaskFunc* fTriggeredEventHandlers[MAX_NUM_EVENT_TRIGGERS];
  void* fTriggeredEventClientDatas[MAX_NUM_EVENT_TRIGGERS];
  unsigned fLastUsedTriggerNum; // in the range [0,MAX_NUM_EVENT_TRIGGERS)
  Boolean fEventTriggersAreBeingUsed;
};

#endif
