// Copyright (c) 2017 Facebook Inc.
// Copyright (c) 2015-2017 Georgia Institute of Technology
// All rights reserved.
//
// Copyright 2019 Google LLC
//
// This source code is licensed under the BSD-style license found in the
// LICENSE file in the root directory of this source tree.

#ifndef __PTHREADPOOL_INCLUDE_PTHREADPOOL_H_
#define __PTHREADPOOL_INCLUDE_PTHREADPOOL_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct pthreadpool* pthreadpool_t;

typedef void (*pthreadpool_task_1d_t)(void*, size_t);
typedef void (*pthreadpool_task_1d_with_thread_t)(void*, size_t, size_t);
typedef void (*pthreadpool_task_1d_tile_1d_t)(void*, size_t, size_t);
typedef void (*pthreadpool_task_1d_tile_1d_dynamic_t)(void*, size_t, size_t);
typedef void (*pthreadpool_task_1d_tile_1d_dynamic_with_id_t)(void*, uint32_t,
                                                              size_t, size_t);
typedef void (*pthreadpool_task_2d_t)(void*, size_t, size_t);
typedef void (*pthreadpool_task_2d_with_thread_t)(void*, size_t, size_t,
                                                  size_t);
typedef void (*pthreadpool_task_2d_tile_1d_t)(void*, size_t, size_t, size_t);
typedef void (*pthreadpool_task_2d_tile_1d_dynamic_t)(void*, size_t, size_t,
                                                      size_t);
typedef void (*pthreadpool_task_2d_tile_2d_t)(void*, size_t, size_t, size_t,
                                              size_t);
typedef void (*pthreadpool_task_2d_tile_2d_dynamic_t)(void*, size_t, size_t,
                                                      size_t, size_t);
typedef void (*pthreadpool_task_3d_t)(void*, size_t, size_t, size_t);
typedef void (*pthreadpool_task_3d_tile_1d_t)(void*, size_t, size_t, size_t,
                                              size_t);
typedef void (*pthreadpool_task_3d_tile_1d_with_thread_t)(void*, size_t, size_t,
                                                          size_t, size_t,
                                                          size_t);
typedef void (*pthreadpool_task_3d_tile_1d_dynamic_t)(void*, size_t, size_t,
                                                      size_t, size_t);
typedef void (*pthreadpool_task_3d_tile_2d_t)(void*, size_t, size_t, size_t,
                                              size_t, size_t);
typedef void (*pthreadpool_task_3d_tile_2d_dynamic_t)(void*, size_t, size_t,
                                                      size_t, size_t, size_t);
typedef void (*pthreadpool_task_4d_t)(void*, size_t, size_t, size_t, size_t);
typedef void (*pthreadpool_task_4d_tile_1d_t)(void*, size_t, size_t, size_t,
                                              size_t, size_t);
typedef void (*pthreadpool_task_4d_tile_2d_t)(void*, size_t, size_t, size_t,
                                              size_t, size_t, size_t);
typedef void (*pthreadpool_task_4d_tile_2d_dynamic_t)(void*, size_t, size_t,
                                                      size_t, size_t, size_t,
                                                      size_t);
typedef void (*pthreadpool_task_5d_t)(void*, size_t, size_t, size_t, size_t,
                                      size_t);
typedef void (*pthreadpool_task_5d_tile_1d_t)(void*, size_t, size_t, size_t,
                                              size_t, size_t, size_t);
typedef void (*pthreadpool_task_5d_tile_2d_t)(void*, size_t, size_t, size_t,
                                              size_t, size_t, size_t, size_t);
typedef void (*pthreadpool_task_6d_t)(void*, size_t, size_t, size_t, size_t,
                                      size_t, size_t);
typedef void (*pthreadpool_task_6d_tile_1d_t)(void*, size_t, size_t, size_t,
                                              size_t, size_t, size_t, size_t);
typedef void (*pthreadpool_task_6d_tile_2d_t)(void*, size_t, size_t, size_t,
                                              size_t, size_t, size_t, size_t,
                                              size_t);

typedef void (*pthreadpool_task_1d_with_id_t)(void*, uint32_t, size_t);
typedef void (*pthreadpool_task_2d_tile_1d_with_id_t)(void*, uint32_t, size_t,
                                                      size_t, size_t);
typedef void (*pthreadpool_task_2d_tile_1d_dynamic_with_id_t)(void*, uint32_t,
                                                              size_t, size_t,
                                                              size_t);
typedef void (*pthreadpool_task_2d_tile_2d_with_id_t)(void*, uint32_t, size_t,
                                                      size_t, size_t, size_t);
typedef void (*pthreadpool_task_2d_tile_2d_dynamic_with_id_t)(void*, uint32_t,
                                                              size_t, size_t,
                                                              size_t, size_t);
typedef void (*pthreadpool_task_3d_tile_1d_with_id_t)(void*, uint32_t, size_t,
                                                      size_t, size_t, size_t);
typedef void (*pthreadpool_task_3d_tile_1d_dynamic_with_id_t)(void*, uint32_t,
                                                              size_t, size_t,
                                                              size_t, size_t);
typedef void (*pthreadpool_task_3d_tile_2d_with_id_t)(void*, uint32_t, size_t,
                                                      size_t, size_t, size_t,
                                                      size_t);
typedef void (*pthreadpool_task_3d_tile_2d_dynamic_with_id_t)(void*, uint32_t,
                                                              size_t, size_t,
                                                              size_t, size_t,
                                                              size_t);
typedef void (*pthreadpool_task_4d_tile_2d_with_id_t)(void*, uint32_t, size_t,
                                                      size_t, size_t, size_t,
                                                      size_t, size_t);
typedef void (*pthreadpool_task_4d_tile_2d_dynamic_with_id_t)(void*, uint32_t,
                                                              size_t, size_t,
                                                              size_t, size_t,
                                                              size_t, size_t);

typedef void (*pthreadpool_task_1d_tile_1d_dynamic_with_id_with_thread_t)(
    void*, uint32_t, size_t, size_t, size_t);
typedef void (*pthreadpool_task_2d_tile_1d_with_id_with_thread_t)(
    void*, uint32_t, size_t, size_t, size_t, size_t);
typedef void (*pthreadpool_task_2d_tile_1d_dynamic_with_id_with_thread_t)(
    void*, uint32_t, size_t, size_t, size_t, size_t);
typedef void (*pthreadpool_task_3d_tile_1d_with_id_with_thread_t)(
    void*, uint32_t, size_t, size_t, size_t, size_t, size_t);
typedef void (*pthreadpool_task_3d_tile_1d_dynamic_with_id_with_thread_t)(
    void*, uint32_t, size_t, size_t, size_t, size_t, size_t);

/**
 * Disable support for denormalized numbers to the maximum extent possible for
 * the duration of the computation.
 *
 * Handling denormalized floating-point numbers is often implemented in
 * microcode, and incurs significant performance degradation. This hint
 * instructs the thread pool to disable support for denormalized numbers before
 * running the computation by manipulating architecture-specific control
 * registers, and restore the initial value of control registers after the
 * computation is complete. The thread pool temporary disables denormalized
 * numbers on all threads involved in the computation (i.e. the caller threads,
 * and potentially worker threads).
 *
 * Disabling denormalized numbers may have a small negative effect on results'
 * accuracy. As various architectures differ in capabilities to control
 * processing of denormalized numbers, using this flag may also hurt results'
 * reproducibility across different instruction set architectures.
 */
#define PTHREADPOOL_FLAG_DISABLE_DENORMALS 0x00000001

/**
 * Yield worker threads to the system scheduler after the operation is finished.
 *
 * Force workers to use kernel wait (instead of active spin-wait by default) for
 * new commands after this command is processed. This flag affects only the
 * immediate next operation on this thread pool. To make the thread pool always
 * use kernel wait, pass this flag to all parallelization functions.
 *
 * Note: This flag is currently ignored as yielding the worker threads after a
 * fixed number of spin-wait iterations is currently the default behaviour.
 */
#define PTHREADPOOL_FLAG_YIELD_WORKERS 0x00000002

/**
 * If worker threads are provided by an external @a pthreadpool_executor,
 * release them back to the executor instead of spinning for a fixed number of
 * iterations first.
 */
#define PTHREADPOOL_FLAG_DONT_SPIN_WORKERS 0x00000004

// Disable function sanitization here. The thread pool calls these functions
// via function pointers with void* contexts, which triggers false positives in
// the function sanitizer when casting and calling the actual context type.
#if defined(__clang__) && __has_attribute(no_sanitize)
#define PTHREADPOOL_NO_SANITIZE_FUNCTION \
  __attribute__((no_sanitize("function")))
#else
#define PTHREADPOOL_NO_SANITIZE_FUNCTION
#endif

