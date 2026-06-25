// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2026 Rasmus Munk Larsen <rmlarsen@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

// Shared context for GPU solvers (GpuLLT, GpuLU, GpuQR, GpuSVD, etc.).
//
// Owns a CUDA stream, cuSOLVER handle, cuBLAS handle, scratch buffer,
// and info word. Each solver holds a GpuSolverContext by composition
// and delegates lifecycle/scratch management to it.

#ifndef EIGEN_GPU_SOLVER_CONTEXT_H
#define EIGEN_GPU_SOLVER_CONTEXT_H

// IWYU pragma: private
#include "./InternalHeaderCheck.h"

#include "./CuSolverSupport.h"
#include "./CuBlasSupport.h"
#include <vector>

namespace Eigen {
namespace gpu {
namespace internal {

struct GpuSolverContext {
  cudaStream_t stream_ = nullptr;
  cusolverDnHandle_t cusolver_ = nullptr;
  cublasHandle_t cublas_ = nullptr;
  CusolverParams params_;
  DeviceBuffer d_scratch_;
  size_t scratch_size_ = 0;
  std::vector<char> h_workspace_;
  ComputationInfo info_ = InvalidInput;
  PinnedHostBuffer pinned_info_{sizeof(int)};  // pinned host memory for async D2H of info word
  bool info_synced_ = true;

  int& info_word() { return *static_cast<int*>(pinned_info_.get()); }
  int info_word() const { return *static_cast<const int*>(pinned_info_.get()); }

  GpuSolverContext() {
    EIGEN_CUDA_RUNTIME_CHECK(cudaStreamCreate(&stream_));
    EIGEN_CUSOLVER_CHECK(cusolverDnCreate(&cusolver_));
    EIGEN_CUSOLVER_CHECK(cusolverDnSetStream(cusolver_, stream_));
    EIGEN_CUBLAS_CHECK(cublasCreate(&cublas_));
    EIGEN_CUBLAS_CHECK(cublasSetStream(cublas_, stream_));
    ensure_scratch(0);
  }

  ~GpuSolverContext() {
    // Ignore errors here: dtors are noexcept, and EIGEN_CU{BLAS,SOLVER,DA_RUNTIME}_CHECK
    // are eigen_assert-based — firing one from a noexcept dtor terminates the program.
    // The trailing cudaFree() of the device buffers (via DeviceBuffer::~DeviceBuffer) is
    // synchronous and waits for any in-flight kernel touching the buffer.
    if (cublas_) (void)cublasDestroy(cublas_);
    if (cusolver_) (void)cusolverDnDestroy(cusolver_);
    if (stream_) (void)cudaStreamDestroy(stream_);
  }

  GpuSolverContext(GpuSolverContext&& o) noexcept
      : stream_(o.stream_),
        cusolver_(o.cusolver_),
        cublas_(o.cublas_),
        params_(std::move(o.params_)),
        d_scratch_(std::move(o.d_scratch_)),
        scratch_size_(o.scratch_size_),
        h_workspace_(std::move(o.h_workspace_)),
        info_(o.info_),
        pinned_info_(std::move(o.pinned_info_)),
        info_synced_(o.info_synced_) {
    o.stream_ = nullptr;
    o.cusolver_ = nullptr;
    o.cublas_ = nullptr;
    o.scratch_size_ = 0;
    o.info_ = InvalidInput;
    o.info_synced_ = true;
  }

  GpuSolverContext& operator=(GpuSolverContext&& o) noexcept {
    if (this != &o) {
      // Drain pending work on the old stream before tearing it down so the
      // upcoming move of d_scratch_ doesn't free buffers that an in-flight
      // kernel is still touching. The asymmetric move-ctor doesn't need this —
      // it constructs onto uninitialized storage.
      if (stream_) EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
      if (cublas_) EIGEN_CUBLAS_CHECK(cublasDestroy(cublas_));
      if (cusolver_) EIGEN_CUSOLVER_CHECK(cusolverDnDestroy(cusolver_));
      if (stream_) EIGEN_CUDA_RUNTIME_CHECK(cudaStreamDestroy(stream_));
      stream_ = o.stream_;
      cusolver_ = o.cusolver_;
      cublas_ = o.cublas_;
      params_ = std::move(o.params_);
      d_scratch_ = std::move(o.d_scratch_);
      scratch_size_ = o.scratch_size_;
      h_workspace_ = std::move(o.h_workspace_);
      info_ = o.info_;
      pinned_info_ = std::move(o.pinned_info_);
      info_synced_ = o.info_synced_;
      o.stream_ = nullptr;
      o.cusolver_ = nullptr;
      o.cublas_ = nullptr;
      o.scratch_size_ = 0;
      o.info_ = InvalidInput;
      o.info_synced_ = true;
    }
    return *this;
  }

  GpuSolverContext(const GpuSolverContext&) = delete;
  GpuSolverContext& operator=(const GpuSolverContext&) = delete;

  // Scratch layout: [ workspace (aligned) | info_word (sizeof(int)) ].
  // Workspace size is rounded up to 16 bytes so the info word lands aligned.
  static constexpr size_t kInfoBytes = sizeof(int);
  static constexpr size_t kScratchAlign = 16;

  static size_t scratchBytesFor(size_t workspace_bytes) {
    workspace_bytes = (workspace_bytes + kScratchAlign - 1) & ~(kScratchAlign - 1);
    return workspace_bytes + kInfoBytes;
  }

  // Ensure d_scratch_ holds at least `workspace_bytes` of scratch plus the trailing
  // info word. Grows but never shrinks. Syncs the stream before reallocating to
  // avoid freeing memory that async kernels may still be using.
  void ensure_scratch(size_t workspace_bytes) {
    size_t needed = scratchBytesFor(workspace_bytes);
    if (needed > scratch_size_) {
      if (d_scratch_) EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
      d_scratch_ = DeviceBuffer(needed);
      scratch_size_ = needed;
    }
  }

  void* scratch_workspace() const { return d_scratch_.get(); }

  int* scratch_info() const {
    eigen_assert(d_scratch_ && scratch_size_ >= kInfoBytes);
    return reinterpret_cast<int*>(static_cast<char*>(d_scratch_.get()) + scratch_size_ - kInfoBytes);
  }

  // Mark a factorization as pending (info not yet available).
  void mark_pending() {
    info_synced_ = false;
    info_ = InvalidInput;
  }

  // Synchronize the stream and interpret the info word. No-op if already synced.
  void sync_info() {
    if (!info_synced_) {
      EIGEN_CUDA_RUNTIME_CHECK(cudaStreamSynchronize(stream_));
      info_ = (info_word() == 0) ? Success : NumericalIssue;
      info_synced_ = true;
    }
  }

  ComputationInfo info() {
    sync_info();
    return info_;
  }
};

}  // namespace internal
}  // namespace gpu
}  // namespace Eigen

#endif  // EIGEN_GPU_SOLVER_CONTEXT_H
