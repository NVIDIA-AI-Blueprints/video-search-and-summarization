// Copyright (c) 2017 Facebook Inc.
// Copyright (c) 2015-2017 Georgia Institute of Technology
// All rights reserved.
//
// Copyright 2019 Google LLC
//
// This source code is licensed under the BSD-style license found in the
// LICENSE file in the root directory of this source tree.

#ifndef __PTHREADPOOL_SRC_THREADPOOL_OBJECT_H_
#define __PTHREADPOOL_SRC_THREADPOOL_OBJECT_H_

/* Standard C headers */
#include <limits.h>
#include <stddef.h>
#include <stdint.h>

/* Internal headers */
#include "threadpool-atomics.h"
#include "threadpool-common.h"

/* POSIX headers */
#if PTHREADPOOL_USE_PTHREADS
#include <pthread.h>
#else
#include <threads.h>
#endif

/* Dependencies */
#include <fxdiv.h>

/* Library header */
#include <pthreadpool.h>

struct PTHREADPOOL_CACHELINE_ALIGNED thread_info {
  /**
   * Index of the first element in the work range.
   * Before processing a new element the owning worker thread increments this
   * value.
   */
  pthreadpool_atomic_size_t range_start;
  /**
   * Index of the element after the last element of the work range.
   * Before processing a new element the stealing worker thread decrements this
   * value.
   */
  pthreadpool_atomic_size_t range_end;
  /**
   * The number of elements in the work range.
   * Due to race conditions range_length <= range_end - range_start.
   * The owning worker thread must decrement this value before incrementing @a
   * range_start. The stealing worker thread must decrement this value before
   * decrementing @a range_end.
   */
  pthreadpool_atomic_size_t range_length;
  /**
   * Thread number in the 0..max_num_threads-1 range.
   */
  size_t thread_number;
  /**
   * Thread pool which owns the thread.
   */
  struct pthreadpool* threadpool;
  /**
   * The pthread object corresponding to the thread.
   */
  pthreadpool_thread_t thread_object;
  /**
   * Whether this thread is active or not.
   */
  pthreadpool_atomic_uint32_t is_active;
};

PTHREADPOOL_STATIC_ASSERT(sizeof(struct thread_info) %
                                  PTHREADPOOL_CACHELINE_SIZE ==
                              0,
                          "thread_info structure must occupy an integer number "
                          "of cache lines (64 bytes)");

struct pthreadpool_1d_with_uarch_params {
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_1d_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_1d_with_uarch function.
   */
  uint32_t max_uarch_index;
};

struct pthreadpool_1d_tile_1d_params {
  /**
   * Copy of the range argument passed to the pthreadpool_parallelize_1d_tile_1d
   * function.
   */
  size_t range;
  /**
   * Copy of the tile argument passed to the pthreadpool_parallelize_1d_tile_1d
   * function.
   */
  size_t tile;
};

struct pthreadpool_1d_tile_1d_dynamic_params {
  /**
   * Copy of the range argument passed to the
   * pthreadpool_parallelize_1d_tile_1d_dynamic function.
   */
  size_t range;
  /**
   * Copy of the tile argument passed to the
   * pthreadpool_parallelize_1d_tile_1d_dynamic function.
   */
  size_t tile;
};

struct pthreadpool_1d_tile_1d_dynamic_with_uarch_params {
  /**
   * Copy of the range argument passed to the
   * pthreadpool_1d_tile_1d_dynamic_with_uarch_params function.
   */
  size_t range;
  /**
   * Copy of the tile argument passed to the
   * pthreadpool_1d_tile_1d_dynamic_with_uarch_params function.
   */
  size_t tile;
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_1d_tile_1d_dynamic_with_uarch_params function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_1d_tile_1d_dynamic_with_uarch_params function.
   */
  uint32_t max_uarch_index;
};

struct pthreadpool_2d_params {
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_2d function.
   */
  struct fxdiv_divisor_size_t range_j;
};

struct pthreadpool_2d_tile_1d_params {
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_2d_tile_1d function.
   */
  size_t range_j;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_2d_tile_1d function.
   */
  size_t tile_j;
  /**
   * FXdiv divisor for the divide_round_up(range_j, tile_j) value.
   */
  struct fxdiv_divisor_size_t tile_range_j;
};