#ifdef __cplusplus
extern "C" {
#endif

/// An abstract interface of a parallel task executor.
struct pthreadpool_executor {
  /// Get the number of tasks that can be executed concurrently.
  int (*num_threads)(void* executor_context);

  /// Schedule `task` to be called, with `context` as its argument.
  void (*schedule)(void* executor_context, void* context,
                   void (*task)(void* context));
};

/**
 * Create a thread pool with the specified number of threads.
 *
 * @param  threads_count  the number of threads in the thread pool.
 *    A value of 0 has special interpretation: it creates a thread pool with as
 *    many threads as there are logical processors in the system.
 *
 * @return  A pointer to an opaque thread pool object if the call is
 *    successful, or NULL pointer if the call failed.
 */
pthreadpool_t pthreadpool_create(size_t threads_count);

/**
 * Create a thread pool with a given @a pthreadpool_executor and a maximum
 * specified number of threads.
 *
 * For each call to a `pthreadpool_parallelize_*` function, the minimum of @a
 * max_num_threads and @a executor->num_threads(executor_context) calls to @a
 * executor->schedule(executor_context, ...) will be executed, potentially
 * lasting for the entire duration of the `pthreadpool_parallelize_*` call.
 *
 * @param executor          A pointer to a @a pthreadpool_executor object that
 *                          will be used to determine the number of extra
 *                          threads (plus the calling thread), and provide the
 *                          threads itself, for each call to a
 *                          `pthreadpool_parallelize_*` function.
 * @param executor_context  A pointer to the context that will be passed to the
 *                          functions in the @a executor object.
 * @param max_num_thread    The maximum number of threads in the thread pool.
 *                          A value of 0 has special interpretation: it creates
 *                          a thread pool with as many threads as there are
 *                          logical processors in the system.
 *
 * @return  A pointer to an opaque thread pool object if the call is
 *    successful, or NULL pointer if the call failed.
 */
pthreadpool_t pthreadpool_create_v2(struct pthreadpool_executor* executor,
                                    void* executor_context,
                                    size_t max_num_threads);

/**
 * Query the number of threads in a thread pool.
 *
 * @param  threadpool  the thread pool to query.
 *
 * @return  The number of threads in the thread pool.
 */
size_t pthreadpool_get_threads_count(pthreadpool_t threadpool);

/**
 * Try to set the number of threads in a thread pool.
 *
 * The number of threads can be at most the number of threads with which the @a
 * threadpool was created, or the number of threads provided by the @a
 * pthreadpool_executor if the threadpool was created with @a
 * pthreadpool_create_v2.
 *
 * Trying to set a larger value will set and return the maximum possible value.
 *
 * @param  threadpool   The thread pool to query.
 * @param  num_threads  The desired number of threads. A value of 0 sets the
 *                      number of threads to the maximum available, i.e. the
 *                      value used when the @a threadpool was created.
 *
 * @return  The updated number of threads in the thread pool.
 */
size_t pthreadpool_set_threads_count(pthreadpool_t threadpool,
                                     size_t num_threads);

/**
 * Release any threads borrowed from an @a pthreadpool_executor.
 *
 * If the @a threadpool was created with @a pthreadpool_create_v2, this function
 * returns any threads acquired during execution to the associated @a
 * pthreadpool_executor.
 *
 * Threads will be re-acquired as needed on the next call to a
 * `pthreadpool_parallelize_*` function.
 *
 * If the @a threadpool was _not_ created with @a pthreadpool_create_v2, then
 * this function does nothing.
 *
 * @param  threadpool   the thread pool on which to release the executor
 *                      threads.
 */
void pthreadpool_release_executor_threads(struct pthreadpool* threadpool);

/**
 * Updates a thread pool with a given @a pthreadpool_executor.
 *
 * @param threadpool        The thread pool in which to replace the executor.
 * @param executor          A pointer to a @a pthreadpool_executor object that
 *                          will be used to determine the number of extra
 *                          threads (plus the calling thread), and provide the
 *                          threads itself, for each call to a
 *                          `pthreadpool_parallelize_*` function.
 * @param executor_context  A pointer to the context that will be passed to the
 *                          functions in the @a executor object.
 *
 * @return  @c true if the @a executor was successfully swapped, and @c false if
 * it was not, e.g. because the current and nex @a executor and @a
 * executor_context are identical.
 */
bool pthreadpool_update_executor(pthreadpool_t threadpool,
                                 struct pthreadpool_executor* executor,
                                 void* executor_context);

/**
 * Process items on a 1D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range; i++)
 *     function(context, i);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each item.
 * @param context     the first argument passed to the specified function.
 * @param range       the number of items on the 1D grid to process. The
 *    specified function will be called once for each item.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_1d(pthreadpool_t threadpool,
                                pthreadpool_task_1d_t function, void* context,
                                size_t range, uint32_t flags);

/**
 * Process items on a 1D grid passing along the current thread id.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range; i++)
 *     function(context, thread_index, i);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each item.
 * @param context     the first argument passed to the specified function.
 * @param range       the number of items on the 1D grid to process. The
 *    specified function will be called once for each item.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_1d_with_thread(
    pthreadpool_t threadpool, pthreadpool_task_1d_with_thread_t function,
    void* context, size_t range, uint32_t flags);

/**
 * Process items on a 1D grid using a microarchitecture-aware task function.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   uint32_t uarch_index = cpuinfo_initialize() ?
 *       cpuinfo_get_current_uarch_index() : default_uarch_index;
 *   if (uarch_index > max_uarch_index) uarch_index = default_uarch_index;
 *   for (size_t i = 0; i < range; i++)
 *     function(context, uarch_index, i);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *    threadpool is NULL, all items are processed serially on the calling
 *    thread.
 * @param function             the function to call for each item.
 * @param context              the first argument passed to the specified
 *    function.
 * @param default_uarch_index  the microarchitecture index to use when
 *    pthreadpool is configured without cpuinfo, cpuinfo initialization failed,
 *    or index returned by cpuinfo_get_current_uarch_index() exceeds the
 *    max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected by
 *    the specified function. If the index returned by
 *    cpuinfo_get_current_uarch_index() exceeds this value, default_uarch_index
 *    will be used instead. default_uarch_index can exceed max_uarch_index.
 * @param range                the number of items on the 1D grid to process.
 *    The specified function will be called once for each item.
 * @param flags                a bitwise combination of zero or more optional
 *    flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_1d_with_uarch(
    pthreadpool_t threadpool, pthreadpool_task_1d_with_id_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range, uint32_t flags);

/**
 * Process items on a 1D grid with specified maximum tile size.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range; i += tile)
 *     function(context, i, min(range - i, tile));
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range       the number of items on the 1D grid to process.
 * @param tile        the maximum number of items on the 1D grid to process in
 *    one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_1d_tile_1d(pthreadpool_t threadpool,
                                        pthreadpool_task_1d_tile_1d_t function,
                                        void* context, size_t range,
                                        size_t tile, uint32_t flags);

/**
 * Process items on a 1D grid with specified prefered tile size.
 *
 * The function repeatedly calls
 *
 *   function(context, i, count)
 *
 * in parallel where `i` is in the range `[0, range)` and a multiple of the
 * provided @a tile and `count` is an integer multiple of @a tile unless `i
 * + count == range`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range       the number of items on the 1D grid to process.
 * @param tile        the preferred multiple number of items on the 1D grid to
 *     process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_1d_tile_1d_dynamic(
    pthreadpool_t threadpool, pthreadpool_task_1d_tile_1d_dynamic_t function,
    void* context, size_t range, size_t tile, uint32_t flags);

/**
 * Process items on a 1D grid with specified prefered tile size, passing along
 * the current thread id.
 *
 * The function repeatedly calls
 *
 *   function(context, thread_id, i, count)
 *
 * in parallel where `i` is in the range `[0, range)` and a multiple of the
 * provided @a tile and `count` is an integer multiple of @a tile unless `i
 * + count == range`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range       the number of items on the 1D grid to process.
 * @param tile        the preferred multiple number of items on the 1D grid to
 *     process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_1d_tile_1d_dynamic_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_1d_tile_1d_dynamic_with_id_t function, void* context,
    size_t range, size_t tile, uint32_t flags);

/**
 * Process items on a 1D grid with specified prefered tile size, passing along
 * the current uarch index and thread id.
 *
 * The function repeatedly calls
 *
 *   function(context, uarch_index, thread_id, i, count)
 *
 * in parallel where `i` is in the range `[0, range)` and a multiple of the
 * provided @a tile and `count` is an integer multiple of @a tile unless `i
 * + count == range`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range       the number of items on the 1D grid to process.
 * @param tile        the preferred multiple number of items on the 1D grid to
 *     process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_1d_tile_1d_dynamic_with_uarch_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_1d_tile_1d_dynamic_with_id_with_thread_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range, size_t tile, uint32_t flags);

/**
 * Process items on a 2D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       function(context, i, j);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each item.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d(pthreadpool_t threadpool,
                                pthreadpool_task_2d_t function, void* context,
                                size_t range_i, size_t range_j, uint32_t flags);

/**
 * Process items on a 2D grid passing along the current thread id.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       function(context, thread_index, i, j);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each item.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_with_thread(
    pthreadpool_t threadpool, pthreadpool_task_2d_with_thread_t function,
    void* context, size_t range_i, size_t range_j, uint32_t flags);

/**
 * Process items on a 2D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       function(context, i, j, min(range_j - j, tile_j));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param tile_j      the maximum number of items along the second dimension of
 *    the 2D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_1d(pthreadpool_t threadpool,
                                        pthreadpool_task_2d_tile_1d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t tile_j,
                                        uint32_t flags);

/**
 * Process items on a 2D grid with the specified maximum tile size along the
 * last grid dimension using a microarchitecture-aware task function.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   uint32_t uarch_index = cpuinfo_initialize() ?
 *       cpuinfo_get_current_uarch_index() : default_uarch_index;
 *   if (uarch_index > max_uarch_index) uarch_index = default_uarch_index;
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       function(context, uarch_index, i, j, min(range_j - j, tile_j));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param default_uarch_index  the microarchitecture index to use when
 *    pthreadpool is configured without cpuinfo, cpuinfo initialization failed,
 *    or index returned by cpuinfo_get_current_uarch_index() exceeds the
 *    max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected by
 *    the specified function. If the index returned by
 *    cpuinfo_get_current_uarch_index() exceeds this value, default_uarch_index
 *    will be used instead. default_uarch_index can exceed max_uarch_index.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param tile_j      the maximum number of items along the second dimension of
 *    the 2D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_1d_with_uarch(
    pthreadpool_t threadpool, pthreadpool_task_2d_tile_1d_with_id_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range_i, size_t range_j, size_t tile_j, uint32_t flags);

/**
 * Process items on a 2D grid with the specified maximum tile size along the
 * last grid dimension using a microarchitecture-aware task function and passing
 * along the current thread id.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   uint32_t uarch_index = cpuinfo_initialize() ?
 *       cpuinfo_get_current_uarch_index() : default_uarch_index;
 *   if (uarch_index > max_uarch_index) uarch_index = default_uarch_index;
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       function(context, uarch_index, thread_index, i, j, min(range_j - j,
 * tile_j));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param default_uarch_index  the microarchitecture index to use when
 *    pthreadpool is configured without cpuinfo, cpuinfo initialization failed,
 *    or index returned by cpuinfo_get_current_uarch_index() exceeds the
 *    max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected by
 *    the specified function. If the index returned by
 *    cpuinfo_get_current_uarch_index() exceeds this value, default_uarch_index
 *    will be used instead. default_uarch_index can exceed max_uarch_index.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param tile_j      the maximum number of items along the second dimension of
 *    the 2D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_1d_with_uarch_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_2d_tile_1d_with_id_with_thread_t function, void* context,
    uint32_t default_uarch_index, uint32_t max_uarch_index, size_t range_i,
    size_t range_j, size_t tile_j, uint32_t flags);

/**
 * Process items on a 2D grid with specified prefered tile size along the
 * last grid dimension.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, count_j)
 *
 * in parallel where `i` is in the range `[0, range_i)`, `j` is in the range
 * `[0, range_j)` and a multiple of the provided @a tile_j, and `count_j` is an
 * integer multiple of @a tile_j unless `j + count_j == range_j`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i       the number of items on the first dimension of the 2D
 *     grid to process.
 * @param range_j       the number of items on the second dimension of the 2D
 *     grid to process.
 * @param tile_j        the preferred multiple number of items on the second
 *     dimension of the 2D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_1d_dynamic(
    pthreadpool_t threadpool, pthreadpool_task_2d_tile_1d_dynamic_t function,
    void* context, size_t range_i, size_t range_j, size_t tile_j,
    uint32_t flags);

/**
 * Process items on a 2D grid with specified prefered tile size along the
 * last grid dimension, passing along the current thread id.
 *
 * The function repeatedly calls
 *
 *   function(context, thread_id, i, j, count_j)
 *
 * in parallel where `i` is in the range `[0, range_i)`, `j` is in the range
 * `[0, range_j)` and a multiple of the provided @a tile_j, and `count_j` is an
 * integer multiple of @a tile_j unless `j + count_j == range_j`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i       the number of items on the first dimension of the 2D
 *     grid to process.
 * @param range_j       the number of items on the second dimension of the 2D
 *     grid to process.
 * @param tile_j        the preferred multiple number of items on the second
 *     dimension of the 2D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_1d_dynamic_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_2d_tile_1d_dynamic_with_id_t function, void* context,
    size_t range_i, size_t range_j, size_t tile_j, uint32_t flags);

/**
 * Process items on a 2D grid with specified prefered tile size along the
 * last grid dimension, passing along the current uarch index and thread id.
 *
 * The function repeatedly calls
 *
 *   function(context, uarch_index, thread_id, i, j, count_j)
 *
 * in parallel where `i` is in the range `[0, range_i)`, `j` is in the range
 * `[0, range_j)` and a multiple of the provided @a tile_j, and `count_j` is an
 * integer multiple of @a tile_j unless `j + count_j == range_j`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i       the number of items on the first dimension of the 2D
 *     grid to process.
 * @param range_j       the number of items on the second dimension of the 2D
 *     grid to process.
 * @param tile_j        the preferred multiple number of items on the second
 *     dimension of the 2D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_1d_dynamic_with_uarch_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_2d_tile_1d_dynamic_with_id_with_thread_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range_i, size_t range_j, size_t tile_j, uint32_t flags);

/**
 * Process items on a 2D grid with the specified maximum tile size along each
 * grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i += tile_i)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       function(context, i, j,
 *         min(range_i - i, tile_i), min(range_j - j, tile_j));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param tile_j      the maximum number of items along the first dimension of
 *    the 2D grid to process in one function call.
 * @param tile_j      the maximum number of items along the second dimension of
 *    the 2D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_2d(pthreadpool_t threadpool,
                                        pthreadpool_task_2d_tile_2d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t tile_i,
                                        size_t tile_j, uint32_t flags);

/**
 * Process items on a 2D grid with specified prefered tile size along each grid
 * dimension.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, count_i, count_j)
 *
 * in parallel where `i` is in the range `[0, range_i)` and a multiple of the
 * provided @a tile_i, `j` is in the range `[0, range_j)` and a multiple of the
 * provided @a tile_j, and `count_i` and `count_j` are integer multiples of @a
 * tile__i and @a tile_j, unless `i + count_i == range_i` or `j + count_j ==
 * range_j`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *                    is NULL, all items are processed serially on the calling
 *                    thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items on the first dimension of the 2D
 *                    grid to process.
 * @param range_j     the number of items on the second dimension of the 2D
 *                    grid to process.
 * @param tile_i      the preferred multiple number of items on the first
 *                    dimension of the 2D grid to process in each function call.
 * @param tile_j      the preferred multiple number of items on the second
 *                    dimension of the 2D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *                    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_2d_dynamic(
    pthreadpool_t threadpool, pthreadpool_task_2d_tile_2d_dynamic_t function,
    void* context, size_t range_i, size_t range_j, size_t tile_i, size_t tile_j,
    uint32_t flags);

/**
 * Process items on a 2D grid with specified prefered tile size along each grid
 * dimension using a microarchitecture-aware task function.
 *
 * The function repeatedly calls
 *
 *   function(context, uarch_index, i, j, count_i, count_j)
 *
 * in parallel where `i` is in the range `[0, range_i)` and a multiple of the
 * provided @a tile_i, `j` is in the range `[0, range_j)` and a multiple of the
 * provided @a tile_j, and `count_i` and `count_j` are integer multiples of @a
 * tile__i and @a tile_j, unless `i + count_i == range_i` or `j + count_j ==
 * range_j`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *                             threadpool is NULL, all items are processed
 *                             serially on the calling thread.
 * @param function             the function to call for each interval of the
 *                             given range.
 * @param context              the first argument passed to the specified
 *                             function.
 * @param default_uarch_index  the microarchitecture index to use when
 *                             pthreadpool is configured without cpuinfo,
 *                             cpuinfo initialization failed, or index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds
 *                             the max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected
 *                             by the specified function. If the index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds this
 *                             value, default_uarch_index will be used instead.
 *                             default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items on the first dimension of the
 *                             2D grid to process.
 * @param range_j              the number of items on the second dimension of
 *                             the 2D grid to process.
 * @param tile_i               the preferred multiple number of items on the
 *                             first dimension of the 2D grid to process in each
 *                             function call.
 * @param tile_j               the preferred multiple number of items on the
 *                             second dimension of the 2D grid to process in
 *                             each function call.
 * @param flags                a bitwise combination of zero or more optional
 *                             flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                             PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_2d_dynamic_with_uarch(
    pthreadpool_t threadpool,
    pthreadpool_task_2d_tile_2d_dynamic_with_id_t function, void* context,
    uint32_t default_uarch_index, uint32_t max_uarch_index, size_t range_i,
    size_t range_j, size_t tile_i, size_t tile_j, uint32_t flags);

/**
 * Process items on a 2D grid with specified prefered tile size along each grid
 * dimension passing along the current thread id.
 *
 * The function repeatedly calls
 *
 *   function(context, thread_id, i, j, count_i, count_j)
 *
 * in parallel where `i` is in the range `[0, range_i)` and a multiple of the
 * provided @a tile_i, `j` is in the range `[0, range_j)` and a multiple of the
 * provided @a tile_j, and `count_i` and `count_j` are integer multiples of @a
 * tile__i and @a tile_j, unless `i + count_i == range_i` or `j + count_j ==
 * range_j`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *                             threadpool is NULL, all items are processed
 *                             serially on the calling thread.
 * @param function             the function to call for each interval of the
 *                             given range.
 * @param context              the first argument passed to the specified
 *                             function.
 * @param range_i              the number of items on the first dimension of the
 *                             2D grid to process.
 * @param range_j              the number of items on the second dimension of
 *                             the 2D grid to process.
 * @param tile_i               the preferred multiple number of items on the
 *                             first dimension of the 2D grid to process in each
 *                             function call.
 * @param tile_j               the preferred multiple number of items on the
 *                             second dimension of the 2D grid to process in
 *                             each function call.
 * @param flags                a bitwise combination of zero or more optional
 *                             flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                             PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_2d_dynamic_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_2d_tile_2d_dynamic_with_id_t function, void* context,
    size_t range_i, size_t range_j, size_t tile_i, size_t tile_j,
    uint32_t flags);

/**
 * Process items on a 2D grid with the specified maximum tile size along each
 * grid dimension using a microarchitecture-aware task function.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   uint32_t uarch_index = cpuinfo_initialize() ?
 *       cpuinfo_get_current_uarch_index() : default_uarch_index;
 *   if (uarch_index > max_uarch_index) uarch_index = default_uarch_index;
 *   for (size_t i = 0; i < range_i; i += tile_i)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       function(context, uarch_index, i, j,
 *         min(range_i - i, tile_i), min(range_j - j, tile_j));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *    threadpool is NULL, all items are processed serially on the calling
 *    thread.
 * @param function             the function to call for each tile.
 * @param context              the first argument passed to the specified
 *    function.
 * @param default_uarch_index  the microarchitecture index to use when
 *                             pthreadpool is configured without cpuinfo,
 *                             cpuinfo initialization failed, or index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds
 *                             the max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected
 *                             by the specified function. If the index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds this
 *                             value, default_uarch_index will be used instead.
 *                             default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items to process along the first
 *    dimension of the 2D grid.
 * @param range_j              the number of items to process along the second
 *    dimension of the 2D grid.
 * @param tile_j               the maximum number of items along the first
 *    dimension of the 2D grid to process in one function call.
 * @param tile_j               the maximum number of items along the second
 *    dimension of the 2D grid to process in one function call.
 * @param flags                a bitwise combination of zero or more optional
 *    flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_2d_tile_2d_with_uarch(
    pthreadpool_t threadpool, pthreadpool_task_2d_tile_2d_with_id_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range_i, size_t range_j, size_t tile_i, size_t tile_j,
    uint32_t flags);

/**
 * Process items on a 3D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         function(context, i, j, k);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 3D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 3D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 3D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d(pthreadpool_t threadpool,
                                pthreadpool_task_3d_t function, void* context,
                                size_t range_i, size_t range_j, size_t range_k,
                                uint32_t flags);

/**
 * Process items on a 3D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         function(context, i, j, k, min(range_k - k, tile_k));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 3D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 3D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 3D grid.
 * @param tile_k      the maximum number of items along the third dimension of
 *    the 3D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_1d(pthreadpool_t threadpool,
                                        pthreadpool_task_3d_tile_1d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t range_k,
                                        size_t tile_k, uint32_t flags);

/**
 * Process items on a 3D grid with the specified maximum tile size along the
 * last grid dimension and passing along the current thread id.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         function(context, thread_index, i, j, k, min(range_k - k, tile_k));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 3D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 3D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 3D grid.
 * @param tile_k      the maximum number of items along the third dimension of
 *    the 3D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_1d_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_3d_tile_1d_with_thread_t function, void* context,
    size_t range_i, size_t range_j, size_t range_k, size_t tile_k,
    uint32_t flags);

/**
 * Process items on a 3D grid with the specified maximum tile size along the
 * last grid dimension using a microarchitecture-aware task function.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   uint32_t uarch_index = cpuinfo_initialize() ?
 *       cpuinfo_get_current_uarch_index() : default_uarch_index;
 *   if (uarch_index > max_uarch_index) uarch_index = default_uarch_index;
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         function(context, uarch_index, i, j, k, min(range_k - k, tile_k));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *    threadpool is NULL, all items are processed serially on the calling
 *    thread.
 * @param function             the function to call for each tile.
 * @param context              the first argument passed to the specified
 *    function.
 * @param default_uarch_index  the microarchitecture index to use when
 *    pthreadpool is configured without cpuinfo, cpuinfo initialization failed,
 *    or index returned by cpuinfo_get_current_uarch_index() exceeds the
 *    max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected by
 *    the specified function. If the index returned by
 *    cpuinfo_get_current_uarch_index() exceeds this value, default_uarch_index
 *    will be used instead. default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items to process along the first
 *    dimension of the 3D grid.
 * @param range_j              the number of items to process along the second
 *    dimension of the 3D grid.
 * @param range_k              the number of items to process along the third
 *    dimension of the 3D grid.
 * @param tile_k               the maximum number of items along the third
 *    dimension of the 3D grid to process in one function call.
 * @param flags                a bitwise combination of zero or more optional
 *    flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_1d_with_uarch(
    pthreadpool_t threadpool, pthreadpool_task_3d_tile_1d_with_id_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range_i, size_t range_j, size_t range_k, size_t tile_k,
    uint32_t flags);

/**
 * Process items on a 3D grid with the specified maximum tile size along the
 * last grid dimension using a microarchitecture-aware task function and passing
 * along the current thread id.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   uint32_t uarch_index = cpuinfo_initialize() ?
 *       cpuinfo_get_current_uarch_index() : default_uarch_index;
 *   if (uarch_index > max_uarch_index) uarch_index = default_uarch_index;
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         function(context, uarch_index, thread_index, i, j, k, min(range_k -
 * k, tile_k));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *    threadpool is NULL, all items are processed serially on the calling
 *    thread.
 * @param function             the function to call for each tile.
 * @param context              the first argument passed to the specified
 *    function.
 * @param default_uarch_index  the microarchitecture index to use when
 *    pthreadpool is configured without cpuinfo, cpuinfo initialization failed,
 *    or index returned by cpuinfo_get_current_uarch_index() exceeds the
 *    max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected by
 *    the specified function. If the index returned by
 *    cpuinfo_get_current_uarch_index() exceeds this value, default_uarch_index
 *    will be used instead. default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items to process along the first
 *    dimension of the 3D grid.
 * @param range_j              the number of items to process along the second
 *    dimension of the 3D grid.
 * @param range_k              the number of items to process along the third
 *    dimension of the 3D grid.
 * @param tile_k               the maximum number of items along the third
 *    dimension of the 3D grid to process in one function call.
 * @param flags                a bitwise combination of zero or more optional
 *    flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_1d_with_uarch_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_3d_tile_1d_with_id_with_thread_t function, void* context,
    uint32_t default_uarch_index, uint32_t max_uarch_index, size_t range_i,
    size_t range_j, size_t range_k, size_t tile_k, uint32_t flags);

/**
 * Process items on a 3D grid with specified prefered tile size along the last
 * grid dimension.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, k, count_k)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)`,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `count_k` is an integer multiple of @a tile_k, unless `k + count_k ==
 *    range_k`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *                             threadpool is NULL, all items are processed
 *                             serially on the calling thread.
 * @param function             the function to call for each interval of the
 *                             given range.
 * @param context              the first argument passed to the specified
 *                             function.
 * @param range_i              the number of items on the first dimension of the
 *                             3D grid to process.
 * @param range_j              the number of items on the second dimension of
 *                             the 3D grid to process.
 * @param range_k              the number of items on the third dimension of the
 *                             3D grid to process.
 * @param tile_k               the preferred multiple number of items on the
 *                             third dimension of the 3D grid to process in each
 *                             function call.
 * @param flags                a bitwise combination of zero or more optional
 *                             flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                             PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_1d_dynamic(
    pthreadpool_t threadpool,
    pthreadpool_task_3d_tile_1d_dynamic_t function, void* context,
    size_t range_i, size_t range_j, size_t range_k, size_t tile_k,
    uint32_t flags);

/**
 * Process items on a 3D grid with specified prefered tile size along the last
 * grid dimension, passing along the thread ID.
 *
 * The function repeatedly calls
 *
 *   function(context, thread_id, i, j, k, count_k)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)`,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `count_k` is an integer multiple of @a tile_k, unless `k + count_k ==
 *    range_k`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *                             threadpool is NULL, all items are processed
 *                             serially on the calling thread.
 * @param function             the function to call for each interval of the
 *                             given range.
 * @param context              the first argument passed to the specified
 *                             function.
 * @param range_i              the number of items on the first dimension of the
 *                             3D grid to process.
 * @param range_j              the number of items on the second dimension of
 *                             the 3D grid to process.
 * @param range_k              the number of items on the third dimension of the
 *                             3D grid to process.
 * @param tile_k               the preferred multiple number of items on the
 *                             third dimension of the 3D grid to process in each
 *                             function call.
 * @param flags                a bitwise combination of zero or more optional
 *                             flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                             PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_1d_dynamic_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_3d_tile_1d_dynamic_with_id_t function, void* context,
    size_t range_i, size_t range_j, size_t range_k, size_t tile_k,
    uint32_t flags);

/**
 * Process items on a 3D grid with specified prefered tile size along the last
 * grid dimension, passing along the thread ID.
 *
 * The function repeatedly calls
 *
 *   function(context, uarch_index, thread_index, i, j, k, count_k)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)`,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `count_k` is an integer multiple of @a tile_k, unless `k + count_k ==
 *    range_k`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *                             threadpool is NULL, all items are processed
 *                             serially on the calling thread.
 * @param function             the function to call for each interval of the
 *                             given range.
 * @param context              the first argument passed to the specified
 *                             function.
 * @param default_uarch_index  the microarchitecture index to use when
 *                             pthreadpool is configured without cpuinfo,
 *                             cpuinfo initialization failed, or index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds the
 *                             max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected by
 *                             the specified function. If the index returned by
 *                             cpuinfo_get_current_uarch_index() exceeds this
 *                             value, default_uarch_index will be used instead.
 *                             default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items on the first dimension of the
 *                             3D grid to process.
 * @param range_j              the number of items on the second dimension of
 *                             the 3D grid to process.
 * @param range_k              the number of items on the third dimension of the
 *                             3D grid to process.
 * @param tile_k               the preferred multiple number of items on the
 *                             third dimension of the 3D grid to process in each
 *                             function call.
 * @param flags                a bitwise combination of zero or more optional
 *                             flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                             PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_1d_dynamic_with_uarch_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_3d_tile_1d_dynamic_with_id_with_thread_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range_i, size_t range_j, size_t range_k, size_t tile_k,
    uint32_t flags);

/**
 * Process items on a 3D grid with the specified maximum tile size along the
 * last two grid dimensions.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         function(context, i, j, k,
 *           min(range_j - j, tile_j), min(range_k - k, tile_k));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 3D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 3D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 3D grid.
 * @param tile_j      the maximum number of items along the second dimension of
 *    the 3D grid to process in one function call.
 * @param tile_k      the maximum number of items along the third dimension of
 *    the 3D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_2d(pthreadpool_t threadpool,
                                        pthreadpool_task_3d_tile_2d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t range_k,
                                        size_t tile_j, size_t tile_k,
                                        uint32_t flags);

/**
 * Process items on a 3D grid with specified prefered tile size along the last
 * two grid dimensions.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, k, count_j, count_k)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)` and a multiple of the provided @a
 *    tile_j,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `count_j` and `count_k` are integer multiples of @a tile__j and @a tile_k,
 *    unless `j + count_j == range_j` or `k + count_k == range_k`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *                    is NULL, all items are processed serially on the calling
 *                    thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items on the first dimension of the 3D
 *                    grid to process.
 * @param range_j     the number of items on the second dimension of the 3D
 *                    grid to process.
 * @param range_k     the number of items on the third dimension of the 3D
 *                    grid to process.
 * @param tile_j      the preferred multiple number of items on the second
 *                    dimension of the 3D grid to process in each function call.
 * @param tile_k      the preferred multiple number of items on the third
 *                    dimension of the 3D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *                    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_2d_dynamic(
    pthreadpool_t threadpool, pthreadpool_task_3d_tile_2d_dynamic_t function,
    void* context, size_t range_i, size_t range_j, size_t range_k,
    size_t tile_j, size_t tile_k, uint32_t flags);

/**
 * Process items on a 3D grid with specified prefered tile size along the last
 * two grid dimensions using a microarchitecture-aware task function.
 *
 * The function repeatedly calls
 *
 *   function(context, uarch_index, i, j, k, count_j, count_k)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)` and a multiple of the provided @a
 *    tile_j,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `count_j` and `count_k` are integer multiples of @a tile__j and @a tile_k,
 *    unless `j + count_j == range_j` or `k + count_k == range_k`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *                             threadpool is NULL, all items are processed
 *                             serially on the calling thread.
 * @param function             the function to call for each interval of the
 *                             given range.
 * @param context              the first argument passed to the specified
 *                             function.
 * @param default_uarch_index  the microarchitecture index to use when
 *                             pthreadpool is configured without cpuinfo,
 *                             cpuinfo initialization failed, or index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds
 *                             the max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected
 *                             by the specified function. If the index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds this
 *                             value, default_uarch_index will be used instead.
 *                             default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items on the first dimension of the
 *                             3D grid to process.
 * @param range_j              the number of items on the second dimension of
 *                             the 3D grid to process.
 * @param range_k              the number of items on the third dimension of the
 *                             3D grid to process.
 * @param tile_j               the preferred multiple number of items on the
 *                             second dimension of the 3D grid to process in
 *                             each function call.
 * @param tile_k               the preferred multiple number of items on the
 *                             third dimension of the 3D grid to process in each
 *                             function call.
 * @param flags                a bitwise combination of zero or more optional
 *                             flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                             PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_2d_dynamic_with_uarch(
    pthreadpool_t threadpool,
    pthreadpool_task_3d_tile_2d_dynamic_with_id_t function, void* context,
    uint32_t default_uarch_index, uint32_t max_uarch_index, size_t range_i,
    size_t range_j, size_t range_k, size_t tile_j, size_t tile_k,
    uint32_t flags);

/**
 * Process items on a 3D grid with specified prefered tile size along the last
 * two grid dimensions passing along the thread ID.
 *
 * The function repeatedly calls
 *
 *   function(context, thread_id, i, j, k, count_j, count_k)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)` and a multiple of the provided @a
 *    tile_j,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `count_j` and `count_k` are integer multiples of @a tile__j and @a tile_k,
 *    unless `j + count_j == range_j` or `k + count_k == range_k`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *                             threadpool is NULL, all items are processed
 *                             serially on the calling thread.
 * @param function             the function to call for each interval of the
 *                             given range.
 * @param context              the first argument passed to the specified
 *                             function.
 * @param range_i              the number of items on the first dimension of the
 *                             3D grid to process.
 * @param range_j              the number of items on the second dimension of
 *                             the 3D grid to process.
 * @param range_k              the number of items on the third dimension of the
 *                             3D grid to process.
 * @param tile_j               the preferred multiple number of items on the
 *                             second dimension of the 3D grid to process in
 *                             each function call.
 * @param tile_k               the preferred multiple number of items on the
 *                             third dimension of the 3D grid to process in each
 *                             function call.
 * @param flags                a bitwise combination of zero or more optional
 *                             flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                             PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_2d_dynamic_with_thread(
    pthreadpool_t threadpool,
    pthreadpool_task_3d_tile_2d_dynamic_with_id_t function, void* context,
    size_t range_i, size_t range_j, size_t range_k, size_t tile_j,
    size_t tile_k, uint32_t flags);

/**
 * Process items on a 3D grid with the specified maximum tile size along the
 * last two grid dimensions using a microarchitecture-aware task function.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   uint32_t uarch_index = cpuinfo_initialize() ?
 *       cpuinfo_get_current_uarch_index() : default_uarch_index;
 *   if (uarch_index > max_uarch_index) uarch_index = default_uarch_index;
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         function(context, uarch_index, i, j, k,
 *           min(range_j - j, tile_j), min(range_k - k, tile_k));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *    threadpool is NULL, all items are processed serially on the calling
 *    thread.
 * @param function             the function to call for each tile.
 * @param context              the first argument passed to the specified
 *    function.
 * @param default_uarch_index  the microarchitecture index to use when
 *    pthreadpool is configured without cpuinfo, cpuinfo initialization failed,
 *    or index returned by cpuinfo_get_current_uarch_index() exceeds the
 *    max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected by
 *    the specified function. If the index returned by
 *    cpuinfo_get_current_uarch_index() exceeds this value, default_uarch_index
 *    will be used instead. default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items to process along the first
 *    dimension of the 3D grid.
 * @param range_j              the number of items to process along the second
 *    dimension of the 3D grid.
 * @param range_k              the number of items to process along the third
 *    dimension of the 3D grid.
 * @param tile_j               the maximum number of items along the second
 *    dimension of the 3D grid to process in one function call.
 * @param tile_k               the maximum number of items along the third
 *    dimension of the 3D grid to process in one function call.
 * @param flags                a bitwise combination of zero or more optional
 *    flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_3d_tile_2d_with_uarch(
    pthreadpool_t threadpool, pthreadpool_task_3d_tile_2d_with_id_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range_i, size_t range_j, size_t range_k, size_t tile_j,
    size_t tile_k, uint32_t flags);

/**
 * Process items on a 4D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           function(context, i, j, k, l);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 4D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 4D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 4D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 4D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_4d(pthreadpool_t threadpool,
                                pthreadpool_task_4d_t function, void* context,
                                size_t range_i, size_t range_j, size_t range_k,
                                size_t range_l, uint32_t flags);

/**
 * Process items on a 4D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l += tile_l)
 *           function(context, i, j, k, l, min(range_l - l, tile_l));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 4D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 4D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 4D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 4D grid.
 * @param tile_l      the maximum number of items along the fourth dimension of
 *    the 4D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_4d_tile_1d(pthreadpool_t threadpool,
                                        pthreadpool_task_4d_tile_1d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t range_k,
                                        size_t range_l, size_t tile_l,
                                        uint32_t flags);

/**
 * Process items on a 4D grid with the specified maximum tile size along the
 * last two grid dimensions.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         for (size_t l = 0; l < range_l; l += tile_l)
 *           function(context, i, j, k, l,
 *             min(range_k - k, tile_k), min(range_l - l, tile_l));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 4D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 4D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 4D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 4D grid.
 * @param tile_k      the maximum number of items along the third dimension of
 *    the 4D grid to process in one function call.
 * @param tile_l      the maximum number of items along the fourth dimension of
 *    the 4D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_4d_tile_2d(pthreadpool_t threadpool,
                                        pthreadpool_task_4d_tile_2d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t range_k,
                                        size_t range_l, size_t tile_k,
                                        size_t tile_l, uint32_t flags);

/**
 * Process items on a 4D grid with the specified maximum tile size along the
 * last two grid dimensions using a microarchitecture-aware task function.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   uint32_t uarch_index = cpuinfo_initialize() ?
 *       cpuinfo_get_current_uarch_index() : default_uarch_index;
 *   if (uarch_index > max_uarch_index) uarch_index = default_uarch_index;
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         for (size_t l = 0; l < range_l; l += tile_l)
 *           function(context, uarch_index, i, j, k, l,
 *             min(range_k - k, tile_k), min(range_l - l, tile_l));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *    threadpool is NULL, all items are processed serially on the calling
 *    thread.
 * @param function             the function to call for each tile.
 * @param context              the first argument passed to the specified
 *    function.
 * @param default_uarch_index  the microarchitecture index to use when
 *    pthreadpool is configured without cpuinfo, cpuinfo initialization failed,
 *    or index returned by cpuinfo_get_current_uarch_index() exceeds the
 *    max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected by
 *    the specified function. If the index returned by
 *    cpuinfo_get_current_uarch_index() exceeds this value, default_uarch_index
 *    will be used instead. default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items to process along the first
 *    dimension of the 4D grid.
 * @param range_j              the number of items to process along the second
 *    dimension of the 4D grid.
 * @param range_k              the number of items to process along the third
 *    dimension of the 4D grid.
 * @param range_l              the number of items to process along the fourth
 *    dimension of the 4D grid.
 * @param tile_k               the maximum number of items along the third
 *    dimension of the 4D grid to process in one function call.
 * @param tile_l               the maximum number of items along the fourth
 *    dimension of the 4D grid to process in one function call.
 * @param flags                a bitwise combination of zero or more optional
 *    flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_4d_tile_2d_with_uarch(
    pthreadpool_t threadpool, pthreadpool_task_4d_tile_2d_with_id_t function,
    void* context, uint32_t default_uarch_index, uint32_t max_uarch_index,
    size_t range_i, size_t range_j, size_t range_k, size_t range_l,
    size_t tile_k, size_t tile_l, uint32_t flags);

/**
 * Process items on a 4D grid with specified prefered tile size along the last
 * two grid dimensions.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, k, l, count_k, count_l)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)`,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `l` is in the range `[0, range_l)` and a multiple of the provided @a
 *    tile_l,
 *  - `count_k` and `count_l` are integer multiples of @a tile_k and @a tile_l,
 *    unless `k + count_k == range_k` or `l + count_l == range_l`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *                    is NULL, all items are processed serially on the calling
 *                    thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items on the first dimension of the 4D
 *                    grid to process.
 * @param range_j     the number of items on the second dimension of the 4D
 *                    grid to process.
 * @param range_k     the number of items on the third dimension of the 4D
 *                    grid to process.
 * @param range_l     the number of items on the fourth dimension of the 4D
 *                    grid to process.
 * @param tile_k      the preferred multiple number of items on the third
 *                    dimension of the 4D grid to process in each function call.
 * @param tile_l      the preferred multiple number of items on the fourth
 *                    dimension of the 4D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *                    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_4d_tile_2d_dynamic(
    pthreadpool_t threadpool, pthreadpool_task_4d_tile_2d_dynamic_t function,
    void* context, size_t range_i, size_t range_j, size_t range_k,
    size_t range_l, size_t tile_k, size_t tile_l, uint32_t flags);

/**
 * Process items on a 4D grid with specified prefered tile size along the last
 * two grid dimensions using a microarchitecture-aware task function.
 *
 * The function repeatedly calls
 *
 *   function(context, uarch_index, i, j, k, l, count_k, count_l)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)`,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `l` is in the range `[0, range_l)` and a multiple of the provided @a
 *    tile_l,
 *  - `count_k` and `count_l` are integer multiples of @a tile_k and @a tile_l,
 *    unless `k + count_k == range_k` or `l + count_l == range_l`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool           the thread pool to use for parallelisation. If
 *                             threadpool is NULL, all items are processed
 *                             serially on the calling thread.
 * @param function             the function to call for each interval of the
 *                             given range.
 * @param context              the first argument passed to the specified
 *                             function.
 * @param default_uarch_index  the microarchitecture index to use when
 *                             pthreadpool is configured without cpuinfo,
 *                             cpuinfo initialization failed, or index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds
 *                             the max_uarch_index value.
 * @param max_uarch_index      the maximum microarchitecture index expected
 *                             by the specified function. If the index returned
 *                             by cpuinfo_get_current_uarch_index() exceeds this
 *                             value, default_uarch_index will be used instead.
 *                             default_uarch_index can exceed max_uarch_index.
 * @param range_i              the number of items on the first dimension of the
 *                             4D grid to process.
 * @param range_j              the number of items on the second dimension of
 *                             the 4D grid to process.
 * @param range_k              the number of items on the third dimension of the
 *                             4D grid to process.
 * @param range_l              the number of items on the fourth dimension of
 *                             the 4D grid to process.
 * @param tile_k               the preferred multiple number of items on the
 *                             third dimension of the 4D grid to process in each
 *                             function call.
 * @param tile_l               the preferred multiple number of items on the
 *                             fourth dimension of the 4D grid to process in
 *                             each function call.
 * @param flags                a bitwise combination of zero or more optional
 *                             flags (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                             PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch(
    pthreadpool_t threadpool,
    pthreadpool_task_4d_tile_2d_dynamic_with_id_t function, void* context,
    uint32_t default_uarch_index, uint32_t max_uarch_index, size_t range_i,
    size_t range_j, size_t range_k, size_t range_l, size_t tile_k,
    size_t tile_l, uint32_t flags);

/**
 * Process items on a 5D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m++)
 *             function(context, i, j, k, l, m);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 5D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 5D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 5D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 5D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 5D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_5d(pthreadpool_t threadpool,
                                pthreadpool_task_5d_t function, void* context,
                                size_t range_i, size_t range_j, size_t range_k,
                                size_t range_l, size_t range_m, uint32_t flags);

/**
 * Process items on a 5D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m += tile_m)
 *             function(context, i, j, k, l, m, min(range_m - m, tile_m));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 5D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 5D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 5D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 5D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 5D grid.
 * @param tile_m      the maximum number of items along the fifth dimension of
 *    the 5D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_5d_tile_1d(pthreadpool_t threadpool,
                                        pthreadpool_task_5d_tile_1d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t range_k,
                                        size_t range_l, size_t range_m,
                                        size_t tile_m, uint32_t flags);

/**
 * Process items on a 5D grid with the specified maximum tile size along the
 * last two grid dimensions.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l += tile_l)
 *           for (size_t m = 0; m < range_m; m += tile_m)
 *             function(context, i, j, k, l, m,
 *               min(range_l - l, tile_l), min(range_m - m, tile_m));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 5D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 5D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 5D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 5D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 5D grid.
 * @param tile_l      the maximum number of items along the fourth dimension of
 *    the 5D grid to process in one function call.
 * @param tile_m      the maximum number of items along the fifth dimension of
 *    the 5D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_5d_tile_2d(pthreadpool_t threadpool,
                                        pthreadpool_task_5d_tile_2d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t range_k,
                                        size_t range_l, size_t range_m,
                                        size_t tile_l, size_t tile_m,
                                        uint32_t flags);

/**
 * Process items on a 6D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m++)
 *             for (size_t n = 0; n < range_n; n++)
 *               function(context, i, j, k, l, m, n);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 6D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 6D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 6D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 6D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 6D grid.
 * @param range_n     the number of items to process along the sixth dimension
 *    of the 6D grid.
 * @param tile_n      the maximum number of items along the sixth dimension of
 *    the 6D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_6d(pthreadpool_t threadpool,
                                pthreadpool_task_6d_t function, void* context,
                                size_t range_i, size_t range_j, size_t range_k,
                                size_t range_l, size_t range_m, size_t range_n,
                                uint32_t flags);

/**
 * Process items on a 6D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m++)
 *             for (size_t n = 0; n < range_n; n += tile_n)
 *               function(context, i, j, k, l, m, n, min(range_n - n, tile_n));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 6D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 6D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 6D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 6D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 6D grid.
 * @param range_n     the number of items to process along the sixth dimension
 *    of the 6D grid.
 * @param tile_n      the maximum number of items along the sixth dimension of
 *    the 6D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_6d_tile_1d(pthreadpool_t threadpool,
                                        pthreadpool_task_6d_tile_1d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t range_k,
                                        size_t range_l, size_t range_m,
                                        size_t range_n, size_t tile_n,
                                        uint32_t flags);

/**
 * Process items on a 6D grid with the specified maximum tile size along the
 * last two grid dimensions.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m += tile_m)
 *             for (size_t n = 0; n < range_n; n += tile_n)
 *               function(context, i, j, k, l, m, n,
 *                 min(range_m - m, tile_m), min(range_n - n, tile_n));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each tile.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items to process along the first dimension
 *    of the 6D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 6D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 6D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 6D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 6D grid.
 * @param range_n     the number of items to process along the sixth dimension
 *    of the 6D grid.
 * @param tile_m      the maximum number of items along the fifth dimension of
 *    the 6D grid to process in one function call.
 * @param tile_n      the maximum number of items along the sixth dimension of
 *    the 6D grid to process in one function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
void pthreadpool_parallelize_6d_tile_2d(pthreadpool_t threadpool,
                                        pthreadpool_task_6d_tile_2d_t function,
                                        void* context, size_t range_i,
                                        size_t range_j, size_t range_k,
                                        size_t range_l, size_t range_m,
                                        size_t range_n, size_t tile_m,
                                        size_t tile_n, uint32_t flags);

/**
 * Terminates threads in the thread pool and releases associated resources.
 *
 * @warning  Accessing the thread pool after a call to this function constitutes
 *    undefined behaviour and may cause data corruption.
 *
 * @param[in,out]  threadpool  The thread pool to destroy.
 */
void pthreadpool_destroy(pthreadpool_t threadpool);

#ifndef PTHREADPOOL_NO_DEPRECATED_API

/* Legacy API for compatibility with pre-existing users (e.g. NNPACK) */
#if defined(__GNUC__)
#define PTHREADPOOL_DEPRECATED __attribute__((__deprecated__))
#else
#define PTHREADPOOL_DEPRECATED
#endif

typedef void (*pthreadpool_function_1d_t)(void*, size_t);
typedef void (*pthreadpool_function_1d_tiled_t)(void*, size_t, size_t);
typedef void (*pthreadpool_function_2d_t)(void*, size_t, size_t);
typedef void (*pthreadpool_function_2d_tiled_t)(void*, size_t, size_t, size_t,
                                                size_t);
typedef void (*pthreadpool_function_3d_tiled_t)(void*, size_t, size_t, size_t,
                                                size_t, size_t, size_t);
typedef void (*pthreadpool_function_4d_tiled_t)(void*, size_t, size_t, size_t,
                                                size_t, size_t, size_t, size_t,
                                                size_t);

void pthreadpool_compute_1d(pthreadpool_t threadpool,
                            pthreadpool_function_1d_t function, void* argument,
                            size_t range) PTHREADPOOL_DEPRECATED;

void pthreadpool_compute_1d_tiled(pthreadpool_t threadpool,
                                  pthreadpool_function_1d_tiled_t function,
                                  void* argument, size_t range,
                                  size_t tile) PTHREADPOOL_DEPRECATED;

void pthreadpool_compute_2d(pthreadpool_t threadpool,
                            pthreadpool_function_2d_t function, void* argument,
                            size_t range_i,
                            size_t range_j) PTHREADPOOL_DEPRECATED;

void pthreadpool_compute_2d_tiled(pthreadpool_t threadpool,
                                  pthreadpool_function_2d_tiled_t function,
                                  void* argument, size_t range_i,
                                  size_t range_j, size_t tile_i,
                                  size_t tile_j) PTHREADPOOL_DEPRECATED;

void pthreadpool_compute_3d_tiled(pthreadpool_t threadpool,
                                  pthreadpool_function_3d_tiled_t function,
                                  void* argument, size_t range_i,
                                  size_t range_j, size_t range_k, size_t tile_i,
                                  size_t tile_j,
                                  size_t tile_k) PTHREADPOOL_DEPRECATED;

void pthreadpool_compute_4d_tiled(pthreadpool_t threadpool,
                                  pthreadpool_function_4d_tiled_t function,
                                  void* argument, size_t range_i,
                                  size_t range_j, size_t range_k,
                                  size_t range_l, size_t tile_i, size_t tile_j,
                                  size_t tile_k,
                                  size_t tile_l) PTHREADPOOL_DEPRECATED;

#endif /* PTHREADPOOL_NO_DEPRECATED_API */

#ifdef __cplusplus
} /* extern "C" */
#endif

