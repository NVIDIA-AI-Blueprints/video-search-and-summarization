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

#ifndef LIBGAV1_SRC_UTILS_THREADPOOL_H_
#define LIBGAV1_SRC_UTILS_THREADPOOL_H_

#include <functional>
#include <memory>

#if defined(__APPLE__)
#include <TargetConditionals.h>
#endif

#if !defined(LIBGAV1_THREADPOOL_USE_STD_MUTEX)
#if defined(__ANDROID__) || (defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE)
#define LIBGAV1_THREADPOOL_USE_STD_MUTEX 1
#else
#define LIBGAV1_THREADPOOL_USE_STD_MUTEX 0
#endif
#endif

#if LIBGAV1_THREADPOOL_USE_STD_MUTEX
#include <condition_variable>  // NOLINT (unapproved c++11 header)
#include <mutex>               // NOLINT (unapproved c++11 header)
#else
// absl::Mutex & absl::CondVar are significantly faster than the pthread
// variants on platforms other than Android. iOS may deadlock on Shutdown()
// using absl, see b/142251739.
#include "absl/base/thread_annotations.h"
#include "absl/synchronization/mutex.h"
#endif

#include "src/utils/compiler_attributes.h"
#include "src/utils/executor.h"
#include "src/utils/memory.h"
#include "src/utils/unbounded_queue.h"

namespace libgav1 {

// An implementation of ThreadPool using POSIX threads (pthreads) or Windows
// threads.
//
// - The pool allocates a fixed number of worker threads on instantiation.
// - The worker threads will pick up work jobs as they arrive.
// - If all workers are busy, work jobs are queued for later execution.
//
// The thread pool is shut down when the pool is destroyed.
//
// Example usage of the thread pool:
//   {
//     std::unique_ptr<ThreadPool> pool = ThreadPool::Create(4);
//     for (int i = 0; i < 100; ++i) {  // Dispatch 100 jobs.
//       pool->Schedule([&my_data]() { MyFunction(&my_data); });
//     }
//   } // ThreadPool gets destroyed only when all jobs are done.
class ThreadPool : public Executor, public Allocable {
 public:
  // Creates the thread pool with the specified number of worker threads.
  // If num_threads is 1, the closures are run in FIFO order.
  static std::unique_ptr<ThreadPool> Create(int num_threads);

  // Like the above factory method, but also sets the name prefix for threads.
  static std::unique_ptr<ThreadPool> Create(const char name_prefix[],
                                            int num_threads);

  // The destructor will shut down the thread pool and all jobs are executed.
  // Note that after shutdown, the thread pool does not accept further jobs.
  ~ThreadPool() override;

  // Adds the specified "closure" to the queue for processing. If worker threads
  // are available, "closure" will run immediately. Otherwise "closure" is
  // queued for later execution.
  //
  // NOTE: If the internal queue is full and cannot be resized because of an
  // out-of-memory error, the current thread runs "closure" before returning
  // from Schedule(). For our use cases, this seems better than the
  // alternatives:
  //   1. Return a failure status.
  //   2. Have the current thread wait until the queue is not full.
  void Schedule(std::function<void()> closure) override;

  int num_threads() const;

 private:
  class WorkerThread;

  // Creates the thread pool with the specified number of worker threads.
  // If num_threads is 1, the closures are run in FIFO order.
  ThreadPool(const char name_prefix[], std::unique_ptr<WorkerThread*[]> threads,
             int num_threads);

  // Starts the worker pool.
  LIBGAV1_MUST_USE_RESULT bool StartWorkers();

  void WorkerFunction();

  // Shuts down the thread pool, i.e. worker threads finish their work and
  // pick up new jobs until the queue is empty. This call will block until
  // the shutdown is complete.
  //
  // Note: If a worker encounters an empty queue after this call, it will exit.
  // Other workers might still be running, and if the queue fills up again, the
  // thread pool will continue to operate with a decreased number of workers.
  // It is up to the caller to prevent adding new jobs.
  void Shutdown();

#if LIBGAV1_THREADPOOL_USE_STD_MUTEX

  void LockMutex() { queue_mutex_.lock(); }
  void UnlockMutex() { queue_mutex_.unlock(); }

  void Wait() {
    std::unique_lock<std::mutex> queue_lock(queue_mutex_, std::adopt_lock);
    condition_.wait(queue_lock);
    queue_lock.release();
  }

  void SignalOne() { condition_.notify_one(); }
  void SignalAll() { condition_.notify_all(); }

  std::condition_variable condition_;
  std::mutex queue_mutex_;

#else  // !LIBGAV1_THREADPOOL_USE_STD_MUTEX

  void LockMutex() ABSL_EXCLUSIVE_LOCK_FUNCTION() { queue_mutex_.Lock(); }
  void UnlockMutex() ABSL_UNLOCK_FUNCTION() { queue_mutex_.Unlock(); }
  void Wait() { condition_.Wait(&queue_mutex_); }
  void SignalOne() { condition_.Signal(); }
  void SignalAll() { condition_.SignalAll(); }

  absl::CondVar condition_;
  absl::Mutex queue_mutex_;

#endif  // LIBGAV1_THREADPOOL_USE_STD_MUTEX

  UnboundedQueue<std::function<void()>> queue_ LIBGAV1_GUARDED_BY(queue_mutex_);
  // If not all the worker threads are created, the first entry after the
  // created worker threads is a null pointer.
  const std::unique_ptr<WorkerThread*[]> threads_;

  bool exit_threads_ LIBGAV1_GUARDED_BY(queue_mutex_) = false;
  const int num_threads_ = 0;
  // name_prefix_ is a C string, whose length is restricted to 16 characters,
  // including the terminating null byte ('\0'). This restriction comes from
  // the Linux pthread_setname_np() function.
  char name_prefix_[16];
};

}  // namespace libgav1

#undef LIBGAV1_THREADPOOL_USE_STD_MUTEX

#endif  // LIBGAV1_SRC_UTILS_THREADPOOL_H_
