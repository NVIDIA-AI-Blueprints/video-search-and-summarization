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

#ifndef LIBGAV1_SRC_THREADING_STRATEGY_H_
#define LIBGAV1_SRC_THREADING_STRATEGY_H_

#include <memory>

#include "src/obu_parser.h"
#include "src/utils/compiler_attributes.h"
#include "src/utils/threadpool.h"

namespace libgav1 {

class FrameScratchBufferPool;

// This class allocates and manages the worker threads among thread pools used
// for multi-threaded decoding.
class ThreadingStrategy {
 public:
  ThreadingStrategy() = default;

  // Not copyable or movable.
  ThreadingStrategy(const ThreadingStrategy&) = delete;
  ThreadingStrategy& operator=(const ThreadingStrategy&) = delete;

  // Creates or re-allocates the thread pools based on the |frame_header| and
  // |thread_count|. This function is used only in non frame-parallel mode. This
  // function is idempotent if the |frame_header| and |thread_count| don't
  // change between calls (it will only create new threads on the first call and
  // do nothing on the subsequent calls). This function also starts the worker
  // threads whenever it creates new thread pools.
  // The following strategy is used to allocate threads:
  //   * One thread is allocated for decoding each Tile.
  //   * Any remaining threads are allocated for superblock row multi-threading
  //     within each of the tile in a round robin fashion.
  // Note: During the lifetime of a ThreadingStrategy object, only one of the
  // Reset() variants will be used.
  LIBGAV1_MUST_USE_RESULT bool Reset(const ObuFrameHeader& frame_header,
                                     int thread_count);

  // Creates or re-allocates a thread pool with |thread_count| threads. This
  // function is used only in frame parallel mode. This function is idempotent
  // if the |thread_count| doesn't change between calls (it will only create new
  // threads on the first call and do nothing on the subsequent calls).
  // Note: During the lifetime of a ThreadingStrategy object, only one of the
  // Reset() variants will be used.
  LIBGAV1_MUST_USE_RESULT bool Reset(int thread_count);

  // Returns a pointer to the ThreadPool that is to be used for Tile
  // multi-threading.
  ThreadPool* tile_thread_pool() const {
    return (tile_thread_count_ != 0) ? thread_pool_.get() : nullptr;
  }

  int tile_thread_count() const { return tile_thread_count_; }

  // Returns a pointer to the underlying ThreadPool.
  // Note: Valid only when |frame_parallel_| is true. This is used for
  // facilitating in-frame multi-threading in that case.
  ThreadPool* thread_pool() const { return thread_pool_.get(); }

  // Returns a pointer to the ThreadPool that is to be used within the Tile at
  // index |tile_index| for superblock row multi-threading.
  // Note: Valid only when |frame_parallel_| is false.
  ThreadPool* row_thread_pool(int tile_index) const {
    return tile_index < max_tile_index_for_row_threads_ ? thread_pool_.get()
                                                        : nullptr;
  }

  // Returns a pointer to the ThreadPool that is to be used for post filter
  // multi-threading.
  // Note: Valid only when |frame_parallel_| is false.
  ThreadPool* post_filter_thread_pool() const {
    return frame_parallel_ ? nullptr : thread_pool_.get();
  }

  // Returns a pointer to the ThreadPool that is to be used for film grain
  // synthesis and blending.
  // Note: Valid only when |frame_parallel_| is false.
  ThreadPool* film_grain_thread_pool() const { return thread_pool_.get(); }

 private:
  std::unique_ptr<ThreadPool> thread_pool_;
  int tile_thread_count_ = 0;
  int max_tile_index_for_row_threads_ = 0;
  bool frame_parallel_ = false;
};

// Initializes the |frame_thread_pool| and the necessary worker threadpools (the
// threading_strategy objects in each of the frame scratch buffer in
// |frame_scratch_buffer_pool|) as follows:
//  * frame_threads = ComputeFrameThreadCount();
//  * For more details on how frame_threads is computed, see the function
//    comment in ComputeFrameThreadCount().
//  * |frame_thread_pool| is created with |frame_threads| threads.
//  * divide the remaining number of threads into each frame thread and
//    initialize a frame_scratch_buffer.threading_strategy for each frame
//    thread.
//  When this function is called, |frame_scratch_buffer_pool| must be empty. If
//  this function returns true, it means the initialization was successful and
//  one of the following is true:
//    * |frame_thread_pool| has been successfully initialized and
//      |frame_scratch_buffer_pool| has been successfully populated with
//      |frame_threads| buffers to be used by each frame thread. The total
//      number of threads that this function creates will always be equal to
//      |thread_count|.
//    * |frame_thread_pool| is nullptr. |frame_scratch_buffer_pool| is not
//      modified. This means that frame threading will not be used and the
//      decoder will continue to operate normally in non frame parallel mode.
LIBGAV1_MUST_USE_RESULT bool InitializeThreadPoolsForFrameParallel(
    int thread_count, int tile_count, int tile_columns,
    std::unique_ptr<ThreadPool>* frame_thread_pool,
    FrameScratchBufferPool* frame_scratch_buffer_pool);

}  // namespace libgav1

#endif  // LIBGAV1_SRC_THREADING_STRATEGY_H_
