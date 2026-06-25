// Copyright (c) 2017 Facebook Inc.
// Copyright (c) 2015-2017 Georgia Institute of Technology
// All rights reserved.
//
// Copyright 2019 Google LLC
//
// This source code is licensed under the BSD-style license found in the
// LICENSE file in the root directory of this source tree.

#ifndef __PTHREADPOOL_SRC_THREADPOOL_ATOMICS_H_
#define __PTHREADPOOL_SRC_THREADPOOL_ATOMICS_H_

/* Standard C headers */
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Windows-specific headers */
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <sched.h>
#endif

/* SSE-specific headers */
#if defined(__i386__) || defined(__i686__) || defined(__x86_64__) || \
    defined(_M_IX86) || defined(_M_X64) && !defined(_M_ARM64EC)
#include <xmmintrin.h>
#endif

/* ARM-specific headers */
#if defined(__ARM_ACLE)
#include <arm_acle.h>
#endif

/* MSVC-specific headers */
#ifdef _MSC_VER
#include <intrin.h>
#endif

/* Configuration header */
#include "threadpool-common.h"

/* POSIX headers */
#if PTHREADPOOL_USE_PTHREADS
#include <pthread.h>
#else
#include <threads.h>
#endif

#if PTHREADPOOL_USE_PTHREADS
typedef pthread_t pthreadpool_thread_t;
typedef pthread_mutex_t pthreadpool_mutex_t;
typedef pthread_cond_t pthreadpool_cond_t;
typedef void* pthreadpool_thread_return_t;
static inline void pthreadpool_mutex_init(pthreadpool_mutex_t* mutex) {
  pthread_mutex_init(mutex, NULL);
}
static inline void pthreadpool_mutex_destroy(pthreadpool_mutex_t* mutex) {
  pthread_mutex_destroy(mutex);
}
static inline void pthreadpool_mutex_lock(pthreadpool_mutex_t* mutex) {
  pthread_mutex_lock(mutex);
}
static inline void pthreadpool_mutex_unlock(pthreadpool_mutex_t* mutex) {
  pthread_mutex_unlock(mutex);
}
static inline void pthreadpool_cond_init(pthreadpool_cond_t* cond) {
  pthread_cond_init(cond, NULL);
}
static inline void pthreadpool_cond_destroy(pthreadpool_cond_t* cond) {
  pthread_cond_destroy(cond);
}
static inline void pthreadpool_cond_wait(pthreadpool_cond_t* cond,
                                         pthreadpool_mutex_t* mutex) {
  pthread_cond_wait(cond, mutex);
}
static inline void pthreadpool_cond_signal(pthreadpool_cond_t* cond) {
  pthread_cond_signal(cond);
}
static inline void pthreadpool_cond_broadcast(pthreadpool_cond_t* cond) {
  pthread_cond_broadcast(cond);
}
static inline void pthreadpool_thread_create(
    pthreadpool_thread_t* thread, pthreadpool_thread_return_t(fun)(void*),
    void* arg) {
  pthread_create(thread, NULL, fun, arg);
}
static inline void pthreadpool_thread_join(
    pthreadpool_thread_t thread, pthreadpool_thread_return_t* return_value) {
  pthread_join(thread, return_value);
}
#else
typedef thrd_t pthreadpool_thread_t;
typedef mtx_t pthreadpool_mutex_t;
typedef cnd_t pthreadpool_cond_t;
typedef int pthreadpool_thread_return_t;
static inline void pthreadpool_mutex_init(pthreadpool_mutex_t* mutex) {
  mtx_init(mutex, mtx_plain);
}
static inline void pthreadpool_mutex_destroy(pthreadpool_mutex_t* mutex) {
  mtx_destroy(mutex);
}
static inline void pthreadpool_mutex_lock(pthreadpool_mutex_t* mutex) {
  mtx_lock(mutex);
}
static inline void pthreadpool_mutex_unlock(pthreadpool_mutex_t* mutex) {
  mtx_unlock(mutex);
}
static inline void pthreadpool_cond_init(pthreadpool_cond_t* cond) {
  cnd_init(cond);
}
static inline void pthreadpool_cond_destroy(pthreadpool_cond_t* cond) {
  cnd_destroy(cond);
}
static inline void pthreadpool_cond_wait(pthreadpool_cond_t* cond,
                                         pthreadpool_mutex_t* mutex) {
  cnd_wait(cond, mutex);
}
static inline void pthreadpool_cond_signal(pthreadpool_cond_t* cond) {
  cnd_signal(cond);
}
static inline void pthreadpool_cond_broadcast(pthreadpool_cond_t* cond) {
  cnd_broadcast(cond);
}
static inline void pthreadpool_thread_create(
    pthreadpool_thread_t* thread, pthreadpool_thread_return_t(fun)(void*),
    void* arg) {
  thrd_create(thread, (thrd_start_t)fun, arg);
}
static inline void pthreadpool_thread_join(
    pthreadpool_thread_t thread, pthreadpool_thread_return_t* return_value) {
  thrd_join(thread, return_value);
}
#endif  // PTHREADPOOL_USE_PTHREADS