#ifdef __cplusplus

namespace libpthreadpool {
namespace detail {
namespace {  // NOLINT: Naming this namespace would expose it.

template <class T>
void call_wrapper_1d(void* arg, size_t i) {
  (*static_cast<const T*>(arg))(i);
}

template <class T>
void call_wrapper_1d_tile_1d(void* arg, size_t range_i, size_t tile_i) {
  (*static_cast<const T*>(arg))(range_i, tile_i);
}

template <class T>
void call_wrapper_1d_tile_1d_dynamic(void* arg, size_t range_i, size_t tile_i) {
  (*static_cast<const T*>(arg))(range_i, tile_i);
}

template <class T>
void call_wrapper_2d(void* functor, size_t i, size_t j) {
  (*static_cast<const T*>(functor))(i, j);
}

template <class T>
void call_wrapper_2d_tile_1d(void* functor, size_t i, size_t range_j,
                             size_t tile_j) {
  (*static_cast<const T*>(functor))(i, range_j, tile_j);
}

template <class T>
void call_wrapper_2d_tile_1d_dynamic(void* functor, size_t i, size_t range_j,
                                     size_t tile_j) {
  (*static_cast<const T*>(functor))(i, range_j, tile_j);
}

template <class T>
void call_wrapper_2d_tile_2d(void* functor, size_t range_i, size_t range_j,
                             size_t tile_i, size_t tile_j) {
  (*static_cast<const T*>(functor))(range_i, range_j, tile_i, tile_j);
}

template <class T>
void call_wrapper_2d_tile_2d_dynamic(void* functor, size_t range_i,
                                     size_t range_j, size_t tile_i,
                                     size_t tile_j) {
  (*static_cast<const T*>(functor))(range_i, range_j, tile_i, tile_j);
}

template <class T>
void call_wrapper_3d(void* functor, size_t i, size_t j, size_t k) {
  (*static_cast<const T*>(functor))(i, j, k);
}

template <class T>
void call_wrapper_3d_tile_1d(void* functor, size_t i, size_t j, size_t range_k,
                             size_t tile_k) {
  (*static_cast<const T*>(functor))(i, j, range_k, tile_k);
}

template <class T>
void call_wrapper_3d_tile_2d(void* functor, size_t i, size_t range_j,
                             size_t range_k, size_t tile_j, size_t tile_k) {
  (*static_cast<const T*>(functor))(i, range_j, range_k, tile_j, tile_k);
}

template <class T>
void call_wrapper_3d_tile_2d_dynamic(void* functor, size_t i, size_t range_j,
                                     size_t range_k, size_t tile_j,
                                     size_t tile_k) {
  (*static_cast<const T*>(functor))(i, range_j, range_k, tile_j, tile_k);
}

template <class T>
void call_wrapper_4d(void* functor, size_t i, size_t j, size_t k, size_t l) {
  (*static_cast<const T*>(functor))(i, j, k, l);
}

template <class T>
void call_wrapper_4d_tile_1d(void* functor, size_t i, size_t j, size_t k,
                             size_t range_l, size_t tile_l) {
  (*static_cast<const T*>(functor))(i, j, k, range_l, tile_l);
}

template <class T>
void call_wrapper_4d_tile_2d(void* functor, size_t i, size_t j, size_t range_k,
                             size_t range_l, size_t tile_k, size_t tile_l) {
  (*static_cast<const T*>(functor))(i, j, range_k, range_l, tile_k, tile_l);
}

template <class T>
void call_wrapper_4d_tile_2d_dynamic(void* functor, size_t i, size_t j,
                                     size_t range_k, size_t range_l,
                                     size_t tile_k, size_t tile_l) {
  (*static_cast<const T*>(functor))(i, j, range_k, range_l, tile_k, tile_l);
}

template <class T>
void call_wrapper_5d(void* functor, size_t i, size_t j, size_t k, size_t l,
                     size_t m) {
  (*static_cast<const T*>(functor))(i, j, k, l, m);
}

template <class T>
void call_wrapper_5d_tile_1d(void* functor, size_t i, size_t j, size_t k,
                             size_t l, size_t range_m, size_t tile_m) {
  (*static_cast<const T*>(functor))(i, j, k, l, range_m, tile_m);
}

template <class T>
void call_wrapper_5d_tile_2d(void* functor, size_t i, size_t j, size_t k,
                             size_t range_l, size_t range_m, size_t tile_l,
                             size_t tile_m) {
  (*static_cast<const T*>(functor))(i, j, k, range_l, range_m, tile_l, tile_m);
}

template <class T>
void call_wrapper_6d(void* functor, size_t i, size_t j, size_t k, size_t l,
                     size_t m, size_t n) {
  (*static_cast<const T*>(functor))(i, j, k, l, m, n);
}

template <class T>
void call_wrapper_6d_tile_1d(void* functor, size_t i, size_t j, size_t k,
                             size_t l, size_t m, size_t range_n,
                             size_t tile_n) {
  (*static_cast<const T*>(functor))(i, j, k, l, m, range_n, tile_n);
}

template <class T>
void call_wrapper_6d_tile_2d(void* functor, size_t i, size_t j, size_t k,
                             size_t l, size_t range_m, size_t range_n,
                             size_t tile_m, size_t tile_n) {
  (*static_cast<const T*>(functor))(i, j, k, l, range_m, range_n, tile_m,
                                    tile_n);
}

} /* namespace */
} /* namespace detail */
} /* namespace libpthreadpool */