struct pthreadpool_2d_tile_1d_with_uarch_params {
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_with_uarch function.
   */
  uint32_t max_uarch_index;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_2d_tile_1d function.
   */
  size_t range_j;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_2d_tile_1d function.
   */
  size_t tile_j;
  /**
   * FXdiv divisor for the divide_round_up(range_j, tile_j) value.
   */
  struct fxdiv_divisor_size_t tile_range_j;
};

struct pthreadpool_2d_tile_1d_dynamic_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_dynamic function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_dynamic function.
   */
  size_t range_j;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_dynamic function.
   */
  size_t tile_j;
};

struct pthreadpool_2d_tile_1d_dynamic_with_uarch_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_dynamic_with_uarch function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_dynamic_with_uarch function.
   */
  size_t range_j;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_dynamic_with_uarch function.
   */
  size_t tile_j;
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_dynamic_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_2d_tile_1d_dynamic_with_uarch function.
   */
  uint32_t max_uarch_index;
};

struct pthreadpool_2d_tile_2d_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_2d_tile_2d function.
   */
  size_t range_i;
  /**
   * Copy of the tile_i argument passed to the
   * pthreadpool_parallelize_2d_tile_2d function.
   */
  size_t tile_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_2d_tile_2d function.
   */
  size_t range_j;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_2d_tile_2d function.
   */
  size_t tile_j;
  /**
   * FXdiv divisor for the divide_round_up(range_j, tile_j) value.
   */
  struct fxdiv_divisor_size_t tile_range_j;
};

struct pthreadpool_2d_tile_2d_with_uarch_params {
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_with_uarch function.
   */
  uint32_t max_uarch_index;
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_with_uarch function.
   */
  size_t range_i;
  /**
   * Copy of the tile_i argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_with_uarch function.
   */
  size_t tile_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_with_uarch function.
   */
  size_t range_j;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_with_uarch function.
   */
  size_t tile_j;
  /**
   * FXdiv divisor for the divide_round_up(range_j, tile_j) value.
   */
  struct fxdiv_divisor_size_t tile_range_j;
};

struct pthreadpool_2d_tile_2d_dynamic_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic function.
   */
  size_t range_j;
  /**
   * Copy of the tile_i argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic function.
   */
  size_t tile_i;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic function.
   */
  size_t tile_j;
};

struct pthreadpool_2d_tile_2d_dynamic_with_uarch_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic function.
   */
  size_t range_j;
  /**
   * Copy of the tile_i argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic function.
   */
  size_t tile_i;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic function.
   */
  size_t tile_j;
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_2d_tile_2d_dynamic_with_uarch function.
   */
  uint32_t max_uarch_index;
};

struct pthreadpool_3d_params {
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_3d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k argument passed to the
   * pthreadpool_parallelize_3d function.
   */
  struct fxdiv_divisor_size_t range_k;
};

struct pthreadpool_3d_tile_1d_params {
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_3d_tile_1d function.
   */
  size_t range_k;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_3d_tile_1d function.
   */
  size_t tile_k;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_3d_tile_1d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the divide_round_up(range_k, tile_k) value.
   */
  struct fxdiv_divisor_size_t tile_range_k;
};

struct pthreadpool_3d_tile_1d_with_uarch_params {
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  uint32_t max_uarch_index;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  size_t range_k;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  size_t tile_k;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the divide_round_up(range_k, tile_k) value.
   */
  struct fxdiv_divisor_size_t tile_range_k;
};

struct pthreadpool_3d_tile_1d_dynamic_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_dynamic function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_dynamic function.
   */
  size_t range_j;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_dynamic function.
   */
  size_t range_k;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_dynamic function.
   */
  size_t tile_k;
};

struct pthreadpool_3d_tile_1d_dynamic_with_uarch_params {
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  uint32_t max_uarch_index;
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  size_t range_j;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  size_t range_k;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_3d_tile_1d_with_uarch function.
   */
  size_t tile_k;
};

