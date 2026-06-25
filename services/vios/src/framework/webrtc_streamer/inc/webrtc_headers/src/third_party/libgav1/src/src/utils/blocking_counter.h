/*
 * Copyright 2019 The libgav1 Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef LIBGAV1_SRC_UTILS_BLOCKING_COUNTER_H_
#define LIBGAV1_SRC_UTILS_BLOCKING_COUNTER_H_

#include <cassert>
#include <condition_variable>  // NOLINT (unapproved c++11 header)
#include <mutex>               // NOLINT (unapproved c++11 header)

#include "src/utils/compiler_attributes.h"

namespace libgav1 {

// Implementation of a Blocking Counter that is used for the "fork-join"
// use case. Typical usage would be as follows:
//   BlockingCounter counter(num_jobs);
//     - spawn the jobs.
//     - call counter.Wait() on the master thread.
//     - worker threads will call counter.Decrement().
//     - master thread will return from counter.Wait() when all workers are
//     complete.
template <bool has_failure_status>
class BlockingCounterImpl {
 public:
  explicit BlockingCounterImpl(int initial_count)
      : count_(initial_count), job_failed_(false) {}

  // Increment the counter by |count|. This must be called before Wait() is
  // called. This must be called from the same thread that will call Wait().
  void IncrementBy(int count) {
    assert(count >= 0);
    std::unique_lock<std::mutex> lock(mutex_);
    count_ += count;
  }

  // Decrement the counter by 1. This function can be called only when
  // |has_failure_status| is false (i.e.) when this class is being used with the
  // |BlockingCounter| alias.
  void Decrement() {
    static_assert(!has_failure_status, "");
    std::unique_lock<std::mutex> lock(mutex_);
    if (--count_ == 0) {
      condition_.notify_one();
    }
  }

  // Decrement the counter by 1. This function can be called only when
  // |has_failure_status| is true (i.e.) when this class is being used with the
  // |BlockingCounterWithStatus| alias. |job_succeeded| is used to update the
  // state of |job_failed_|.
  void Decrement(bool job_succeeded) {
    static_assert(has_failure_status, "");
    std::unique_lock<std::mutex> lock(mutex_);
    job_failed_ |= !job_succeeded;
    if (--count_ == 0) {
      condition_.notify_one();
    }
  }

  // Block until the counter becomes 0. This function can be called only once
  // per object. If |has_failure_status| is true, true is returned if all the
  // jobs succeeded and false is returned if any of the jobs failed. If
  // |has_failure_status| is false, this function always returns true.
  bool Wait() {
    std::unique_lock<std::mutex> lock(mutex_);
    condition_.wait(lock, [this]() { return count_ == 0; });
    // If |has_failure_status| is false, we simply return true.
    return has_failure_status ? !job_failed_ : true;
  }

 private:
  std::mutex mutex_;
  std::condition_variable condition_;
  int count_ LIBGAV1_GUARDED_BY(mutex_);
  bool job_failed_ LIBGAV1_GUARDED_BY(mutex_);
};

using BlockingCounterWithStatus = BlockingCounterImpl<true>;
using BlockingCounter = BlockingCounterImpl<false>;

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_BLOCKING_COUNTER_H_