/* Align the atomic values on the size of a cache line to avoid false sharing,
 * i.e. two or more atomic variables sharing the same cache line will block
 * each other during atomic operations.
 */
typedef atomic_uint_fast32_t PTHREADPOOL_CACHELINE_ALIGNED
    pthreadpool_atomic_uint32_t;
typedef atomic_int_least32_t PTHREADPOOL_CACHELINE_ALIGNED
    pthreadpool_atomic_int32_t;
typedef atomic_size_t PTHREADPOOL_CACHELINE_ALIGNED pthreadpool_atomic_size_t;
typedef atomic_uintptr_t PTHREADPOOL_CACHELINE_ALIGNED
    pthreadpool_atomic_void_p;

static inline int32_t pthreadpool_load_relaxed_int32_t(
    pthreadpool_atomic_int32_t* address) {
  return atomic_load_explicit(address, memory_order_relaxed);
}

static inline uint32_t pthreadpool_load_relaxed_uint32_t(
    pthreadpool_atomic_uint32_t* address) {
  return atomic_load_explicit(address, memory_order_relaxed);
}

static inline size_t pthreadpool_load_relaxed_size_t(
    pthreadpool_atomic_size_t* address) {
  return atomic_load_explicit(address, memory_order_relaxed);
}

static inline void* pthreadpool_load_relaxed_void_p(
    pthreadpool_atomic_void_p* address) {
  return (void*)atomic_load_explicit(address, memory_order_relaxed);
}

static inline uint32_t pthreadpool_load_acquire_uint32_t(
    pthreadpool_atomic_uint32_t* address) {
  return atomic_load_explicit(address, memory_order_acquire);
}

static inline uint32_t pthreadpool_load_consume_uint32_t(
    pthreadpool_atomic_uint32_t* address) {
  return atomic_load_explicit(address, memory_order_consume);
}

static inline uint32_t pthreadpool_load_sequentially_consistent_uint32_t(
    pthreadpool_atomic_uint32_t* address) {
  return atomic_load_explicit(address, memory_order_seq_cst);
}

static inline int32_t pthreadpool_load_acquire_int32_t(
    pthreadpool_atomic_int32_t* address) {
  return atomic_load_explicit(address, memory_order_acquire);
}

static inline int32_t pthreadpool_load_consume_int32_t(
    pthreadpool_atomic_int32_t* address) {
  return atomic_load_explicit(address, memory_order_consume);
}

static inline uint32_t pthreadpool_load_sequentially_consistent_int32_t(
    pthreadpool_atomic_int32_t* address) {
  return atomic_load_explicit(address, memory_order_seq_cst);
}

static inline size_t pthreadpool_load_acquire_size_t(
    pthreadpool_atomic_size_t* address) {
  return atomic_load_explicit(address, memory_order_acquire);
}

static inline void pthreadpool_store_relaxed_uint32_t(
    pthreadpool_atomic_uint32_t* address, uint32_t value) {
  atomic_store_explicit(address, value, memory_order_relaxed);
}

static inline void pthreadpool_store_relaxed_size_t(
    pthreadpool_atomic_size_t* address, size_t value) {
  atomic_store_explicit(address, value, memory_order_relaxed);
}

static inline void pthreadpool_store_relaxed_void_p(
    pthreadpool_atomic_void_p* address, void* value) {
  atomic_store_explicit(address, (uintptr_t)value, memory_order_relaxed);
}

static inline void pthreadpool_store_release_uint32_t(
    pthreadpool_atomic_uint32_t* address, uint32_t value) {
  atomic_store_explicit(address, value, memory_order_release);
}

static inline void pthreadpool_store_sequentially_consistent_uint32_t(
    pthreadpool_atomic_uint32_t* address, uint32_t value) {
  atomic_store_explicit(address, value, memory_order_seq_cst);
}

static inline void pthreadpool_store_release_int32_t(
    pthreadpool_atomic_int32_t* address, int32_t value) {
  atomic_store_explicit(address, value, memory_order_release);
}

static inline void pthreadpool_store_sequentially_consistent_int32_t(
    pthreadpool_atomic_int32_t* address, int32_t value) {
  atomic_store_explicit(address, value, memory_order_seq_cst);
}

static inline void pthreadpool_store_release_size_t(
    pthreadpool_atomic_size_t* address, size_t value) {
  atomic_store_explicit(address, value, memory_order_release);
}

static inline size_t pthreadpool_decrement_fetch_relaxed_size_t(
    pthreadpool_atomic_size_t* address) {
  return atomic_fetch_sub_explicit(address, 1, memory_order_relaxed) - 1;
}

static inline size_t pthreadpool_decrement_n_fetch_relaxed_size_t(
    pthreadpool_atomic_size_t* address, size_t n) {
  return atomic_fetch_sub_explicit(address, n, memory_order_relaxed) - n;
}

static inline size_t pthreadpool_fetch_decrement_n_relaxed_size_t(
    pthreadpool_atomic_size_t* address, size_t n) {
  return atomic_fetch_sub_explicit(address, n, memory_order_relaxed);
}