struct pthreadpool_3d_tile_2d_params {
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_3d_tile_2d function.
   */
  size_t range_j;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_3d_tile_2d function.
   */
  size_t tile_j;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_3d_tile_2d function.
   */
  size_t range_k;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_3d_tile_2d function.
   */
  size_t tile_k;
  /**
   * FXdiv divisor for the divide_round_up(range_j, tile_j) value.
   */
  struct fxdiv_divisor_size_t tile_range_j;
  /**
   * FXdiv divisor for the divide_round_up(range_k, tile_k) value.
   */
  struct fxdiv_divisor_size_t tile_range_k;
};

struct pthreadpool_3d_tile_2d_with_uarch_params {
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_with_uarch function.
   */
  uint32_t max_uarch_index;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_with_uarch function.
   */
  size_t range_j;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_with_uarch function.
   */
  size_t tile_j;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_with_uarch function.
   */
  size_t range_k;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_with_uarch function.
   */
  size_t tile_k;
  /**
   * FXdiv divisor for the divide_round_up(range_j, tile_j) value.
   */
  struct fxdiv_divisor_size_t tile_range_j;
  /**
   * FXdiv divisor for the divide_round_up(range_k, tile_k) value.
   */
  struct fxdiv_divisor_size_t tile_range_k;
};

struct pthreadpool_3d_tile_2d_dynamic_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t range_j;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t range_k;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t tile_j;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t tile_k;
};

struct pthreadpool_3d_tile_2d_dynamic_with_uarch_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t range_j;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t range_k;
  /**
   * Copy of the tile_j argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t tile_j;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic function.
   */
  size_t tile_k;
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_3d_tile_2d_dynamic_with_uarch function.
   */
  uint32_t max_uarch_index;
};

struct pthreadpool_4d_params {
  /**
   * Copy of the range_k argument passed to the pthreadpool_parallelize_4d
   * function.
   */
  size_t range_k;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_4d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k * range_l value.
   */
  struct fxdiv_divisor_size_t range_kl;
  /**
   * FXdiv divisor for the range_l argument passed to the
   * pthreadpool_parallelize_4d function.
   */
  struct fxdiv_divisor_size_t range_l;
};

struct pthreadpool_4d_tile_1d_params {
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_4d_tile_1d function.
   */
  size_t range_k;
  /**
   * Copy of the range_l argument passed to the
   * pthreadpool_parallelize_4d_tile_1d function.
   */
  size_t range_l;
  /**
   * Copy of the tile_l argument passed to the
   * pthreadpool_parallelize_4d_tile_1d function.
   */
  size_t tile_l;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_4d_tile_1d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k * divide_round_up(range_l, tile_l) value.
   */
  struct fxdiv_divisor_size_t tile_range_kl;
  /**
   * FXdiv divisor for the divide_round_up(range_l, tile_l) value.
   */
  struct fxdiv_divisor_size_t tile_range_l;
};

struct pthreadpool_4d_tile_2d_params {
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_4d_tile_2d function.
   */
  size_t range_k;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_4d_tile_2d function.
   */
  size_t tile_k;
  /**
   * Copy of the range_l argument passed to the
   * pthreadpool_parallelize_4d_tile_2d function.
   */
  size_t range_l;
  /**
   * Copy of the tile_l argument passed to the
   * pthreadpool_parallelize_4d_tile_2d function.
   */
  size_t tile_l;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_4d_tile_2d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the divide_round_up(range_k, tile_k) *
   * divide_round_up(range_l, tile_l) value.
   */
  struct fxdiv_divisor_size_t tile_range_kl;
  /**
   * FXdiv divisor for the divide_round_up(range_l, tile_l) value.
   */
  struct fxdiv_divisor_size_t tile_range_l;
};

struct pthreadpool_4d_tile_2d_with_uarch_params {
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_with_uarch function.
   */
  uint32_t max_uarch_index;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_with_uarch function.
   */
  size_t range_k;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_with_uarch function.
   */
  size_t tile_k;
  /**
   * Copy of the range_l argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_with_uarch function.
   */
  size_t range_l;
  /**
   * Copy of the tile_l argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_with_uarch function.
   */
  size_t tile_l;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_with_uarch function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the divide_round_up(range_k, tile_k) *
   * divide_round_up(range_l, tile_l) value.
   */
  struct fxdiv_divisor_size_t tile_range_kl;
  /**
   * FXdiv divisor for the divide_round_up(range_l, tile_l) value.
   */
  struct fxdiv_divisor_size_t tile_range_l;
};