/**
 * Drop-in wrapper for the @a pthreadpool_scheduler that uses itself as its own
 * context.
 */
class PthreadpoolExecutor : public pthreadpool_executor {
 public:
  using TaskFunction = void (*)(void*);

  PthreadpoolExecutor() {
    num_threads = num_threads_impl;
    schedule = schedule_impl;
  }
  virtual ~PthreadpoolExecutor() = default;

  /**
   * Return the context of this @a PthreadpoolExecutor, e.g. for the @a
   * pthreadpool_create_v2 function.
   */
  void* GetContext() { return this; }

  /**
   * Override these methods for your own threadpool.
   */
  virtual int NumThreads() = 0;
  virtual void Schedule(void* context, TaskFunction task) = 0;

 private:
  static int num_threads_impl(void* executor) {
    return reinterpret_cast<PthreadpoolExecutor*>(executor)->NumThreads();
  }

  static void schedule_impl(void* executor, void* context, TaskFunction task) {
    reinterpret_cast<PthreadpoolExecutor*>(executor)->Schedule(context, task);
  }
};

/**
 * Process items on a 1D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range; i++)
 *     functor(i);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each item.
 * @param range       the number of items on the 1D grid to process. The
 *    specified functor will be called once for each item.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_1d(pthreadpool_t threadpool,
                                       const T& functor, size_t range,
                                       uint32_t flags = 0) {
  pthreadpool_parallelize_1d(
      threadpool, &libpthreadpool::detail::call_wrapper_1d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range, flags);
}

/**
 * Process items on a 1D grid with specified maximum tile size.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range; i += tile)
 *     functor(i, min(range - i, tile));
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range       the number of items on the 1D grid to process.
 * @param tile        the maximum number of items on the 1D grid to process in
 *    one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_1d_tile_1d(pthreadpool_t threadpool,
                                               const T& functor, size_t range,
                                               size_t tile,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_1d_tile_1d(
      threadpool, &libpthreadpool::detail::call_wrapper_1d_tile_1d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range, tile,
      flags);
}

/**
 * Process items on a 1D grid with specified prefered tile size.
 *
 * The function repeatedly calls
 *
 *   function(context, i, count)
 *
 * in parallel where `i` is in the range `[0, range)` and a multiple of the
 * provided @a tile and `count` is an integer multiple of @a tile unless `i
 * + count == range`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range       the number of items on the 1D grid to process.
 * @param tile        the preferred multiple number of items on the 1D grid to
 *     process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_1d_tile_1d_dynamic(pthreadpool_t threadpool,
                                                       const T& functor,
                                                       size_t range,
                                                       size_t tile,
                                                       uint32_t flags = 0) {
  pthreadpool_parallelize_1d_tile_1d_dynamic(
      threadpool,
      &libpthreadpool::detail::call_wrapper_1d_tile_1d_dynamic<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range, tile,
      flags);
}

/**
 * Process items on a 2D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       functor(i, j);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each item.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_2d(pthreadpool_t threadpool,
                                       const T& functor, size_t range_i,
                                       size_t range_j, uint32_t flags = 0) {
  pthreadpool_parallelize_2d(
      threadpool, &libpthreadpool::detail::call_wrapper_2d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      flags);
}

/**
 * Process items on a 2D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       functor(i, j, min(range_j - j, tile_j));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param tile_j      the maximum number of items along the second dimension of
 *    the 2D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_2d_tile_1d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t tile_j,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_2d_tile_1d(
      threadpool, &libpthreadpool::detail::call_wrapper_2d_tile_1d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      tile_j, flags);
}

/**
 * Process items on a 2D grid with specified prefered tile size along the
 * last grid dimension.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, count_j)
 *
 * in parallel where `i` is in the range `[0, range_i)`, `j` is in the range
 * `[0, range_j)` and a multiple of the provided @a tile_j, and `count_j` is an
 * integer multiple of @a tile_j unless `j + count_j == range_j`.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i       the number of items on the first dimension of the 2D
 *     grid to process.
 * @param range_j       the number of items on the second dimension of the 2D
 *     grid to process.
 * @param tile_j        the preferred multiple number of items on the second
 *     dimension of the 2D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_2d_tile_1d_dynamic(
    pthreadpool_t threadpool, const T& functor, size_t range_i, size_t range_j,
    size_t tile_j, uint32_t flags = 0) {
  pthreadpool_parallelize_2d_tile_1d_dynamic(
      threadpool,
      &libpthreadpool::detail::call_wrapper_2d_tile_1d_dynamic<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      tile_j, flags);
}

/**
 * Process items on a 2D grid with the specified maximum tile size along each
 * grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i += tile_i)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       functor(i, j,
 *         min(range_i - i, tile_i), min(range_j - j, tile_j));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 2D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 2D grid.
 * @param tile_j      the maximum number of items along the first dimension of
 *    the 2D grid to process in one functor call.
 * @param tile_j      the maximum number of items along the second dimension of
 *    the 2D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_2d_tile_2d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t tile_i,
                                               size_t tile_j,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_2d_tile_2d(
      threadpool, &libpthreadpool::detail::call_wrapper_2d_tile_2d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      tile_i, tile_j, flags);
}

/**
 * Process items on a 2D grid with specified prefered tile size along each grid
 * dimension.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, count_i, count_j)
 *
 * in parallel where `i` is in the range `[0, range_i)` and a multiple of the
 * provided @a tile_i, `j` is in the range `[0, range_j)` and a multiple of the
 * provided @a tile_j, and `count_i` and `count_j` are integer multiples of @a
 * tile__i and @a tile_j, unless `i + count_i == range_i` or `j + count_j ==
 * range_j`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i       the number of items on the first dimension of the 2D
 *     grid to process.
 * @param range_j       the number of items on the second dimension of the 2D
 *     grid to process.
 * @param tile_i        the preferred multiple number of items on the first
 *     dimension of the 2D grid to process in each function call.
 * @param tile_j        the preferred multiple number of items on the second
 *     dimension of the 2D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_2d_tile_2d_dynamic(
    pthreadpool_t threadpool, const T& functor, size_t range_i, size_t range_j,
    size_t tile_i, size_t tile_j, uint32_t flags = 0) {
  pthreadpool_parallelize_2d_tile_2d_dynamic(
      threadpool,
      &libpthreadpool::detail::call_wrapper_2d_tile_2d_dynamic<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      tile_i, tile_j, flags);
}

/**
 * Process items on a 3D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         functor(i, j, k);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 3D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 3D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 3D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_3d(pthreadpool_t threadpool,
                                       const T& functor, size_t range_i,
                                       size_t range_j, size_t range_k,
                                       uint32_t flags = 0) {
  pthreadpool_parallelize_3d(
      threadpool, &libpthreadpool::detail::call_wrapper_3d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, flags);
}

/**
 * Process items on a 3D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         functor(i, j, k, min(range_k - k, tile_k));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 3D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 3D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 3D grid.
 * @param tile_k      the maximum number of items along the third dimension of
 *    the 3D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_3d_tile_1d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t range_k,
                                               size_t tile_k,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_3d_tile_1d(
      threadpool, &libpthreadpool::detail::call_wrapper_3d_tile_1d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, tile_k, flags);
}

/**
 * Process items on a 3D grid with the specified maximum tile size along the
 * last two grid dimensions.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j += tile_j)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         functor(i, j, k,
 *           min(range_j - j, tile_j), min(range_k - k, tile_k));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 3D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 3D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 3D grid.
 * @param tile_j      the maximum number of items along the second dimension of
 *    the 3D grid to process in one functor call.
 * @param tile_k      the maximum number of items along the third dimension of
 *    the 3D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_3d_tile_2d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t range_k,
                                               size_t tile_j, size_t tile_k,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_3d_tile_2d(
      threadpool, &libpthreadpool::detail::call_wrapper_3d_tile_2d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, tile_j, tile_k, flags);
}

/**
 * Process items on a 3D grid with specified prefered tile size along the last
 * two grid dimensions.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, k, count_j, count_k)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)` and a multiple of the provided @a
 *    tile_j,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `count_j` and `count_k` are integer multiples of @a tile__j and @a tile_k,
 *    unless `j + count_j == range_j` or `k + count_k == range_k`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i       the number of items on the first dimension of the 3D
 *     grid to process.
 * @param range_j       the number of items on the second dimension of the 3D
 *     grid to process.
 * @param range_k       the number of items on the third dimension of the 3D
 *     grid to process.
 * @param tile_j        the preferred multiple number of items on the second
 *     dimension of the 3D grid to process in each function call.
 * @param tile_k        the preferred multiple number of items on the third
 *     dimension of the 3D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_3d_tile_2d_dynamic(
    pthreadpool_t threadpool, const T& functor, size_t range_i, size_t range_j,
    size_t range_k, size_t tile_j, size_t tile_k, uint32_t flags = 0) {
  pthreadpool_parallelize_3d_tile_2d_dynamic(
      threadpool,
      &libpthreadpool::detail::call_wrapper_3d_tile_2d_dynamic<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, tile_j, tile_k, flags);
}

/**
 * Process items on a 4D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           functor(i, j, k, l);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 4D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 4D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 4D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 4D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_4d(pthreadpool_t threadpool,
                                       const T& functor, size_t range_i,
                                       size_t range_j, size_t range_k,
                                       size_t range_l, uint32_t flags = 0) {
  pthreadpool_parallelize_4d(
      threadpool, &libpthreadpool::detail::call_wrapper_4d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, flags);
}

/**
 * Process items on a 4D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l += tile_l)
 *           functor(i, j, k, l, min(range_l - l, tile_l));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 4D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 4D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 4D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 4D grid.
 * @param tile_l      the maximum number of items along the fourth dimension of
 *    the 4D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_4d_tile_1d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t range_k,
                                               size_t range_l, size_t tile_l,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_4d_tile_1d(
      threadpool, &libpthreadpool::detail::call_wrapper_4d_tile_1d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, tile_l, flags);
}

/**
 * Process items on a 4D grid with the specified maximum tile size along the
 * last two grid dimensions.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k += tile_k)
 *         for (size_t l = 0; l < range_l; l += tile_l)
 *           functor(i, j, k, l,
 *             min(range_k - k, tile_k), min(range_l - l, tile_l));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 4D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 4D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 4D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 4D grid.
 * @param tile_k      the maximum number of items along the third dimension of
 *    the 4D grid to process in one functor call.
 * @param tile_l      the maximum number of items along the fourth dimension of
 *    the 4D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_4d_tile_2d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t range_k,
                                               size_t range_l, size_t tile_k,
                                               size_t tile_l,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_4d_tile_2d(
      threadpool, &libpthreadpool::detail::call_wrapper_4d_tile_2d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, tile_k, tile_l, flags);
}

/**
 * Process items on a 4D grid with specified prefered tile size along the last
 * two grid dimensions.
 *
 * The function repeatedly calls
 *
 *   function(context, i, j, k, l, count_k, count_l)
 *
 * in parallel where:
 *  - `i` is in the range `[0, range_i)`,
 *  - `j` is in the range `[0, range_j)`,
 *  - `k` is in the range `[0, range_k)` and a multiple of the provided @a
 *    tile_k,
 *  - `l` is in the range `[0, range_l)` and a multiple of the provided @a
 *    tile_l,
 *  - `count_k` and `count_l` are integer multiples of @a tile_k and @a tile_l,
 *    unless `k + count_k == range_k` or `l + count_l == range_l`, respectivly.
 *
 * The `count`s are chosen such as to minimize the number of calls to @a
 * function while keeping the computation load balanced across all threads.
 *
 * When the call returns, all items have been processed and the thread pool is
 * ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool,
 *    the calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *                    is NULL, all items are processed serially on the calling
 *                    thread.
 * @param function    the function to call for each interval of the given range.
 * @param context     the first argument passed to the specified function.
 * @param range_i     the number of items on the first dimension of the 4D
 *                    grid to process.
 * @param range_j     the number of items on the second dimension of the 4D
 *                    grid to process.
 * @param range_k     the number of items on the third dimension of the 4D
 *                    grid to process.
 * @param range_l     the number of items on the fourth dimension of the 4D
 *                    grid to process.
 * @param tile_k      the preferred multiple number of items on the third
 *                    dimension of the 4D grid to process in each function call.
 * @param tile_l      the preferred multiple number of items on the fourth
 *                    dimension of the 4D grid to process in each function call.
 * @param flags       a bitwise combination of zero or more optional flags
 *                    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or
 *                    PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_4d_tile_2d_dynamic(
    pthreadpool_t threadpool, const T& functor, size_t range_i, size_t range_j,
    size_t range_k, size_t range_l, size_t tile_k, size_t tile_l,
    uint32_t flags = 0) {
  pthreadpool_parallelize_3d_tile_2d_dynamic(
      threadpool,
      &libpthreadpool::detail::call_wrapper_4d_tile_2d_dynamic<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, tile_k, tile_l, flags);
}

/**
 * Process items on a 5D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m++)
 *             functor(i, j, k, l, m);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 5D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 5D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 5D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 5D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 5D grid.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_5d(pthreadpool_t threadpool,
                                       const T& functor, size_t range_i,
                                       size_t range_j, size_t range_k,
                                       size_t range_l, size_t range_m,
                                       uint32_t flags = 0) {
  pthreadpool_parallelize_5d(
      threadpool, &libpthreadpool::detail::call_wrapper_5d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, range_m, flags);
}

/**
 * Process items on a 5D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m += tile_m)
 *             functor(i, j, k, l, m, min(range_m - m, tile_m));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 5D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 5D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 5D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 5D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 5D grid.
 * @param tile_m      the maximum number of items along the fifth dimension of
 *    the 5D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_5d_tile_1d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t range_k,
                                               size_t range_l, size_t range_m,
                                               size_t tile_m,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_5d_tile_1d(
      threadpool, &libpthreadpool::detail::call_wrapper_5d_tile_1d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, range_m, tile_m, flags);
}

/**
 * Process items on a 5D grid with the specified maximum tile size along the
 * last two grid dimensions.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l += tile_l)
 *           for (size_t m = 0; m < range_m; m += tile_m)
 *             functor(i, j, k, l, m,
 *               min(range_l - l, tile_l), min(range_m - m, tile_m));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 5D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 5D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 5D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 5D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 5D grid.
 * @param tile_l      the maximum number of items along the fourth dimension of
 *    the 5D grid to process in one functor call.
 * @param tile_m      the maximum number of items along the fifth dimension of
 *    the 5D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_5d_tile_2d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t range_k,
                                               size_t range_l, size_t range_m,
                                               size_t tile_l, size_t tile_m,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_5d_tile_2d(
      threadpool, &libpthreadpool::detail::call_wrapper_5d_tile_2d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, range_m, tile_l, tile_m, flags);
}

/**
 * Process items on a 6D grid.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m++)
 *             for (size_t n = 0; n < range_n; n++)
 *               functor(i, j, k, l, m, n);
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 6D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 6D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 6D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 6D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 6D grid.
 * @param range_n     the number of items to process along the sixth dimension
 *    of the 6D grid.
 * @param tile_n      the maximum number of items along the sixth dimension of
 *    the 6D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_6d(pthreadpool_t threadpool,
                                       const T& functor, size_t range_i,
                                       size_t range_j, size_t range_k,
                                       size_t range_l, size_t range_m,
                                       size_t range_n, uint32_t flags = 0) {
  pthreadpool_parallelize_6d(
      threadpool, &libpthreadpool::detail::call_wrapper_6d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, range_m, range_n, flags);
}

/**
 * Process items on a 6D grid with the specified maximum tile size along the
 * last grid dimension.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m++)
 *             for (size_t n = 0; n < range_n; n += tile_n)
 *               functor(i, j, k, l, m, n, min(range_n - n, tile_n));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 6D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 6D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 6D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 6D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 6D grid.
 * @param range_n     the number of items to process along the sixth dimension
 *    of the 6D grid.
 * @param tile_n      the maximum number of items along the sixth dimension of
 *    the 6D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_6d_tile_1d(pthreadpool_t threadpool,
                                               const T& functor, size_t range_i,
                                               size_t range_j, size_t range_k,
                                               size_t range_l, size_t range_m,
                                               size_t range_n, size_t tile_n,
                                               uint32_t flags = 0) {
  pthreadpool_parallelize_6d_tile_1d(
      threadpool, &libpthreadpool::detail::call_wrapper_6d_tile_1d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, range_m, range_n, tile_n, flags);
}

/**
 * Process items on a 6D grid with the specified maximum tile size along the
 * last two grid dimensions.
 *
 * The function implements a parallel version of the following snippet:
 *
 *   for (size_t i = 0; i < range_i; i++)
 *     for (size_t j = 0; j < range_j; j++)
 *       for (size_t k = 0; k < range_k; k++)
 *         for (size_t l = 0; l < range_l; l++)
 *           for (size_t m = 0; m < range_m; m += tile_m)
 *             for (size_t n = 0; n < range_n; n += tile_n)
 *               functor(i, j, k, l, m, n,
 *                 min(range_m - m, tile_m), min(range_n - n, tile_n));
 *
 * When the function returns, all items have been processed and the thread pool
 * is ready for a new task.
 *
 * @note If multiple threads call this function with the same thread pool, the
 *    calls are serialized.
 *
 * @param threadpool  the thread pool to use for parallelisation. If threadpool
 *    is NULL, all items are processed serially on the calling thread.
 * @param functor     the functor to call for each tile.
 * @param range_i     the number of items to process along the first dimension
 *    of the 6D grid.
 * @param range_j     the number of items to process along the second dimension
 *    of the 6D grid.
 * @param range_k     the number of items to process along the third dimension
 *    of the 6D grid.
 * @param range_l     the number of items to process along the fourth dimension
 *    of the 6D grid.
 * @param range_m     the number of items to process along the fifth dimension
 *    of the 6D grid.
 * @param range_n     the number of items to process along the sixth dimension
 *    of the 6D grid.
 * @param tile_m      the maximum number of items along the fifth dimension of
 *    the 6D grid to process in one functor call.
 * @param tile_n      the maximum number of items along the sixth dimension of
 *    the 6D grid to process in one functor call.
 * @param flags       a bitwise combination of zero or more optional flags
 *    (PTHREADPOOL_FLAG_DISABLE_DENORMALS or PTHREADPOOL_FLAG_DONT_SPIN_WORKERS)
 */
template <class T>
inline void pthreadpool_parallelize_6d_tile_2d(
    pthreadpool_t threadpool, const T& functor, size_t range_i, size_t range_j,
    size_t range_k, size_t range_l, size_t range_m, size_t range_n,
    size_t tile_m, size_t tile_n, uint32_t flags = 0) {
  pthreadpool_parallelize_6d_tile_2d(
      threadpool, &libpthreadpool::detail::call_wrapper_6d_tile_2d<const T>,
      const_cast<void*>(static_cast<const void*>(&functor)), range_i, range_j,
      range_k, range_l, range_m, range_n, tile_m, tile_n, flags);
}

#endif /* __cplusplus */

#endif /* __PTHREADPOOL_INCLUDE_PTHREADPOOL_H_ */