static inline size_t pthreadpool_decrement_fetch_release_size_t(
    pthreadpool_atomic_size_t* address) {
  return atomic_fetch_sub_explicit(address, 1, memory_order_release) - 1;
}

static inline size_t pthreadpool_decrement_fetch_acquire_release_size_t(
    pthreadpool_atomic_size_t* address) {
  return atomic_fetch_sub_explicit(address, 1, memory_order_acq_rel) - 1;
}

static inline uint32_t pthreadpool_decrement_fetch_acquire_release_uint32_t(
    pthreadpool_atomic_uint32_t* address) {
  return atomic_fetch_sub_explicit(address, 1, memory_order_acq_rel) - 1;
}

static inline bool pthreadpool_try_decrement_relaxed_size_t(
    pthreadpool_atomic_size_t* value) {
  size_t actual_value = atomic_load_explicit(value, memory_order_acquire);
  while (actual_value != 0) {
    if (atomic_compare_exchange_strong_explicit(
            value, &actual_value, actual_value - 1, memory_order_relaxed,
            memory_order_relaxed)) {
      return true;
    }
  }
  return false;
}

static inline size_t pthreadpool_fetch_add_relaxed_size_t(
    pthreadpool_atomic_size_t* address, size_t value) {
  return atomic_fetch_add_explicit(address, value, memory_order_relaxed);
}

static inline uint32_t pthreadpool_fetch_add_acquire_release_uint32_t(
    pthreadpool_atomic_uint32_t* address, uint32_t value) {
  return atomic_fetch_add_explicit(address, value, memory_order_acq_rel);
}

static inline int32_t pthreadpool_fetch_add_acquire_release_int32_t(
    pthreadpool_atomic_int32_t* address, uint32_t value) {
  return atomic_fetch_add_explicit(address, value, memory_order_acq_rel);
}

static inline int32_t pthreadpool_fetch_add_sequentially_consistent_int32_t(
    pthreadpool_atomic_int32_t* address, uint32_t value) {
  return atomic_fetch_add_explicit(address, value, memory_order_seq_cst);
}

static inline uint32_t pthreadpool_exchange_acquire_uint32_t(
    pthreadpool_atomic_uint32_t* address, uint32_t value) {
  return atomic_exchange_explicit(address, value, memory_order_acquire);
}

static inline int32_t pthreadpool_exchange_release_int32_t(
    pthreadpool_atomic_int32_t* address, uint32_t value) {
  return atomic_exchange_explicit(address, value, memory_order_release);
}

static inline uint32_t pthreadpool_exchange_acquire_release_uint32_t(
    pthreadpool_atomic_uint32_t* address, uint32_t value) {
  return atomic_exchange_explicit(address, value, memory_order_acq_rel);
}

static inline uint32_t pthreadpool_exchange_sequentially_consistent_uint32_t(
    pthreadpool_atomic_uint32_t* address, uint32_t value) {
  return atomic_exchange_explicit(address, value, memory_order_seq_cst);
}

static inline bool pthreadpool_compare_exchange_acquire_release_int32_t(
    pthreadpool_atomic_int32_t* address, int_least32_t* expected_value,
    int32_t new_value) {
  return atomic_compare_exchange_strong_explicit(address, expected_value,
                                               new_value, memory_order_acq_rel,
                                               memory_order_acquire);
}

static inline bool pthreadpool_compare_exchange_sequentially_consistent_int32_t(
    pthreadpool_atomic_int32_t* address, int_least32_t* expected_value,
    int32_t new_value) {
  return atomic_compare_exchange_strong_explicit(address, expected_value,
                                               new_value, memory_order_seq_cst,
                                               memory_order_seq_cst);
}

static inline void pthreadpool_fence_acquire(void) {
  atomic_thread_fence(memory_order_acquire);
}

static inline void pthreadpool_fence_release(void) {
  atomic_thread_fence(memory_order_release);
}

static inline void pthreadpool_yield(uint32_t step) {
  if (step < PTHREADPOOL_SPIN_PAUSE_ITERATIONS) {
#if defined(__ARM_ACLE) || \
    defined(_MSC_VER) &&   \
        (defined(_M_ARM) || defined(_M_ARM64) || defined(_M_ARM64EC))
    __yield();
#elif defined(__GNUC__) &&                                      \
    (defined(__ARM_ARCH) && (__ARM_ARCH >= 7) ||                \
     (defined(__ARM_ARCH_6K__) || defined(__ARM_ARCH_6KZ__)) && \
         !defined(__thumb__))
    __asm__ __volatile__("yield");
#elif defined(__i386__) || defined(__i686__) || defined(__x86_64__) || \
    defined(_M_IX86) || defined(_M_X64)
    _mm_pause();
#else
    pthreadpool_fence_acquire();
#endif
  } else {
#ifdef _WIN32
    Sleep(0);
#else
    sched_yield();
#endif
  }
}

#endif  // __PTHREADPOOL_SRC_THREADPOOL_ATOMICS_H_