struct pthreadpool_4d_tile_2d_dynamic_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic function.
   */
  size_t range_j;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic function.
   */
  size_t range_k;
  /**
   * Copy of the range_l argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic function.
   */
  size_t range_l;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic function.
   */
  size_t tile_k;
  /**
   * Copy of the tile_l argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic function.
   */
  size_t tile_l;
};

struct pthreadpool_4d_tile_2d_dynamic_with_uarch_params {
  /**
   * Copy of the range_i argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch function.
   */
  size_t range_i;
  /**
   * Copy of the range_j argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch function.
   */
  size_t range_j;
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch function.
   */
  size_t range_k;
  /**
   * Copy of the range_l argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch function.
   */
  size_t range_l;
  /**
   * Copy of the tile_k argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch function.
   */
  size_t tile_k;
  /**
   * Copy of the tile_l argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch function.
   */
  size_t tile_l;
  /**
   * Copy of the default_uarch_index argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch function.
   */
  uint32_t default_uarch_index;
  /**
   * Copy of the max_uarch_index argument passed to the
   * pthreadpool_parallelize_4d_tile_2d_dynamic_with_uarch function.
   */
  uint32_t max_uarch_index;
};

struct pthreadpool_5d_params {
  /**
   * Copy of the range_l argument passed to the pthreadpool_parallelize_5d
   * function.
   */
  size_t range_l;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_5d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k argument passed to the
   * pthreadpool_parallelize_5d function.
   */
  struct fxdiv_divisor_size_t range_k;
  /**
   * FXdiv divisor for the range_l * range_m value.
   */
  struct fxdiv_divisor_size_t range_lm;
  /**
   * FXdiv divisor for the range_m argument passed to the
   * pthreadpool_parallelize_5d function.
   */
  struct fxdiv_divisor_size_t range_m;
};

struct pthreadpool_5d_tile_1d_params {
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_5d_tile_1d function.
   */
  size_t range_k;
  /**
   * Copy of the range_m argument passed to the
   * pthreadpool_parallelize_5d_tile_1d function.
   */
  size_t range_m;
  /**
   * Copy of the tile_m argument passed to the
   * pthreadpool_parallelize_5d_tile_1d function.
   */
  size_t tile_m;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_5d_tile_1d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k * range_l value.
   */
  struct fxdiv_divisor_size_t range_kl;
  /**
   * FXdiv divisor for the range_l argument passed to the
   * pthreadpool_parallelize_5d_tile_1d function.
   */
  struct fxdiv_divisor_size_t range_l;
  /**
   * FXdiv divisor for the divide_round_up(range_m, tile_m) value.
   */
  struct fxdiv_divisor_size_t tile_range_m;
};

struct pthreadpool_5d_tile_2d_params {
  /**
   * Copy of the range_l argument passed to the
   * pthreadpool_parallelize_5d_tile_2d function.
   */
  size_t range_l;
  /**
   * Copy of the tile_l argument passed to the
   * pthreadpool_parallelize_5d_tile_2d function.
   */
  size_t tile_l;
  /**
   * Copy of the range_m argument passed to the
   * pthreadpool_parallelize_5d_tile_2d function.
   */
  size_t range_m;
  /**
   * Copy of the tile_m argument passed to the
   * pthreadpool_parallelize_5d_tile_2d function.
   */
  size_t tile_m;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_5d_tile_2d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k argument passed to the
   * pthreadpool_parallelize_5d_tile_2d function.
   */
  struct fxdiv_divisor_size_t range_k;
  /**
   * FXdiv divisor for the divide_round_up(range_l, tile_l) *
   * divide_round_up(range_m, tile_m) value.
   */
  struct fxdiv_divisor_size_t tile_range_lm;
  /**
   * FXdiv divisor for the divide_round_up(range_m, tile_m) value.
   */
  struct fxdiv_divisor_size_t tile_range_m;
};

struct pthreadpool_6d_params {
  /**
   * Copy of the range_l argument passed to the pthreadpool_parallelize_6d
   * function.
   */
  size_t range_l;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_6d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k argument passed to the
   * pthreadpool_parallelize_6d function.
   */
  struct fxdiv_divisor_size_t range_k;
  /**
   * FXdiv divisor for the range_l * range_m * range_n value.
   */
  struct fxdiv_divisor_size_t range_lmn;
  /**
   * FXdiv divisor for the range_m argument passed to the
   * pthreadpool_parallelize_6d function.
   */
  struct fxdiv_divisor_size_t range_m;
  /**
   * FXdiv divisor for the range_n argument passed to the
   * pthreadpool_parallelize_6d function.
   */
  struct fxdiv_divisor_size_t range_n;
};

struct pthreadpool_6d_tile_1d_params {
  /**
   * Copy of the range_l argument passed to the
   * pthreadpool_parallelize_6d_tile_1d function.
   */
  size_t range_l;
  /**
   * Copy of the range_n argument passed to the
   * pthreadpool_parallelize_6d_tile_1d function.
   */
  size_t range_n;
  /**
   * Copy of the tile_n argument passed to the
   * pthreadpool_parallelize_6d_tile_1d function.
   */
  size_t tile_n;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_6d_tile_1d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k argument passed to the
   * pthreadpool_parallelize_6d_tile_1d function.
   */
  struct fxdiv_divisor_size_t range_k;
  /**
   * FXdiv divisor for the range_l * range_m * divide_round_up(range_n, tile_n)
   * value.
   */
  struct fxdiv_divisor_size_t tile_range_lmn;
  /**
   * FXdiv divisor for the range_m argument passed to the
   * pthreadpool_parallelize_6d_tile_1d function.
   */
  struct fxdiv_divisor_size_t range_m;
  /**
   * FXdiv divisor for the divide_round_up(range_n, tile_n) value.
   */
  struct fxdiv_divisor_size_t tile_range_n;
};

struct pthreadpool_6d_tile_2d_params {
  /**
   * Copy of the range_k argument passed to the
   * pthreadpool_parallelize_6d_tile_2d function.
   */
  size_t range_k;
  /**
   * Copy of the range_m argument passed to the
   * pthreadpool_parallelize_6d_tile_2d function.
   */
  size_t range_m;
  /**
   * Copy of the tile_m argument passed to the
   * pthreadpool_parallelize_6d_tile_2d function.
   */
  size_t tile_m;
  /**
   * Copy of the range_n argument passed to the
   * pthreadpool_parallelize_6d_tile_2d function.
   */
  size_t range_n;
  /**
   * Copy of the tile_n argument passed to the
   * pthreadpool_parallelize_6d_tile_2d function.
   */
  size_t tile_n;
  /**
   * FXdiv divisor for the range_j argument passed to the
   * pthreadpool_parallelize_6d_tile_2d function.
   */
  struct fxdiv_divisor_size_t range_j;
  /**
   * FXdiv divisor for the range_k * range_l value.
   */
  struct fxdiv_divisor_size_t range_kl;
  /**
   * FXdiv divisor for the range_l argument passed to the
   * pthreadpool_parallelize_6d_tile_2d function.
   */
  struct fxdiv_divisor_size_t range_l;
  /**
   * FXdiv divisor for the divide_round_up(range_m, tile_m) *
   * divide_round_up(range_n, tile_n) value.
   */
  struct fxdiv_divisor_size_t tile_range_mn;
  /**
   * FXdiv divisor for the divide_round_up(range_n, tile_n) value.
   */
  struct fxdiv_divisor_size_t tile_range_n;
};

union pthreadpool_params {
  struct pthreadpool_1d_with_uarch_params parallelize_1d_with_uarch;
  struct pthreadpool_1d_tile_1d_params parallelize_1d_tile_1d;
  struct pthreadpool_1d_tile_1d_dynamic_params parallelize_1d_tile_1d_dynamic;
  struct pthreadpool_1d_tile_1d_dynamic_with_uarch_params
      parallelize_1d_tile_1d_dynamic_with_uarch;
  struct pthreadpool_2d_params parallelize_2d;
  struct pthreadpool_2d_tile_1d_params parallelize_2d_tile_1d;
  struct pthreadpool_2d_tile_1d_with_uarch_params
      parallelize_2d_tile_1d_with_uarch;
  struct pthreadpool_2d_tile_1d_dynamic_params parallelize_2d_tile_1d_dynamic;
  struct pthreadpool_2d_tile_1d_dynamic_with_uarch_params
      parallelize_2d_tile_1d_dynamic_with_uarch;
  struct pthreadpool_2d_tile_2d_params parallelize_2d_tile_2d;
  struct pthreadpool_2d_tile_2d_with_uarch_params
      parallelize_2d_tile_2d_with_uarch;
  struct pthreadpool_2d_tile_2d_dynamic_params parallelize_2d_tile_2d_dynamic;
  struct pthreadpool_2d_tile_2d_dynamic_with_uarch_params
      parallelize_2d_tile_2d_dynamic_with_uarch;
  struct pthreadpool_3d_params parallelize_3d;
  struct pthreadpool_3d_tile_1d_params parallelize_3d_tile_1d;
  struct pthreadpool_3d_tile_1d_with_uarch_params
      parallelize_3d_tile_1d_with_uarch;
  struct pthreadpool_3d_tile_1d_dynamic_params parallelize_3d_tile_1d_dynamic;
  struct pthreadpool_3d_tile_1d_dynamic_with_uarch_params
      parallelize_3d_tile_1d_dynamic_with_uarch;
  struct pthreadpool_3d_tile_2d_params parallelize_3d_tile_2d;
  struct pthreadpool_3d_tile_2d_with_uarch_params
      parallelize_3d_tile_2d_with_uarch;
  struct pthreadpool_3d_tile_2d_dynamic_params parallelize_3d_tile_2d_dynamic;
  struct pthreadpool_3d_tile_2d_dynamic_with_uarch_params
      parallelize_3d_tile_2d_dynamic_with_uarch;
  struct pthreadpool_4d_params parallelize_4d;
  struct pthreadpool_4d_tile_1d_params parallelize_4d_tile_1d;
  struct pthreadpool_4d_tile_2d_params parallelize_4d_tile_2d;
  struct pthreadpool_4d_tile_2d_with_uarch_params
      parallelize_4d_tile_2d_with_uarch;
  struct pthreadpool_4d_tile_2d_dynamic_params parallelize_4d_tile_2d_dynamic;
  struct pthreadpool_4d_tile_2d_dynamic_with_uarch_params
      parallelize_4d_tile_2d_dynamic_with_uarch;
  struct pthreadpool_5d_params parallelize_5d;
  struct pthreadpool_5d_tile_1d_params parallelize_5d_tile_1d;
  struct pthreadpool_5d_tile_2d_params parallelize_5d_tile_2d;
  struct pthreadpool_6d_params parallelize_6d;
  struct pthreadpool_6d_tile_1d_params parallelize_6d_tile_1d;
  struct pthreadpool_6d_tile_2d_params parallelize_6d_tile_2d;
};

#define PTHREADPOOL_NUM_ACTIVE_THREADS_DONE INT_MAX

struct PTHREADPOOL_CACHELINE_ALIGNED pthreadpool {
  /**
   * The number of threads that are processing an operation.
   */
  pthreadpool_atomic_size_t active_threads;

#if PTHREADPOOL_USE_FUTEX
  /**
   * Indicates if there are active threads.
   * Only two values are possible:
   * - has_active_threads == 0 if active_threads == 0
   * - has_active_threads == 1 if active_threads != 0
   */
  pthreadpool_atomic_uint32_t has_active_threads;
#endif
  /**
   * The entry point function to call for each thread in the thread pool for
   * parallelization tasks.
   */
  pthreadpool_atomic_void_p thread_function;

  /**
   * The function to call for each item.
   */
  pthreadpool_atomic_void_p task;

  /**
   * The first argument to the item processing function.
   */
  pthreadpool_atomic_void_p argument;

  /**
   * Additional parallelization parameters.
   * These parameters are specific for each thread_function.
   */
  union pthreadpool_params params;

  /**
   * Copy of the flags passed to a parallelization function.
   */
  pthreadpool_atomic_uint32_t flags;

  /**
   * Serializes concurrent calls to @a pthreadpool_parallelize_* from different
   * threads.
   */
  pthreadpool_mutex_t execution_mutex;

#if PTHREADPOOL_USE_CONDVAR
  /**
   * Guards access to the @a active_threads variable.
   */
  pthreadpool_mutex_t completion_mutex;

  /**
   * Condition variable to wait until all threads complete an operation (until
   * @a active_threads is zero).
   */
  pthreadpool_cond_t completion_condvar;

  /**
   * Guards access to the @a num_active_threads_condvar variable.
   */
  pthreadpool_mutex_t num_active_threads_mutex;

  /**
   * Condition variable to wait on a change of @a num_active_threads.
   */
  pthreadpool_cond_t num_active_threads_condvar;
#endif

#if PTHREADPOOL_USE_FUTEX
  /**
   * Number of threads currently futex-waiting on @a num_active_threads.
   */
  pthreadpool_atomic_uint32_t num_waiting_threads;
#endif  // PTHREADPOOL_USE_FUTEX

  /**
   * The ID of the job currently being run.
   */
  uint32_t job_id;

  /**
   * The current number of threads running on the current job.
   *
   * This value is used to make sure that no more than @a max_active_threads are
   * working on a parallel job at the same time, as well as to track when all
   * threads working on a job have completed.
   */
  pthreadpool_atomic_int32_t num_active_threads;

  /**
   * Boolean value that indicates whether @a num_active_threads has become zero.
   */
  pthreadpool_atomic_uint32_t work_is_done;

  /**
   * Pointer to a `pthreadpool_executor` that will handle the creation of
   * parallel threas for this threadpool.
   */
  struct pthreadpool_executor executor;

  /**
   * Pointer to a `pthreadpool_executor` context that will passed on to the @a
   * executor functions.
   */
  void* executor_context;

  /**
   * The number of threads that are currently running. Threads using
   * this datastructure should atomically decrement this variable as the last
   * thing they do before releasing it.
   */
  pthreadpool_atomic_uint32_t num_recruited_threads;

  /**
   * The maximum number of threads, i.e. the thread_info structs that were
   * originally allocated. This value never changes after @a pthreadpool_create.
   */
  size_t max_num_threads;

  /**
   * The current number of active threads in the thread pool.
   */
  pthreadpool_atomic_size_t threads_count;

  /**
   * Thread information structures that immediately follow this structure.
   */
  struct thread_info threads[];
};

PTHREADPOOL_STATIC_ASSERT(sizeof(struct pthreadpool) %
                                  PTHREADPOOL_CACHELINE_SIZE ==
                              0,
                          "pthreadpool structure must occupy an integer number "
                          "of cache lines (64 bytes)");

PTHREADPOOL_INTERNAL struct pthreadpool* pthreadpool_allocate(
    size_t threads_count);

PTHREADPOOL_INTERNAL void pthreadpool_deallocate(
    struct pthreadpool* threadpool);

typedef void (*thread_function_t)(struct pthreadpool* threadpool,
                                  struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_parallelize(
    struct pthreadpool* threadpool, thread_function_t thread_function,
    const void* params, size_t params_size, void* task, void* context,
    size_t linear_range, uint32_t flags);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_1d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_1d_with_thread_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_1d_with_uarch_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_1d_tile_1d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_2d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_2d_with_thread_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_2d_tile_1d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_2d_tile_1d_with_uarch_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_2d_tile_1d_with_uarch_with_thread_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_2d_tile_2d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_2d_tile_2d_with_uarch_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_3d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_3d_tile_1d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_3d_tile_1d_with_thread_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_3d_tile_1d_with_uarch_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_3d_tile_1d_with_uarch_with_thread_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_3d_tile_2d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_3d_tile_2d_with_uarch_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_4d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_4d_tile_1d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_4d_tile_2d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void
pthreadpool_thread_parallelize_4d_tile_2d_with_uarch_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_5d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_5d_tile_1d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_5d_tile_2d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_6d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_6d_tile_1d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

PTHREADPOOL_INTERNAL void pthreadpool_thread_parallelize_6d_tile_2d_fastpath(
    struct pthreadpool* threadpool, struct thread_info* thread);

#endif  // __PTHREADPOOL_SRC_THREADPOOL_OBJECT_H_
